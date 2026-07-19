from __future__ import annotations

import base64
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from google.genai import errors as genai_errors
from google.genai import types
from langgraph.checkpoint.memory import MemorySaver

from agents.verification.agent import VerificationAgent
from config import settings
from core.domain import VerificationOutput
from core.exceptions import NeedsHumanReviewError, VerificationError

def _function_call_part(
    name: str, args: dict, thought_signature: bytes | None = None
) -> MagicMock:
    part = MagicMock()
    part.function_call = MagicMock(name=name, args=args)
    part.function_call.name = name
    part.thought_signature = thought_signature
    return part


def _text_part() -> MagicMock:
    part = MagicMock()
    part.function_call = None
    part.text = "thinking out loud"
    return part


def _response(parts: list, finish_reason=types.FinishReason.STOP) -> MagicMock:
    candidate = MagicMock()
    candidate.content = MagicMock(parts=parts)
    candidate.finish_reason = finish_reason
    response = MagicMock()
    response.candidates = [candidate]
    return response


def _tool_response(
    verdict: str = "OK",
    fault_party: str = "none",
    confidence: float = 0.95,
    explanation: str = "Item matches specification.",
) -> MagicMock:
    part = _function_call_part(
        "submit_verdict",
        {
            "verdict": verdict,
            "fault_party": fault_party,
            "confidence": confidence,
            "explanation": explanation,
        },
    )
    return _response([part])


def _text_response() -> MagicMock:
    return _response([_text_part()], finish_reason=types.FinishReason.STOP)


def _thinking_response() -> MagicMock:
    return _response([_text_part()], finish_reason=types.FinishReason.MAX_TOKENS)


def _context_gathering_responses(order_id: int = 1, workshop_id: int = 1) -> list:
    return [
        _response([_function_call_part("get_order_spec", {"order_id": order_id})]),
        _response([_function_call_part("get_workshop_history", {"workshop_id": workshop_id})]),
    ]


def _make_agent(order_repo=None, trust_repo=None, verification_repo=None) -> VerificationAgent:
    order = order_repo or AsyncMock()
    if order_repo is None:
        order.get.return_value = {
            "product_type": "kurta", "total_qty": 100, "quality_min": 3, "deadline": "2026-08-01",
        }

    trust = trust_repo or AsyncMock()
    if trust_repo is None:
        trust.get_recent_events.return_value = []
        trust.scorer = MagicMock()
        trust.scorer.compute_score.return_value = 0.8
        trust.scorer.compute_defect_rate.return_value = 0.1

    verification = verification_repo or AsyncMock()
    if verification_repo is None:
        verification.get_recent_explanations.return_value = []

    return VerificationAgent(
        client=MagicMock(),
        order_repo=order,
        trust_repo=trust,
        verification_repo=verification,
        checkpointer=MemorySaver(),
    )

@pytest.mark.anyio
async def test_translate_explanation_returns_translated_text():
    agent = _make_agent()
    translated_part = _text_part()
    translated_part.text = "यह वस्तु ऑर्डर से मेल नहीं खाती।"
    agent._client.aio.models.generate_content = AsyncMock(
        return_value=_response([translated_part])
    )

    result = await agent.translate_explanation("This item does not match the order.", "hi")

    assert result == "यह वस्तु ऑर्डर से मेल नहीं खाती।"


@pytest.mark.anyio
async def test_translate_explanation_returns_none_on_api_failure():
    agent = _make_agent()
    agent._client.aio.models.generate_content = AsyncMock(
        side_effect=genai_errors.APIError(500, {"error": {"message": "server error"}})
    )

    result = await agent.translate_explanation("This item does not match the order.", "hi")

    assert result is None


@pytest.mark.anyio
async def test_translate_explanation_returns_none_for_blank_response():
    agent = _make_agent()
    blank_part = _text_part()
    blank_part.text = ""
    agent._client.aio.models.generate_content = AsyncMock(
        return_value=_response([blank_part])
    )

    result = await agent.translate_explanation("This item does not match the order.", "hi")

    assert result is None


@pytest.mark.anyio
async def test_verify_full_tool_sequence_returns_output():
    order_repo = AsyncMock()
    order_repo.get.return_value = {
        "product_type": "kurta", "total_qty": 100, "quality_min": 3, "deadline": "2026-08-01",
    }
    trust_repo = AsyncMock()
    trust_repo.get_recent_events.return_value = []
    trust_repo.scorer = MagicMock()
    trust_repo.scorer.compute_score.return_value = 0.8
    trust_repo.scorer.compute_defect_rate.return_value = 0.1
    verification_repo = AsyncMock()
    verification_repo.get_recent_explanations.return_value = ["Stitching defect on prior order."]

    agent = _make_agent(order_repo, trust_repo, verification_repo)

    responses = [
        _response([_function_call_part("get_order_spec", {"order_id": 1})]),
        _response([_function_call_part("get_workshop_history", {"workshop_id": 7})]),
        _tool_response("DEFECT", "workshop", 0.92, "Stitching defect visible."),
    ]

    with (
        patch.object(agent, "_call_api", side_effect=responses),
        patch.object(agent, "_encode_image", return_value="base64data"),
    ):
        result = await agent.verify("photo.jpg", order_id=1, workshop_id=7, sublot_id=42)

    assert isinstance(result, VerificationOutput)
    assert result.verdict == "DEFECT"
    assert result.fault_party == "workshop"
    order_repo.get.assert_awaited_once_with(1)
    trust_repo.get_recent_events.assert_awaited_once()
    verification_repo.get_recent_explanations.assert_awaited_once_with(7, limit=5)


@pytest.mark.anyio
async def test_thought_signature_is_echoed_back_on_the_next_turn():
    agent = _make_agent()
    signature_bytes = b"\x01\x02\xff\xfe-signature"

    responses = [
        _response([_function_call_part(
            "get_order_spec", {"order_id": 1}, thought_signature=signature_bytes,
        )]),
        _response([_function_call_part("get_workshop_history", {"workshop_id": 7})]),
        _tool_response("OK"),
    ]

    with (
        patch.object(agent, "_call_api", side_effect=responses) as mock_call,
        patch.object(agent, "_encode_image", return_value="data"),
    ):
        await agent.verify("photo.jpg", order_id=1, workshop_id=7, sublot_id=42)

    second_call_contents = mock_call.call_args_list[1].args[0]
    model_turn = next(c for c in second_call_contents if c.get("role") == "model")
    function_call_part = next(p for p in model_turn["parts"] if "function_call" in p)
    assert function_call_part["thought_signature"] == base64.standard_b64encode(signature_bytes).decode()


@pytest.mark.anyio
async def test_verify_ignores_model_provided_ids_uses_ground_truth():
    order_repo = AsyncMock()
    order_repo.get.return_value = {
        "product_type": "kurta", "total_qty": 50, "quality_min": 2, "deadline": "2026-09-01",
    }
    agent = _make_agent(order_repo=order_repo)

    responses = [
        _response([_function_call_part("get_order_spec", {"order_id": 999})]),
        _response([_function_call_part("get_workshop_history", {"workshop_id": 999})]),
        _tool_response("OK"),
    ]

    with (
        patch.object(agent, "_call_api", side_effect=responses),
        patch.object(agent, "_encode_image", return_value="data"),
    ):
        await agent.verify("photo.jpg", order_id=5, workshop_id=1, sublot_id=1)

    order_repo.get.assert_awaited_once_with(5)


@pytest.mark.anyio
async def test_verify_ok_verdict():
    agent = _make_agent()
    responses = _context_gathering_responses() + [
        _tool_response("OK", "none", 0.99, "Matches spec."),
    ]
    with (
        patch.object(agent, "_call_api", side_effect=responses),
        patch.object(agent, "_encode_image", return_value="data"),
    ):
        result = await agent.verify("photo.png", order_id=1, workshop_id=1, sublot_id=1)

    assert result.verdict == "OK"
    assert result.fault_party == "none"


@pytest.mark.anyio
async def test_verify_spec_ambiguity_verdict():
    agent = _make_agent()
    responses = _context_gathering_responses() + [
        _tool_response("SPEC_AMBIGUITY", "buyer", 0.75, "Spec too vague."),
    ]
    with (
        patch.object(agent, "_call_api", side_effect=responses),
        patch.object(agent, "_encode_image", return_value="data"),
    ):
        result = await agent.verify("photo.jpg", order_id=1, workshop_id=1, sublot_id=2)

    assert result.verdict == "SPEC_AMBIGUITY"
    assert result.fault_party == "buyer"


@pytest.mark.anyio
async def test_verify_passes_buyer_note_into_initial_message():
    agent = _make_agent()
    responses = _context_gathering_responses() + [_tool_response("OK")]
    with (
        patch.object(agent, "_call_api", side_effect=responses) as mock_call,
        patch.object(agent, "_encode_image", return_value="data"),
    ):
        await agent.verify(
            "photo.jpg", order_id=1, workshop_id=1, sublot_id=1,
            buyer_note="3 units — torn seam",
        )

    sent_contents = mock_call.call_args.args[0]
    text_part = sent_contents[0]["parts"][1]["text"]
    assert "3 units — torn seam" in text_part

@pytest.mark.anyio
async def test_verify_raises_verification_error_when_call_api_raises_api_error():
    agent = _make_agent()
    api_error = genai_errors.APIError(500, {"error": {"message": "connection failed"}})

    with (
        patch.object(agent, "_call_api", side_effect=api_error),
        patch.object(agent, "_encode_image", return_value="data"),
    ):
        with pytest.raises(VerificationError) as exc_info:
            await agent.verify("photo.jpg", order_id=1, workshop_id=1, sublot_id=5)

    assert "Gemini API failed" in str(exc_info.value)
    assert exc_info.value.__cause__ is api_error


@pytest.mark.anyio
async def test_verify_error_wraps_original_exception_as_cause():
    agent = _make_agent()
    original = genai_errors.APIError(503, {"error": {"message": "unavailable"}})

    with (
        patch.object(agent, "_call_api", side_effect=original),
        patch.object(agent, "_encode_image", return_value="data"),
    ):
        with pytest.raises(VerificationError) as exc_info:
            await agent.verify("photo.jpg", order_id=1, workshop_id=1, sublot_id=99)

    assert exc_info.value.__cause__ is original

@pytest.mark.anyio
async def test_verify_raises_needs_human_review_when_loop_and_retry_exhausted():
    agent = _make_agent()
    with (
        patch.object(agent, "_call_api", return_value=_text_response()),
        patch.object(agent, "_encode_image", return_value="data"),
    ):
        with pytest.raises(NeedsHumanReviewError) as exc_info:
            await agent.verify("photo.jpg", order_id=1, workshop_id=1, sublot_id=3)

    assert "paused for human review" in str(exc_info.value).lower()
    assert "3" in str(exc_info.value)
    assert exc_info.value.thread_id == "verification-sublot-3"


@pytest.mark.anyio
async def test_stricter_prompt_retry_succeeds_after_loop_exhaustion():
    agent = _make_agent()
    responses = _context_gathering_responses() + [_text_response(), _tool_response("OK")]
    with (
        patch.object(agent, "_call_api", side_effect=responses),
        patch.object(agent, "_encode_image", return_value="data"),
    ):
        result = await agent.verify("photo.jpg", order_id=1, workshop_id=1, sublot_id=3)

    assert result.verdict == "OK"


def test_needs_human_review_is_subclass_of_verification_error():
    assert issubclass(NeedsHumanReviewError, VerificationError)

@pytest.mark.anyio
async def test_verify_succeeds_when_tool_call_in_second_response():
    agent = _make_agent()
    responses = [_thinking_response()] + _context_gathering_responses() + [
        _tool_response("OK", "none", 0.88, "Looks fine."),
    ]

    with (
        patch.object(agent, "_call_api", side_effect=responses),
        patch.object(agent, "_encode_image", return_value="data"),
    ):
        result = await agent.verify("photo.jpg", order_id=1, workshop_id=1, sublot_id=4)

    assert result.verdict == "OK"

@pytest.mark.anyio
async def test_premature_submit_verdict_rejected_then_recovers():
    agent = _make_agent()
    responses = iter(
        [_tool_response("OK")] + _context_gathering_responses() + [_tool_response("OK")]
    )
    captured_contents: list[list] = []

    async def fake_call_api(contents: list[dict]):
        captured_contents.append(list(contents))
        return next(responses)

    with (
        patch.object(agent, "_call_api", side_effect=fake_call_api),
        patch.object(agent, "_encode_image", return_value="data"),
    ):
        result = await agent.verify("photo.jpg", order_id=1, workshop_id=1, sublot_id=40)

    assert result.verdict == "OK"
    rejected_turn = captured_contents[1][-1]["parts"]
    submit_verdict_replies = [
        p for p in rejected_turn
        if p.get("function_response", {}).get("name") == "submit_verdict"
    ]
    assert len(submit_verdict_replies) == 1
    error_text = submit_verdict_replies[0]["function_response"]["response"]["result"]["error"]
    assert "get_order_spec" in error_text
    assert "get_workshop_history" in error_text


@pytest.mark.anyio
async def test_premature_submit_verdict_never_recovers_raises_needs_human_review():
    agent = _make_agent()
    with (
        patch.object(agent, "_call_api", return_value=_tool_response("OK")),
        patch.object(agent, "_encode_image", return_value="data"),
    ):
        with pytest.raises(NeedsHumanReviewError) as exc_info:
            await agent.verify("photo.jpg", order_id=1, workshop_id=1, sublot_id=41)

    assert "get_order_spec" in str(exc_info.value)
    assert "get_workshop_history" in str(exc_info.value)
    assert exc_info.value.thread_id == "verification-sublot-41"


@pytest.mark.anyio
async def test_submit_verdict_accepted_once_only_required_tools_called_not_optional_one():
    agent = _make_agent()
    responses = _context_gathering_responses() + [_tool_response("OK")]
    with (
        patch.object(agent, "_call_api", side_effect=responses),
        patch.object(agent, "_encode_image", return_value="data"),
        patch("agents.verification.agent.reference_images.get_path", return_value=None),
    ):
        result = await agent.verify("photo.jpg", order_id=1, workshop_id=1, sublot_id=42)

    assert result.verdict == "OK"

@pytest.mark.anyio
async def test_missing_photo_raises_verification_error():
    agent = _make_agent()
    with patch.object(agent, "_encode_image", side_effect=FileNotFoundError("no such file")):
        with pytest.raises(VerificationError) as exc_info:
            await agent.verify("/uploads/missing.jpg", order_id=1, workshop_id=1, sublot_id=20)

    assert "cannot read photo" in str(exc_info.value).lower()
    assert exc_info.value.__cause__ is not None


@pytest.mark.anyio
async def test_unreadable_photo_raises_verification_error():
    agent = _make_agent()
    with patch.object(agent, "_encode_image", side_effect=PermissionError("access denied")):
        with pytest.raises(VerificationError):
            await agent.verify("/uploads/locked.jpg", order_id=1, workshop_id=1, sublot_id=21)

@pytest.mark.anyio
async def test_unsupported_image_extension_raises_verification_error():
    agent = _make_agent()
    with patch.object(agent, "_encode_image", return_value="data"):
        with pytest.raises(VerificationError) as exc_info:
            await agent.verify("photo.tiff", order_id=1, workshop_id=1, sublot_id=22)

    assert ".tiff" in str(exc_info.value)


def test_supported_extensions_are_accepted():
    agent = VerificationAgent(
        client=MagicMock(), order_repo=MagicMock(), trust_repo=MagicMock(),
        verification_repo=MagicMock(), checkpointer=MemorySaver(),
    )
    for ext in (".jpg", ".jpeg", ".png", ".webp", ".gif"):
        media_type = agent._media_type(f"photo{ext}")
        assert "/" in media_type

@pytest.mark.anyio
async def test_invalid_verdict_from_model_raises_verification_error():
    agent = _make_agent()
    with (
        patch.object(agent, "_call_api", return_value=_tool_response(verdict="OKAY")),
        patch.object(agent, "_encode_image", return_value="data"),
    ):
        with pytest.raises(VerificationError) as exc_info:
            await agent.verify("photo.jpg", order_id=1, workshop_id=1, sublot_id=30)

    assert "verdict" in str(exc_info.value)


@pytest.mark.anyio
async def test_invalid_fault_party_from_model_raises_verification_error():
    agent = _make_agent()
    responses = _context_gathering_responses() + [_tool_response(fault_party="neither")]
    with (
        patch.object(agent, "_call_api", side_effect=responses),
        patch.object(agent, "_encode_image", return_value="data"),
    ):
        with pytest.raises(VerificationError) as exc_info:
            await agent.verify("photo.jpg", order_id=1, workshop_id=1, sublot_id=31)

    assert "fault_party" in str(exc_info.value)


@pytest.mark.anyio
async def test_confidence_out_of_range_raises_verification_error():
    agent = _make_agent()
    responses = _context_gathering_responses() + [_tool_response(confidence=1.5)]
    with (
        patch.object(agent, "_call_api", side_effect=responses),
        patch.object(agent, "_encode_image", return_value="data"),
    ):
        with pytest.raises(VerificationError) as exc_info:
            await agent.verify("photo.jpg", order_id=1, workshop_id=1, sublot_id=32)

    assert "confidence" in str(exc_info.value).lower()


@pytest.mark.anyio
async def test_missing_field_in_tool_input_raises_verification_error():
    agent = _make_agent()
    part = _function_call_part(
        "submit_verdict",
        {"fault_party": "none", "confidence": 0.9, "explanation": "Looks ok."},
    )
    response = _response([part])
    responses = _context_gathering_responses() + [response]

    with (
        patch.object(agent, "_call_api", side_effect=responses),
        patch.object(agent, "_encode_image", return_value="data"),
    ):
        with pytest.raises(VerificationError) as exc_info:
            await agent.verify("photo.jpg", order_id=1, workshop_id=1, sublot_id=33)

    assert "malformed" in str(exc_info.value).lower()

@pytest.mark.anyio
async def test_api_error_during_stricter_prompt_retry_raises_verification_error():
    agent = _make_agent()
    api_error = genai_errors.APIError(500, {"error": {"message": "boom"}})

    with (
        patch.object(agent, "_call_api", side_effect=[_text_response(), api_error]),
        patch.object(agent, "_encode_image", return_value="data"),
    ):
        with pytest.raises(VerificationError) as exc_info:
            await agent.verify("photo.jpg", order_id=1, workshop_id=1, sublot_id=7)

    assert "stricter-prompt retry" in str(exc_info.value)
    assert exc_info.value.__cause__ is api_error

@pytest.mark.anyio
async def test_call_api_invokes_client_with_correct_shape():
    from agents.verification.prompts import SYSTEM_PROMPT, TOOLS

    mock_client = MagicMock()
    mock_client.aio.models.generate_content = AsyncMock(return_value=_tool_response("OK"))
    agent = VerificationAgent(
        client=mock_client, order_repo=AsyncMock(), trust_repo=AsyncMock(),
        verification_repo=AsyncMock(), checkpointer=MemorySaver(),
    )

    contents = [{"role": "user", "parts": [{"text": "test"}]}]
    result = await agent._call_api(contents)

    assert result.candidates[0].content.parts[0].function_call.name == "submit_verdict"
    mock_client.aio.models.generate_content.assert_awaited_once()
    call_kwargs = mock_client.aio.models.generate_content.call_args.kwargs
    assert call_kwargs["model"] == settings.verification_model
    assert call_kwargs["contents"] == contents
    config = call_kwargs["config"]
    assert config.system_instruction == SYSTEM_PROMPT
    assert config.tools == TOOLS
    assert config.automatic_function_calling.disable is True

@pytest.mark.anyio
async def test_execute_tool_unknown_name_returns_error_dict_not_crash():
    agent = _make_agent()
    result, extra_parts = await agent._execute_tool(
        "some_unknown_tool", order_id=1, workshop_id=1
    )
    assert "error" in result
    assert "some_unknown_tool" in result["error"]
    assert extra_parts == []

@pytest.mark.anyio
async def test_get_order_spec_returns_error_dict_when_order_missing():
    order_repo = AsyncMock()
    order_repo.get.return_value = None
    agent = _make_agent(order_repo=order_repo)

    result = await agent._get_order_spec(order_id=999)

    assert "error" in result
    assert "999" in result["error"]

@pytest.mark.anyio
async def test_get_reference_image_returns_error_dict_when_order_missing():
    order_repo = AsyncMock()
    order_repo.get.return_value = None
    agent = _make_agent(order_repo=order_repo)

    result, extra_parts = await agent._get_reference_image(order_id=999)

    assert "error" in result
    assert "999" in result["error"]
    assert extra_parts == []


@pytest.mark.anyio
async def test_get_reference_image_reports_not_found_when_no_photo_on_file():
    order_repo = AsyncMock()
    order_repo.get.return_value = {
        "product_type": "unlisted-product", "total_qty": 10, "quality_min": 1, "deadline": "2026-08-01",
    }
    agent = _make_agent(order_repo=order_repo)

    with patch("agents.verification.agent.reference_images.get_path", return_value=None):
        result, extra_parts = await agent._get_reference_image(order_id=1)

    assert result == {
        "found": False,
        "product_type": "unlisted-product",
        "note": "No reference photo on file for this product_type.",
    }
    assert extra_parts == []


@pytest.mark.anyio
async def test_get_reference_image_returns_inline_image_part_when_found(tmp_path):
    import base64

    photo_path = tmp_path / "jute-tote-bag.png"
    raw_bytes = b"\x89PNGfake-reference-bytes"
    photo_path.write_bytes(raw_bytes)

    order_repo = AsyncMock()
    order_repo.get.return_value = {
        "product_type": "jute-tote-bag", "total_qty": 100, "quality_min": 2, "deadline": "2026-08-01",
    }
    agent = _make_agent(order_repo=order_repo)

    with patch("agents.verification.agent.reference_images.get_path", return_value=photo_path):
        result, extra_parts = await agent._get_reference_image(order_id=1)

    assert result == {"found": True, "product_type": "jute-tote-bag"}
    assert len(extra_parts) == 1
    assert extra_parts[0]["inline_data"]["mime_type"] == "image/png"
    assert extra_parts[0]["inline_data"]["data"] == base64.standard_b64encode(raw_bytes).decode()

@pytest.mark.anyio
async def test_verify_attaches_reference_image_to_next_turn_when_found(tmp_path):
    import base64

    photo_path = tmp_path / "khadi-scarf.png"
    raw_bytes = b"fake-reference-image-bytes"
    photo_path.write_bytes(raw_bytes)

    order_repo = AsyncMock()
    order_repo.get.return_value = {
        "product_type": "khadi-scarf", "total_qty": 50, "quality_min": 2, "deadline": "2026-08-01",
    }
    agent = _make_agent(order_repo=order_repo)

    responses = _context_gathering_responses() + [
        _response([_function_call_part("get_reference_image", {"order_id": 1})]),
        _tool_response("OK"),
    ]

    def encode(path: str) -> str:
        if path == str(photo_path):
            return base64.standard_b64encode(raw_bytes).decode()
        return "defect-photo-data"

    with (
        patch.object(agent, "_call_api", side_effect=responses) as mock_call,
        patch.object(agent, "_encode_image", side_effect=encode),
        patch("agents.verification.agent.reference_images.get_path", return_value=photo_path),
    ):
        result = await agent.verify("defect.jpg", order_id=1, workshop_id=1, sublot_id=10)

    assert result.verdict == "OK"
    reference_call_contents = mock_call.call_args_list[3].args[0]
    last_turn_parts = reference_call_contents[-1]["parts"]
    function_response_parts = [p for p in last_turn_parts if "function_response" in p]
    inline_data_parts = [p for p in last_turn_parts if "inline_data" in p]

    assert len(function_response_parts) == 1
    assert function_response_parts[0]["function_response"]["response"]["result"]["found"] is True
    assert len(inline_data_parts) == 1
    assert inline_data_parts[0]["inline_data"]["data"] == base64.standard_b64encode(raw_bytes).decode()


@pytest.mark.anyio
async def test_verify_no_inline_image_part_when_reference_not_found():
    order_repo = AsyncMock()
    order_repo.get.return_value = {
        "product_type": "mystery-product", "total_qty": 10, "quality_min": 1, "deadline": "2026-08-01",
    }
    agent = _make_agent(order_repo=order_repo)

    responses = _context_gathering_responses() + [
        _response([_function_call_part("get_reference_image", {"order_id": 1})]),
        _tool_response("OK"),
    ]

    with (
        patch.object(agent, "_call_api", side_effect=responses) as mock_call,
        patch.object(agent, "_encode_image", return_value="data"),
        patch("agents.verification.agent.reference_images.get_path", return_value=None),
    ):
        await agent.verify("defect.jpg", order_id=1, workshop_id=1, sublot_id=11)

    reference_call_contents = mock_call.call_args_list[3].args[0]
    last_turn_parts = reference_call_contents[-1]["parts"]
    assert not any("inline_data" in p for p in last_turn_parts)

def test_encode_image_reads_and_base64_encodes_real_file(tmp_path):
    import base64

    photo_path = tmp_path / "defect.jpg"
    raw_bytes = b"\xff\xd8\xff\xe0fake-jpeg-content"
    photo_path.write_bytes(raw_bytes)

    encoded = VerificationAgent._encode_image(str(photo_path))

    assert encoded == base64.standard_b64encode(raw_bytes).decode()


def test_encode_image_raises_file_not_found_for_missing_file(tmp_path):
    missing = tmp_path / "does-not-exist.jpg"
    with pytest.raises(FileNotFoundError):
        VerificationAgent._encode_image(str(missing))


@pytest.mark.anyio
async def test_graph_recursion_limit_translates_to_needs_human_review():
    with patch.object(VerificationAgent, "_route_after_turn", return_value="model"):
        agent = _make_agent()

    with (
        patch.object(
            agent, "_call_api",
            return_value=_response([_function_call_part("get_order_spec", {"order_id": 1})]),
        ),
        patch.object(agent, "_encode_image", return_value="data"),
    ):
        with pytest.raises(NeedsHumanReviewError) as exc_info:
            await agent.verify("photo.jpg", order_id=1, workshop_id=1, sublot_id=99)

    assert "recursion limit" in str(exc_info.value).lower()


@pytest.mark.anyio
async def test_multiple_tool_calls_in_one_turn_dispatched_and_merged_in_order():
    agent = _make_agent()
    multi_call_response = _response([
        _function_call_part("get_order_spec", {"order_id": 1}),
        _function_call_part("get_workshop_history", {"workshop_id": 1}),
    ])
    responses = [multi_call_response, _tool_response("OK")]

    captured_contents: list[list] = []

    async def fake_call_api(contents):
        captured_contents.append(list(contents))
        return responses[len(captured_contents) - 1]

    with (
        patch.object(agent, "_call_api", side_effect=fake_call_api),
        patch.object(agent, "_encode_image", return_value="data"),
    ):
        result = await agent.verify("photo.jpg", order_id=1, workshop_id=1, sublot_id=50)

    assert result.verdict == "OK"
    assert len(captured_contents) == 2
    merged_turn = captured_contents[1][-1]["parts"]
    names_in_order = [
        p["function_response"]["name"] for p in merged_turn if "function_response" in p
    ]
    assert names_in_order == ["get_order_spec", "get_workshop_history"]


@pytest.mark.anyio
async def test_resume_with_guidance_gives_agent_a_fresh_attempt():
    agent = _make_agent()
    with (
        patch.object(agent, "_call_api", return_value=_text_response()),
        patch.object(agent, "_encode_image", return_value="data"),
    ):
        with pytest.raises(NeedsHumanReviewError) as exc_info:
            await agent.verify("photo.jpg", order_id=1, workshop_id=1, sublot_id=60)
    assert exc_info.value.thread_id == "verification-sublot-60"

    responses = _context_gathering_responses() + [_tool_response("OK")]
    with patch.object(agent, "_call_api", side_effect=responses):
        result = await agent.resume_with_guidance(
            sublot_id=60, guidance="Look more carefully at the stitching."
        )

    assert result.verdict == "OK"


@pytest.mark.anyio
async def test_resume_with_guidance_can_pause_again_if_still_unresolved():
    agent = _make_agent()
    with (
        patch.object(agent, "_call_api", return_value=_text_response()),
        patch.object(agent, "_encode_image", return_value="data"),
    ):
        with pytest.raises(NeedsHumanReviewError):
            await agent.verify("photo.jpg", order_id=1, workshop_id=1, sublot_id=62)

    with patch.object(agent, "_call_api", return_value=_text_response()):
        with pytest.raises(NeedsHumanReviewError) as exc_info:
            await agent.resume_with_guidance(sublot_id=62, guidance="still unclear")

    assert exc_info.value.thread_id == "verification-sublot-62"


@pytest.mark.anyio
async def test_resume_with_verdict_completes_paused_run_without_calling_model_again():
    agent = _make_agent()
    with (
        patch.object(agent, "_call_api", return_value=_text_response()),
        patch.object(agent, "_encode_image", return_value="data"),
    ):
        with pytest.raises(NeedsHumanReviewError):
            await agent.verify("photo.jpg", order_id=1, workshop_id=1, sublot_id=61)

    human_verdict = VerificationOutput(
        verdict="DEFECT", fault_party="workshop", confidence=1.0,
        explanation="Human reviewer confirmed a torn seam directly.",
    )
    with patch.object(agent, "_call_api") as mock_call:
        result = await agent.resume_with_verdict(sublot_id=61, verdict=human_verdict)

    assert result == human_verdict
    mock_call.assert_not_awaited()


@pytest.mark.anyio
async def test_is_resumable_false_for_a_crashed_not_interrupted_thread():
    agent = _make_agent()
    api_error = genai_errors.APIError(500, {"error": {"message": "boom"}})
    with (
        patch.object(agent, "_call_api", side_effect=api_error),
        patch.object(agent, "_encode_image", return_value="data"),
    ):
        with pytest.raises(VerificationError):
            await agent.verify("photo.jpg", order_id=1, workshop_id=1, sublot_id=70)

    assert await agent.is_resumable(70) is False


@pytest.mark.anyio
async def test_is_resumable_true_for_a_genuinely_paused_thread():
    agent = _make_agent()
    with (
        patch.object(agent, "_call_api", return_value=_text_response()),
        patch.object(agent, "_encode_image", return_value="data"),
    ):
        with pytest.raises(NeedsHumanReviewError):
            await agent.verify("photo.jpg", order_id=1, workshop_id=1, sublot_id=71)

    assert await agent.is_resumable(71) is True


@pytest.mark.anyio
async def test_is_resumable_false_for_a_thread_that_never_ran():
    agent = _make_agent()
    assert await agent.is_resumable(9999) is False
