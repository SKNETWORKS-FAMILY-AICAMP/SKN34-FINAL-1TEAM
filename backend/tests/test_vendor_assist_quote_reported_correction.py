"""문장을 나누어 보고한 견적 금액의 정정은 완전한 폐기·채택 근거가 필요하다."""

import asyncio
from datetime import date
from uuid import UUID

import pytest

from app.agent import vendor_assist
from app.vendor.assist_schemas import (
    AssistContext,
    AssistRequest,
    CorrectionRelation,
    FieldIntent,
    ModelInterpretation,
)
from app.vendor.assist_service import interpret

OLD = "총액 320만원이라고 제가 잘못 말씀드렸네요"
NEW = "정확히는 294만 8천 원이에요"
CONFIRM = "그 금액으로 정정 부탁해요"
TEXT = f"아까 {OLD}. {NEW}. {CONFIRM}."
CURRENT = {"amount": 3_200_000, "vat": "included", "days": 4, "valid": "2026-12-20"}


def run_case(
    monkeypatch,
    text,
    *,
    rejected=3_200_000,
    accepted=2_948_000,
    old="320만원",
    new="294만 8천 원",
    rule=False,
):
    field = FieldIntent(
        field="amount",
        action="set",
        value=accepted,
        evidence=new,
        intent_kind="explicit_change",
        value_source="utterance",
        correction=CorrectionRelation(
            kind="replace",
            rejected_value=rejected,
            rejected_evidence=old,
            accepted_evidence=new,
        ),
    )

    async def fake_model(_request, _context):
        return ModelInterpretation(fields=[field])

    monkeypatch.setattr(vendor_assist, "interpret", fake_model)
    request = AssistRequest(
        request_id="reported-amount-correction",
        form="quote",
        resource_id=UUID("00000000-0000-4000-8000-000000000179"),
        text=text,
        current=CURRENT.copy(),
        previous_candidate=None,
        rule_fields=[FieldIntent(field="amount", action="set", value=3_200_000, evidence="320만원")]
        if rule
        else [],
    )
    trace = {}
    result = asyncio.run(interpret(request, AssistContext(today=date(2026, 11, 23)), trace=trace))
    return {field.field: field for field in result.fields}, trace


@pytest.mark.parametrize(
    ("text", "old", "new"),
    [
        (TEXT, OLD, NEW),
        (TEXT, "320만원", "294만 8천 원"),
        (
            "앞서 견적 금액 320만원이라고 잘못 말했어요. 실제 금액은 294만 8천 원입니다. 이 금액으로 정정해 주세요.",
            "320만원",
            "294만 8천 원",
        ),
    ],
)
def test_reported_quote_amount_is_verified(monkeypatch, text, old, new):
    result, trace = run_case(monkeypatch, text, old=old, new=new, rule=True)
    amount = result["amount"]
    assert (amount.status, amount.action, amount.value, amount.source) == (
        "valid",
        "set",
        2_948_000,
        "llm",
    )
    assert amount.correction.rejected_value == 3_200_000
    assert trace["model_reviewed"][0]["reason_code"] is None
    assert all(result[name].action == "keep" for name in ("vat", "days", "valid"))


@pytest.mark.parametrize(
    "text",
    [
        TEXT.replace("잘못 말씀드렸네요", "말씀드렸네요"),
        TEXT.replace("정확히는", "혹시"),
        TEXT.replace("원이에요.", "원이에요?"),
        TEXT.replace("정정 부탁해요", "정정할까요"),
        TEXT.replace("정정 부탁해요", "정정하지 마세요"),
        TEXT.replace("그 금액", "그 기간"),
        TEXT.replace("총액", "계약금"),
        TEXT.replace("정확히는", "추가금은"),
        TEXT.replace("그 금액으로", "가능하면 그 금액으로"),
        TEXT.replace("그 금액으로", "300만원 대신 그 금액으로"),
        TEXT + "금액은 취소할게요.",
        TEXT + "혹은 300만원으로 할게요.",
        f"{NEW}. 아까 {OLD}. {CONFIRM}.",
    ],
)
def test_reported_quote_amount_rejects_ambiguous_relations(monkeypatch, text):
    result, _ = run_case(monkeypatch, text)
    field = result["amount"]
    assert not (field.status == "valid" and field.action == "set"), field


@pytest.mark.parametrize(
    ("rejected", "accepted", "old", "new"),
    [
        (3_210_000, 2_948_000, "320만원", "294만 8천 원"),
        (3_200_000, 2_940_000, "320만원", "294만"),
        (3_200_000, 8_000, "320만원", "8천 원"),
        (200_000, 2_948_000, "20만원", "294만 8천 원"),
    ],
)
def test_reported_quote_amount_rejects_wrong_or_clipped_spans(
    monkeypatch, rejected, accepted, old, new
):
    result, _ = run_case(monkeypatch, TEXT, rejected=rejected, accepted=accepted, old=old, new=new)
    field = result["amount"]
    assert not (field.status == "valid" and field.action == "set"), field
