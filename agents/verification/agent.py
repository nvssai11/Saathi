from __future__ import annotations

import base64
import logging
from pathlib import Path

import httpx
from google import genai
from google.genai import errors as genai_errors
from google.genai import types
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


class VerificationAgent:
    def __init__(
        self,
        client: genai.Client,
        order_repo: OrderRepository,
        trust_repo: TrustRepository,
        verification_repo: VerificationRepository,
    ) -> None:
        self._client = client
        self._orders = order_repo
        self._trust = trust_repo
        self._verifications = verification_repo

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

        called_tools: set[str] = set()

        for iteration in range(settings.verification_max_loop_iterations):
            try:
                response = await self._call_api(contents)
            except _RETRYABLE_API_ERRORS as exc:
                raise VerificationError(
                    f"Sublot {sublot_id}: Gemini API failed after "
                    f"{settings.verification_retry_attempts} retries: {exc}"
                ) from exc
            candidate = response.candidates[0]
            parts = candidate.content.parts or []
            function_calls = [p for p in parts if p.function_call]
            missing_before_verdict = _REQUIRED_TOOLS_BEFORE_VERDICT - called_tools

            for part in function_calls:
                if part.function_call.name == "submit_verdict" and not missing_before_verdict:
                    return self._parse_verdict(dict(part.function_call.args or {}))

            if not function_calls:
                if candidate.finish_reason == types.FinishReason.STOP:
                    logger.warning(
                        "VerificationAgent finished without a verdict on sublot %d "
                        "(iteration %d/%d)",
                        sublot_id, iteration + 1, settings.verification_max_loop_iterations,
                    )
                    break
                contents.append(candidate.content)
                contents.append({
                    "role": "user",
                    "parts": [{
                        "text": (
                            "Please continue — call get_order_spec, get_workshop_history, "
                            "and then submit_verdict."
                        ),
                    }],
                })
                continue

            response_parts = []
            extra_parts: list[dict] = []
            for part in function_calls:
                name = part.function_call.name
                if name == "submit_verdict":
                    result: dict = {
                        "error": (
                            "Cannot submit a verdict yet — call these tools "
                            f"first: {', '.join(sorted(missing_before_verdict))}."
                        ),
                    }
                    tool_extra_parts: list[dict] = []
                else:
                    result, tool_extra_parts = await self._execute_tool(
                        name, order_id, workshop_id
                    )
                    if name in _REQUIRED_TOOLS_BEFORE_VERDICT:
                        called_tools.add(name)
                response_parts.append({
                    "function_response": {
                        "name": name,
                        "response": {"result": result},
                    },
                })
                extra_parts.extend(tool_extra_parts)

            contents.append(candidate.content)
            contents.append({"role": "user", "parts": response_parts + extra_parts})

        return await self._retry_with_stricter_prompt(contents, sublot_id, called_tools)

    async def _retry_with_stricter_prompt(
        self, contents: list[dict], sublot_id: int, called_tools: set[str]
    ) -> VerificationOutput:
        contents.append({
            "role": "user",
            "parts": [{
                "text": (
                    "You have the order spec and workshop history already. "
                    "Return your verdict now by calling submit_verdict."
                ),
            }],
        })
        try:
            response = await self._call_api(contents)
        except _RETRYABLE_API_ERRORS as exc:
            raise VerificationError(
                f"Sublot {sublot_id}: Gemini API failed on stricter-prompt retry: {exc}"
            ) from exc
        candidate = response.candidates[0]
        missing_before_verdict = _REQUIRED_TOOLS_BEFORE_VERDICT - called_tools

        for part in candidate.content.parts or []:
            if (
                part.function_call
                and part.function_call.name == "submit_verdict"
                and not missing_before_verdict
            ):
                return self._parse_verdict(dict(part.function_call.args or {}))

        raise NeedsHumanReviewError(
            f"Sublot {sublot_id}: loop exhausted after "
            f"{settings.verification_max_loop_iterations} iterations plus one "
            f"stricter-prompt retry, still no verdict grounded in required "
            f"tool calls (missing: {', '.join(sorted(missing_before_verdict)) or 'none — no verdict call at all'})"
        )

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
            [{
                "inline_data": {
                    "mime_type": self._media_type(str(path)),
                    "data": image_data,
                },
            }],
        )

    @staticmethod
    def _parse_verdict(tool_input: dict) -> VerificationOutput:
        try:
            verdict     = tool_input["verdict"]
            fault_party = tool_input["fault_party"]
            confidence  = float(tool_input["confidence"])
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
