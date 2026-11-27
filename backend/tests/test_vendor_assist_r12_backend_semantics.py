"""Field-local semantic operations exercised through the actual offline service."""

import asyncio
from datetime import date
from uuid import UUID

import pytest

from app.agent import llm, vendor_assist
from app.config import settings
from app.vendor.assist_schemas import AssistContext, AssistRequest, FieldIntent, ModelInterpretation
from app.vendor.assist_service import interpret

CTX = AssistContext(today=date(2026, 11, 25), base_month=date(2026, 12, 1))
CURRENT = {
    "extra": {"title": "기존 보강", "reason": "기존 손상", "amount": 250000, "vat": "included"},
    "period": {"start": "2026-12-01", "end": "2026-12-31", "excluded": []},
    "quote": {"amount": 5000000, "vat": "included", "days": 5, "valid": "2026-12-31"},
    "dates": {"days": ["2026-12-02"], "crew": 2},
}


@pytest.mark.parametrize("producer", ["rule", "model", "both"])
@pytest.mark.parametrize(
    "form,field,text,evidence,value,accepted",
    [
        (
            "extra",
            "amount",
            "사유는 35만원 상당 자재 손상으로 기재합니다.",
            "35만원",
            350000,
            False,
        ),
        (
            "extra",
            "amount",
            "사유는 28만원 상당 자재 손상으로 기재합니다.",
            "28만원",
            280000,
            False,
        ),
        (
            "period",
            "excluded",
            "12월 11일을 뺄지 12월 15일을 뺄지 미정입니다.",
            "12월 11일",
            ["2026-12-11"],
            False,
        ),
        (
            "period",
            "excluded",
            "12월 3일을 뺄지 12월 7일을 뺄지 아직 정하지 못했어요.",
            "12월 3일",
            ["2026-12-03"],
            False,
        ),
        (
            "quote",
            "days",
            "현장 상황상 7일이 걸립니다. 공사 기간을 7일로 수정해 주세요.",
            "공사 기간을 7일로 수정해 주세요",
            7,
            True,
        ),
        (
            "quote",
            "days",
            "현장 상황상 7일이 걸립니다. 공사 기간을 7일로 수정하지 말아 주세요.",
            "공사 기간을 7일로 수정",
            7,
            False,
        ),
        (
            "quote",
            "days",
            "공사 기간은 7일과 9일 중 고민입니다. 7일로 수정할까요?",
            "7일로 수정",
            7,
            False,
        ),
        (
            "dates",
            "crew",
            "인원은 두 명으로 두는 게 좋겠어요. 작업 인원 4명으로 변경하지 마세요.",
            "작업 인원 4명",
            4,
            False,
        ),
        ("quote", "vat", "부가세 별도로 발행해 주세요.", "부가세 별도로", "excluded", True),
        ("quote", "vat", "부가세 별도로 발행하면 되나요?", "부가세 별도로", "excluded", False),
        (
            "quote",
            "vat",
            "지난번에 부가세를 별도로 한다고 했었어요.",
            "부가세를 별도로",
            "excluded",
            False,
        ),
        (
            "quote",
            "vat",
            "지난번에 부가세를 별도로 한다고 했었어요. 이번에는 부가세를 포함으로 변경해 주세요.",
            "부가세를 포함으로 변경해 주세요",
            "included",
            True,
        ),
    ],
)
def test_semantic_field_ownership(
    monkeypatch, producer, form, field, text, evidence, value, accepted
):
    assert not settings.llm_live
    proposal = FieldIntent(
        field=field,
        action="set",
        value=value,
        evidence=evidence,
        intent_kind="explicit_change",
        value_source="utterance",
    )
    request = AssistRequest(
        request_id="r12-offline",
        resource_id=UUID(int=179),
        form=form,
        text=text,
        current=CURRENT[form],
        previous_candidate=None,
        rule_fields=[proposal] if producer in ("rule", "both") else [],
    )

    async def forbidden(*args, **kwargs):
        raise AssertionError("External provider forbidden")

    async def stub(*args, **kwargs):
        return ModelInterpretation(fields=[proposal])

    monkeypatch.setattr(llm, "structured", forbidden)
    monkeypatch.setattr(vendor_assist, "interpret", stub if producer != "rule" else forbidden)
    result = asyncio.run(interpret(request, CTX, use_model=producer != "rule"))
    actual = next(f for f in result.fields if f.field == field)
    if accepted:
        assert actual.status == "valid" and actual.action == "set" and actual.value == value, actual
    else:
        assert not (actual.status == "valid" and actual.action == "set"), actual
        assert not actual.alternatives, actual


@pytest.mark.parametrize(
    "form,field,text,rule_value,expected_status",
    [
        ("period", "start", "공사 기간 화면을 나중에 수정할 수 있는 건가요?", None, "valid"),
        ("period", "end", "여기 입력한 기간은 담당자와 확인할 수 있나요?", None, "valid"),
        ("extra", "title", "담당자에게 확인하고 올게요.", "담당자에게 확인하고 올게요.", "valid"),
        ("period", "excluded", "착공일을 12월 4일로 미뤄 주세요.", None, "valid"),
        ("period", "start", "착공일은 아직 미정이에요.", None, "uncertain"),
        ("period", "end", "종료일을 12월 16일로 변경할지 고민 중입니다.", None, "uncertain"),
        ("quote", "amount", "금액은 450만원으로 변경할지 고민 중입니다.", None, "uncertain"),
    ],
)
def test_no_mention_does_not_create_unsupported_hold(
    monkeypatch, form, field, text, rule_value, expected_status
):
    assert not settings.llm_live
    abstain = FieldIntent(
        field=field, action="keep", value=None, evidence="", intent_kind="no_mention"
    )
    rule = (
        abstain
        if rule_value is None
        else FieldIntent(field=field, action="set", value=rule_value, evidence=text)
    )
    request = AssistRequest(
        request_id="r12-ownership",
        resource_id=UUID(int=179),
        form=form,
        text=text,
        current=CURRENT[form],
        previous_candidate=None,
        rule_fields=[rule],
    )

    async def forbidden(*args, **kwargs):
        raise AssertionError("External provider forbidden")

    async def stub(*args, **kwargs):
        return ModelInterpretation(
            fields=[abstain.model_copy(update={"field": name}) for name in CURRENT[form]]
        )

    monkeypatch.setattr(llm, "structured", forbidden)
    monkeypatch.setattr(vendor_assist, "interpret", stub)
    result = asyncio.run(interpret(request, CTX, use_model=True))
    actual = next(f for f in result.fields if f.field == field)
    if expected_status == "valid":
        assert (
            actual.status,
            actual.action,
            actual.intent_kind,
            actual.value,
            actual.evidence,
        ) == ("valid", "keep", "no_mention", None, ""), actual
        assert actual.origin_request_id == request.request_id
    else:
        assert actual.status in ("uncertain", "invalid") and actual.action == "uncertain", actual
    assert not actual.alternatives, actual
