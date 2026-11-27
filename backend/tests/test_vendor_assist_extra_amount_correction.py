"""업체 추가금의 폐기 금액 → 채택 금액 정정 근거를 실제 서비스로 검증한다."""

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

CURRENT = {
    "title": "걸레받이 보수",
    "reason": "철거 중 파손 발견",
    "amount": 180_000,
    "vat": "excluded",
}
RESOURCE = UUID("00000000-0000-4000-8000-000000000179")


def _candidate(
    rejected=180_000,
    accepted=215_000,
    *,
    rejected_evidence="18만 원은 잘못 적었고",
    accepted_evidence="21만 5천 원으로 정정",
    evidence=None,
):
    return FieldIntent(
        field="amount",
        action="set",
        value=accepted,
        evidence=evidence or f"{rejected_evidence} {accepted_evidence}",
        intent_kind="explicit_change",
        value_source="utterance",
        correction=CorrectionRelation(
            kind="replace",
            rejected_value=rejected,
            rejected_evidence=rejected_evidence,
            accepted_evidence=accepted_evidence,
        ),
    )


def _interpret(monkeypatch, text, amount, *, form="extra"):
    async def fake_model(_request, _context):
        fields = [amount]
        if form == "extra":
            fields += [
                FieldIntent(
                    field="vat",
                    action="keep",
                    value=None,
                    evidence="세금 조건은 그대로요" if "세금 조건은 그대로요" in text else "",
                    intent_kind="retain_current"
                    if "세금 조건은 그대로요" in text
                    else "no_mention",
                    value_source="current" if "세금 조건은 그대로요" in text else None,
                )
            ]
        return ModelInterpretation(fields=fields)

    monkeypatch.setattr(vendor_assist, "interpret", fake_model)
    request = AssistRequest(
        request_id="extra-amount-correction",
        form=form,
        resource_id=RESOURCE,
        text=text,
        current=CURRENT.copy() if form == "extra" else {"amount": 180_000, "days": 2},
        previous_candidate=None,
        rule_fields=[],
    )
    trace = {}
    result = asyncio.run(
        interpret(
            request,
            AssistContext(today=date(2026, 11, 23), base_month=date(2026, 12, 1)),
            trace=trace,
        )
    )
    return {field.field: field for field in result.fields}, trace


def _held(field):
    assert not (field.action in ("set", "clear") and field.status == "valid"), field


def test_actual_extra_price_correction_is_actionable_with_vat_unchanged(monkeypatch):
    text = "18만 원은 잘못 적었고 21만 5천 원으로 정정해 주세요. 세금 조건은 그대로요."
    result, trace = _interpret(monkeypatch, text, _candidate())
    amount = result["amount"]
    assert (amount.status, amount.action, amount.value, amount.source) == (
        "valid",
        "set",
        215_000,
        "llm",
    )
    assert (amount.correction.kind, amount.correction.rejected_value) == ("replace", 180_000)
    assert amount.origin_request_id == "extra-amount-correction"
    assert trace["model_reviewed"][0]["reason_code"] is None
    assert result["vat"].status == "valid" and result["vat"].action == "keep"
    assert result["title"].action == "keep"
    assert result["reason"].action == "keep"


@pytest.mark.parametrize(
    ("text", "old_evidence", "new_evidence"),
    [
        (
            "18만 원은 잘못 썼고 21만 5천 원으로 정정해 주세요.",
            "18만 원은 잘못 썼고",
            "21만 5천 원으로 정정",
        ),
        (
            "18만 원은 잘못 입력했는데 21만 5천 원으로 고쳐 주세요.",
            "18만 원은 잘못 입력했는데",
            "21만 5천 원으로 고쳐",
        ),
        (
            "18만 원은 틀리게 적었고 21만 5천 원으로 수정해 주세요.",
            "18만 원은 틀리게 적었고",
            "21만 5천 원으로 수정",
        ),
        (
            "18만 원은 잘못 적었고 21만 5천 원으로 정정해 주세요. 작업 인원 2명은 그대로요.",
            "18만 원은 잘못 적었고",
            "21만 5천 원으로 정정",
        ),
    ],
)
def test_explicit_error_and_forward_amount_change(monkeypatch, text, old_evidence, new_evidence):
    result, _ = _interpret(
        monkeypatch,
        text,
        _candidate(
            rejected_evidence=old_evidence,
            accepted_evidence=new_evidence,
            evidence=text.split(".")[0],
        ),
    )
    assert (result["amount"].status, result["amount"].value) == ("valid", 215_000)


@pytest.mark.parametrize(
    "text",
    [
        "21만 5천 원은 잘못 적었고 18만 원으로 정정해 주세요.",
        "18만 원으로 정정하고 21만 5천 원은 잘못 적었어요.",
        "18만 원을 입력했고 21만 5천 원으로 정정해 주세요.",
        "18만 원은 잘못 적었고 21만 5천 원을 정정해 주세요.",
        "18만 원은 잘못 적었고 21만 5천 원으로 정정하지 마세요.",
        "18만 원은 잘못 적었고 21만 5천 원으로 정정할까요?",
        "18만 원은 잘못 적었고 21만 5천 원으로 정정해 주세요?",
        "18만 원은 잘못 적었고 21만 5천 원으로 정정할 수도 있어요.",
        "18만 원은 잘못 적었고 21만 5천 원으로 정정할 생각 중이에요.",
        "18만 원은 잘못 적었고 21만 5천 원으로 정정할 예정입니다.",
        "18만 원은 잘못 적었고 21만 5천 원이나 22만 원으로 정정해 주세요.",
        "18만 원은 잘못 적었고 21만 5천 원 또는 22만 원으로 정정해 주세요.",
        "18만 원은 잘못 적었고 21만 5천 원으로 정정하고 22만 원으로 또 바꿔 주세요.",
        "18만 원은 잘못 적었고 21만 5천 원으로 정정해 주세요. 금액 220000으로도 가능합니다.",
        "18만 원은 잘못 적었고 21만 5천 원으로 정정해 주세요. 아, 금액 정정은 취소할게요.",
        "18만 원은 잘못 적었고 21만 5천 원으로 정정해 주세요. 정정은 없던 걸로 할게요.",
        "18만 원은 잘못 적었고 21만 5천 원으로 정정해 주세요. 아, 취소할게요.",
        "18만 원은 잘못 적었고 21만 5천 원으로 정정해 주세요. 금액은 다시 보류할게요.",
        "아직 미정인데 18만 원은 잘못 적었고 21만 5천 원으로 정정해 주세요.",
        "금액을 고민 중이라 18만 원은 잘못 적었고 21만 5천 원으로 정정해 주세요.",
        "18만 원은 잘못 적었고 21만 5천 원으로 정정해 달라고 말하지 마세요.",
    ],
)
def test_reversed_uncertain_or_extra_alternatives_are_not_actionable(monkeypatch, text):
    result, _ = _interpret(
        monkeypatch,
        text,
        _candidate(rejected_evidence="18만 원", accepted_evidence="21만 5천 원", evidence=text),
    )
    _held(result["amount"])


@pytest.mark.parametrize(
    ("rejected", "accepted"),
    [(170_000, 215_000), (180_000, 210_000), (180_000, 5_000), (180_000, 220_000)],
)
def test_model_values_must_match_full_grounded_amounts(monkeypatch, rejected, accepted):
    text = "18만 원은 잘못 적었고 21만 5천 원으로 정정해 주세요."
    result, _ = _interpret(monkeypatch, text, _candidate(rejected=rejected, accepted=accepted))
    _held(result["amount"])


def test_cancellation_explicitly_scoped_to_other_field_does_not_cancel_amount(monkeypatch):
    text = "18만 원은 잘못 적었고 21만 5천 원으로 정정해 주세요. 세금 조건 변경은 취소할게요."
    result, _ = _interpret(monkeypatch, text, _candidate())
    assert (result["amount"].status, result["amount"].value) == ("valid", 215_000)


def test_correction_special_case_does_not_relax_quote_validation(monkeypatch):
    text = "18만 원은 잘못 적었고 21만 5천 원으로 정정해 주세요."
    result, _ = _interpret(monkeypatch, text, _candidate(), form="quote")
    _held(result["amount"])
