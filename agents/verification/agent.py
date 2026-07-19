from __future__ import annotations

import base64
import logging
import operator
from pathlib import Path
from typing import Annotated, TypedDict

import httpx
from google import genai
from google.genai import errors as genai_errors
from google.genai import types
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.errors import GraphRecursionError
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, Send, interrupt
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from agents.verification import reference_images
from agents.verification.prompts import SYSTEM_PROMPT, TOOLS
from config import settings
from core.domain import VerificationOutput
from core.exceptions import NeedsHumanReviewError, VerificationError
from core.media_types import SUPPORTED_IMAGE_EXTENSIONS
from db.repositories.order_repository import OrderRepository
from db.repositories.trust_repository import TrustRepository
from db.repositories.verification_repository import VerificationRepository

logger = logging.getLogger(__name__)

_RETRYABLE_API_ERRORS = (genai_errors.APIError, httpx.HTTPError)

_VALID_VERDICTS = frozenset({"OK", "DEFECT", "SPEC_AMBIGUITY"})
_VALID_FAULT_PARTIES = frozenset({"workshop", "buyer", "none"})
_REQUIRED_TOOLS_BEFORE_VERDICT = frozenset({"get_order_spec", "get_workshop_history"})

_LANGUAGE_NAMES: dict[str, str] = {"hi": "Hindi"}

_NUDGE_TEXT = (
    "Please continue — call get_order_spec, get_workshop_history, "
    "and then submit_verdict."
)
_STRICTER_RETRY_TEXT = (
    "You have the order spec and workshop history already. "
    "Return your verdict now by calling submit_verdict."
)


def _accumulate_or_reset(existing: list | None, new: list | None) -> list:
    if new is None:
        return []
    return (existing or []) + new


class _VerifyState(TypedDict, total=False):
    contents: Annotated[list[dict], operator.add]
    called_tools: set[str]
    iteration: int
    order_id: int
    workshop_id: int
    sublot_id: int
    finish_reason_is_stop: bool
    function_calls: list[dict]
    model_turn: dict
    tool_results: Annotated[list[dict], _accumulate_or_reset]
    result: VerificationOutput


class _ToolCallPayload(TypedDict):
    index: int
    function_call: dict
    order_id: int
    workshop_id: int
    missing_before_verdict: frozenset


class VerificationAgent:
    def __init__(
        self,
        client: genai.Client,
        order_repo: OrderRepository,
        trust_repo: TrustRepository,
        verification_repo: VerificationRepository,
        checkpointer: BaseCheckpointSaver,
    ) -> None:
        self._client = client
        self._orders = order_repo
        self._trust = trust_repo
        self._verifications = verification_repo
        self._checkpointer = checkpointer
        self._graph = self._build_graph()
        self._recursion_limit = 6 * settings.verification_max_loop_iterations + 5

    def _build_graph(self):
        graph = StateGraph(_VerifyState)
        graph.add_node("model", self._node_model)
        graph.add_node("submit", self._node_submit)
        graph.add_node("nudge", self._node_nudge)
        graph.add_node("execute_tool", self._node_execute_tool, input_schema=_ToolCallPayload)
        graph.add_node("collect_tool_results", self._node_collect_tool_results)
        graph.add_node("stricter_retry", self._node_stricter_retry)
        graph.add_node("human_review", self._node_human_review)

        graph.add_edge(START, "model")
        graph.add_conditional_edges(
            "model",
            self._route_after_model,
            {"submit": "submit", "nudge": "nudge", "stricter_retry": "stricter_retry"},
        )
        graph.add_edge("submit", END)
        graph.add_conditional_edges(
            "nudge", self._route_after_turn, {"model": "model", "stricter_retry": "stricter_retry"}
        )
        graph.add_edge("execute_tool", "collect_tool_results")
        graph.add_conditional_edges(
            "collect_tool_results",
            self._route_after_turn,
            {"model": "model", "stricter_retry": "stricter_retry"},
        )
        graph.add_conditional_edges(
            "stricter_retry", self._route_stricter_retry_result, {"end": END, "human_review": "human_review"}
        )
        graph.add_conditional_edges(
            "human_review", self._route_after_human_review, {"end": END, "model": "model"}
        )
        return graph.compile(checkpointer=self._checkpointer)

    @staticmethod
    def _thread_id(sublot_id: int) -> str:
        return f"verification-sublot-{sublot_id}"

    async def verify(
        self,
        photo_path: str,
        order_id: int,
        workshop_id: int,
        sublot_id: int,
        buyer_note: str | None = None,
    ) -> VerificationOutput:
        try:
            image_data = self._encode_image(photo_path)
        except OSError as exc:
            raise VerificationError(
                f"Sublot {sublot_id}: cannot read photo at {photo_path!r}: {exc}"
            ) from exc

        context_lines = [
            f"Sub-lot ID: {sublot_id}",
            f"Order ID: {order_id}",
            f"Workshop ID: {workshop_id}",
        ]
        if buyer_note:
            context_lines.append(f"Buyer-reported issue: {buyer_note}")
        context_lines.append(
            "\nInspect the photo above. Call get_order_spec and "
            "get_workshop_history to gather context, then call submit_verdict."
        )

        contents: list[dict] = [
            {
                "role": "user",
                "parts": [
                    {
                        "inline_data": {
                            "mime_type": self._media_type(photo_path),
                            "data": image_data,
                        },
                    },
                    {"text": "\n".join(context_lines)},
                ],
            }
        ]

        initial_state: _VerifyState = {
            "contents": contents,
            "called_tools": set(),
            "iteration": 0,
            "order_id": order_id,
            "workshop_id": workshop_id,
            "sublot_id": sublot_id,
        }
        final_state = await self._run_graph(initial_state, sublot_id=sublot_id)
        return self._extract_result_or_pause(final_state, sublot_id)

    async def is_resumable(self, sublot_id: int) -> bool:
        config = {"configurable": {"thread_id": self._thread_id(sublot_id)}}
        snapshot = await self._graph.aget_state(config)
        return any(task.interrupts for task in snapshot.tasks)

    async def resume_with_guidance(self, sublot_id: int, guidance: str) -> VerificationOutput:
        final_state = await self._run_graph(
            Command(resume={"mode": "guidance", "text": guidance}), sublot_id=sublot_id
        )
        return self._extract_result_or_pause(final_state, sublot_id)

    async def resume_with_verdict(
        self, sublot_id: int, verdict: VerificationOutput
    ) -> VerificationOutput:
        await self._run_graph(
            Command(
                resume={
                    "mode": "verdict",
                    "verdict": {
                        "verdict": verdict.verdict,
                        "fault_party": verdict.fault_party,
                        "confidence": verdict.confidence,
                        "explanation": verdict.explanation,
                    },
                }
            ),
            sublot_id=sublot_id,
        )
        return verdict

    async def _run_graph(self, input_: dict | Command, *, sublot_id: int) -> dict:
        config = {
            "configurable": {"thread_id": self._thread_id(sublot_id)},
            "recursion_limit": self._recursion_limit,
        }
        try:
            return await self._graph.ainvoke(input_, config=config)
        except GraphRecursionError as exc:
            raise NeedsHumanReviewError(
                f"Sublot {sublot_id}: LangGraph recursion limit "
                f"({self._recursion_limit}) hit before a verdict was "
                f"reached — this indicates the graph did not respect its "
                f"own iteration budget.",
                thread_id=None,
            ) from exc

    def _extract_result_or_pause(self, final_state: dict, sublot_id: int) -> VerificationOutput:
        if "__interrupt__" in final_state:
            payload = final_state["__interrupt__"][0].value
            missing = ", ".join(payload.get("missing_tools", [])) or "none — no verdict call at all"
            raise NeedsHumanReviewError(
                f"Sublot {sublot_id}: agent could not reach a grounded verdict "
                f"on its own (missing: {missing}) and is paused for human review "
                f"— resumable via resume_with_guidance/resume_with_verdict.",
                thread_id=self._thread_id(sublot_id),
            )
        return final_state["result"]

    async def _node_model(self, state: _VerifyState) -> dict:
        try:
            response = await self._call_api(state["contents"])
        except _RETRYABLE_API_ERRORS as exc:
            raise VerificationError(
                f"Sublot {state['sublot_id']}: Gemini API failed after "
                f"{settings.verification_retry_attempts} retries: {exc}"
            ) from exc
        candidate = response.candidates[0]
        parts = candidate.content.parts or []
        function_calls: list[dict] = []
        model_turn_parts: list[dict] = []
        for part in parts:
            if part.function_call:
                fc = {"name": part.function_call.name, "args": dict(part.function_call.args or {})}
                function_calls.append(fc)
                part_dict: dict = {"function_call": fc}
                if part.thought_signature:
                    part_dict["thought_signature"] = base64.standard_b64encode(
                        part.thought_signature
                    ).decode()
                model_turn_parts.append(part_dict)
            elif part.text:
                model_turn_parts.append({"text": part.text})
        return {
            "finish_reason_is_stop": candidate.finish_reason == types.FinishReason.STOP,
            "function_calls": function_calls,
            "model_turn": {"role": "model", "parts": model_turn_parts},
            "iteration": state["iteration"] + 1,
        }

    @staticmethod
    def _route_after_model(state: _VerifyState):
        function_calls = state["function_calls"]
        called_tools = state["called_tools"]
        missing_before_verdict = _REQUIRED_TOOLS_BEFORE_VERDICT - called_tools

        for fc in function_calls:
            if fc["name"] == "submit_verdict" and not missing_before_verdict:
                return "submit"

        if not function_calls:
            if state["finish_reason_is_stop"]:
                return "stricter_retry"
            return "nudge"

        return [
            Send(
                "execute_tool",
                _ToolCallPayload(
                    index=i,
                    function_call=fc,
                    order_id=state["order_id"],
                    workshop_id=state["workshop_id"],
                    missing_before_verdict=missing_before_verdict,
                ),
            )
            for i, fc in enumerate(function_calls)
        ]

    async def _node_submit(self, state: _VerifyState) -> dict:
        for fc in state["function_calls"]:
            if fc["name"] == "submit_verdict":
                return {"result": self._parse_verdict(fc["args"])}
        raise VerificationError(
            f"Sublot {state['sublot_id']}: routed to submit without a qualifying "
            f"submit_verdict call — this indicates a routing bug"
        )

    async def _node_nudge(self, state: _VerifyState) -> dict:
        return {
            "contents": [
                state["model_turn"],
                {"role": "user", "parts": [{"text": _NUDGE_TEXT}]},
            ],
        }

    async def _node_execute_tool(self, payload: _ToolCallPayload) -> dict:
        fc = payload["function_call"]
        name = fc["name"]
        if name == "submit_verdict":
            result: dict = {
                "error": (
                    "Cannot submit a verdict yet — call these tools first: "
                    f"{', '.join(sorted(payload['missing_before_verdict']))}."
                ),
            }
            extra_parts: list[dict] = []
            called_name = None
        else:
            result, extra_parts = await self._execute_tool(
                name, payload["order_id"], payload["workshop_id"]
            )
            called_name = name if name in _REQUIRED_TOOLS_BEFORE_VERDICT else None
        return {
            "tool_results": [
                {
                    "index": payload["index"],
                    "response_part": {
                        "function_response": {"name": name, "response": {"result": result}},
                    },
                    "extra_parts": extra_parts,
                    "called_name": called_name,
                }
            ],
        }

    async def _node_collect_tool_results(self, state: _VerifyState) -> dict:
        results = sorted(state.get("tool_results") or [], key=lambda r: r["index"])
        called_tools = set(state["called_tools"])
        response_parts = []
        extra_parts: list[dict] = []
        for r in results:
            response_parts.append(r["response_part"])
            extra_parts.extend(r["extra_parts"])
            if r["called_name"]:
                called_tools.add(r["called_name"])
        return {
            "contents": [
                state["model_turn"],
                {"role": "user", "parts": response_parts + extra_parts},
            ],
            "called_tools": called_tools,
            "tool_results": None,
        }

    @staticmethod
    def _route_after_turn(state: _VerifyState) -> str:
        if state["iteration"] >= settings.verification_max_loop_iterations:
            return "stricter_retry"
        return "model"

    async def _node_stricter_retry(self, state: _VerifyState) -> dict:
        stricter_turn = [{"role": "user", "parts": [{"text": _STRICTER_RETRY_TEXT}]}]
        try:
            response = await self._call_api(state["contents"] + stricter_turn)
        except _RETRYABLE_API_ERRORS as exc:
            raise VerificationError(
                f"Sublot {state['sublot_id']}: Gemini API failed on stricter-prompt retry: {exc}"
            ) from exc
        candidate = response.candidates[0]
        missing_before_verdict = _REQUIRED_TOOLS_BEFORE_VERDICT - state["called_tools"]

        for part in candidate.content.parts or []:
            if (
                part.function_call
                and part.function_call.name == "submit_verdict"
                and not missing_before_verdict
            ):
                return {
                    "contents": stricter_turn,
                    "result": self._parse_verdict(dict(part.function_call.args or {})),
                }
        return {"contents": stricter_turn}

    @staticmethod
    def _route_stricter_retry_result(state: _VerifyState) -> str:
        return "end" if "result" in state else "human_review"

    async def _node_human_review(self, state: _VerifyState) -> dict:
        missing = _REQUIRED_TOOLS_BEFORE_VERDICT - state["called_tools"]
        decision = interrupt(
            {
                "sublot_id": state["sublot_id"],
                "reason": "loop_exhausted_no_grounded_verdict",
                "missing_tools": sorted(missing),
            }
        )
        if decision.get("mode") == "verdict":
            return {"result": self._parse_verdict(decision["verdict"])}
        guidance_text = decision.get("text", "")
        return {
            "contents": [
                {
                    "role": "user",
                    "parts": [
                        {
                            "text": (
                                f"A human reviewer added this guidance: {guidance_text}\n"
                                "Please reconsider and call submit_verdict once you're confident."
                            ),
                        }
                    ],
                }
            ],
            "iteration": 0,
        }

    @staticmethod
    def _route_after_human_review(state: _VerifyState) -> str:
        return "end" if "result" in state else "model"

    async def translate_explanation(self, explanation: str, language_code: str) -> str | None:
        language_name = _LANGUAGE_NAMES.get(language_code, language_code)
        try:
            response = await self._client.aio.models.generate_content(
                model=settings.verification_model,
                contents=[{
                    "role": "user",
                    "parts": [{
                        "text": (
                            f"Translate the following product quality note into "
                            f"natural, plain {language_name} for a small workshop "
                            f"owner to read. Return only the translated text, "
                            f"nothing else.\n\n{explanation}"
                        ),
                    }],
                }],
                config=types.GenerateContentConfig(max_output_tokens=settings.verification_max_tokens),
            )
            parts = response.candidates[0].content.parts or []
            translated = "".join(p.text for p in parts if p.text).strip()
            return translated or None
        except Exception:
            logger.warning(
                "Translation to %s failed for a verification explanation — "
                "falling back to English only", language_name, exc_info=True,
            )
            return None

    @retry(
        retry=retry_if_exception_type(_RETRYABLE_API_ERRORS),
        stop=stop_after_attempt(settings.verification_retry_attempts),
        wait=wait_exponential(
            min=settings.verification_retry_min_wait_seconds,
            max=settings.verification_retry_max_wait_seconds,
        ),
        reraise=True,
    )
    async def _call_api(self, contents: list[dict]) -> types.GenerateContentResponse:
        return await self._client.aio.models.generate_content(
            model=settings.verification_model,
            contents=contents,
            config=types.GenerateContentConfig(
                system_instruction=SYSTEM_PROMPT,
                tools=TOOLS,
                max_output_tokens=settings.verification_max_tokens,
                automatic_function_calling=types.AutomaticFunctionCallingConfig(
                    disable=True
                ),
            ),
        )

    async def _execute_tool(
        self, name: str, order_id: int, workshop_id: int
    ) -> tuple[dict, list[dict]]:
        if name == "get_order_spec":
            return await self._get_order_spec(order_id), []
        if name == "get_workshop_history":
            return await self._get_workshop_history(workshop_id), []
        if name == "get_reference_image":
            return await self._get_reference_image(order_id)
        return {"error": f"Unknown tool: {name}"}, []

    async def _get_order_spec(self, order_id: int) -> dict:
        order_row = await self._orders.get(order_id)
        if order_row is None:
            return {"error": f"Order {order_id} not found"}
        return {
            "product_type": order_row["product_type"],
            "total_qty": order_row["total_qty"],
            "quality_min": order_row["quality_min"],
            "deadline": str(order_row["deadline"]),
        }

    async def _get_workshop_history(self, workshop_id: int) -> dict:
        events = await self._trust.get_recent_events(
            workshop_id, limit=settings.trust_window_size
        )
        scorer = self._trust.scorer
        common_failure_modes = await self._verifications.get_recent_explanations(
            workshop_id, limit=5
        )
        return {
            "trust_score": scorer.compute_score(events),
            "recent_defect_rate": scorer.compute_defect_rate(events),
            "common_failure_modes": common_failure_modes,
        }

    async def _get_reference_image(self, order_id: int) -> tuple[dict, list[dict]]:
        order_row = await self._orders.get(order_id)
        if order_row is None:
            return {"error": f"Order {order_id} not found"}, []

        product_type = order_row["product_type"]
        path = reference_images.get_path(product_type)
        if path is None:
            return (
                {
                    "found": False,
                    "product_type": product_type,
                    "note": "No reference photo on file for this product_type.",
                },
                [],
            )

        image_data = self._encode_image(str(path))
        return (
            {"found": True, "product_type": product_type},
            [
                {
                    "inline_data": {
                        "mime_type": self._media_type(str(path)),
                        "data": image_data,
                    },
                }
            ],
        )

    @staticmethod
    def _parse_verdict(tool_input: dict) -> VerificationOutput:
        try:
            verdict = tool_input["verdict"]
            fault_party = tool_input["fault_party"]
            confidence = float(tool_input["confidence"])
            explanation = str(tool_input["explanation"])
        except (KeyError, TypeError, ValueError) as exc:
            raise VerificationError(
                f"Model returned malformed submit_verdict call: {exc} — raw: {tool_input}"
            ) from exc

        if verdict not in _VALID_VERDICTS:
            raise VerificationError(
                f"Model returned invalid verdict {verdict!r} — "
                f"expected one of {sorted(_VALID_VERDICTS)}"
            )
        if fault_party not in _VALID_FAULT_PARTIES:
            raise VerificationError(
                f"Model returned invalid fault_party {fault_party!r} — "
                f"expected one of {sorted(_VALID_FAULT_PARTIES)}"
            )
        if not (0.0 <= confidence <= 1.0):
            raise VerificationError(
                f"Model returned confidence {confidence} outside [0.0, 1.0]"
            )

        return VerificationOutput(
            verdict=verdict,
            fault_party=fault_party,
            confidence=confidence,
            explanation=explanation,
        )

    @staticmethod
    def _encode_image(photo_path: str) -> str:
        data = Path(photo_path).read_bytes()
        return base64.standard_b64encode(data).decode()

    @staticmethod
    def _media_type(photo_path: str) -> str:
        suffix = Path(photo_path).suffix.lower()
        media_type = SUPPORTED_IMAGE_EXTENSIONS.get(suffix)
        if media_type is None:
            raise VerificationError(
                f"Unsupported image extension {suffix!r} — "
                f"Gemini vision accepts: {', '.join(sorted(SUPPORTED_IMAGE_EXTENSIONS))}"
            )
        return media_type
