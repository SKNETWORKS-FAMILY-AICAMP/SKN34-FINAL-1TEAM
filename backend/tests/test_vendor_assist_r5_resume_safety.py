"""R5 재개: 날짜 연장·단축의 정중한 실행 요청과 문의·부정 구분."""

import asyncio
from datetime import date
from uuid import UUID

import pytest

from app.agent import vendor_assist
from app.config import settings
from app.vendor.assist_intent_safety import date_set_is_inquiry
from app.vendor.assist_schemas import AssistContext, AssistRequest, FieldIntent, ModelInterpretation
from app.vendor.assist_service import interpret

CTX = AssistContext(today=date(2026, 11, 23), base_month=date(2026, 12, 1), crew_max=6, days_max=30)
CURRENT = {"amount": 2820000, "vat": "excluded", "days": 5, "valid": "2026-12-18"}


@pytest.mark.parametrize(
    "form,field,text",
    [
        ("quote", "valid", "견적 유효기간은 12월 30일까지로 늘려 주시겠어요?"),
        ("quote", "valid", "견적 유효기간을 12월 30일까지 연장해 주실 수 있을까요?"),
        ("quote", "valid", "유효기간을 12월 15일까지로 줄여 주시겠습니까?"),
        ("quote", "days", "공사 일수를 3일로 단축해 주실래요?"),
        ("period", "end", "종료일을 12월 30일까지로 늘려주시겠어요?"),
        ("period", "end", "종료일을 12월 15일까지로 줄여 주시겠어요?"),
    ],
)
def test_polite_date_change_is_an_execution_request(form, field, text):
    assert not date_set_is_inquiry(form, field, text)


@pytest.mark.parametrize(
    "suffix",
    [
        "늘려도 될까요?",
        "연장할 수 있는지 확인 부탁합니다",
        "연장 가능한지 확인해 주시겠어요?",
        "가능하면 늘려 주시겠어요?",
        "늘리지 마세요",
        "늘리지는 마세요",
        "연장하지 않겠습니다",
        "안 늘리겠습니다",
        "안 늘려 주시겠어요?",
        "안 연장해 주시겠어요?",
        "못 늘려 주시겠어요?",
        "늘려 주시겠어요? 그 요청은 취소합니다",
    ],
)
def test_polite_date_change_does_not_hide_a_question_or_denial(suffix):
    text = "견적 유효기간을 12월 30일까지로 " + suffix
    assert date_set_is_inquiry("quote", "valid", text, "12월 30일")


def test_independent_inquiry_does_not_prevent_polite_explicit_extension():
    text = "공사 일수는 5일로 해도 될까요? 유효기간은 12월 30일까지 늘려 주시겠어요?"
    assert date_set_is_inquiry("quote", "days", text)
    assert not date_set_is_inquiry("quote", "valid", text)


def _run(monkeypatch, text, value, *, use_model, evidence=None):
    assert not settings.llm_live
    # 동결 fresh40b의 모델 근거는 정중 종결 앞에서 잘려 있다.
    field = FieldIntent(
        field="valid",
        action="set",
        value=value,
        evidence=evidence or text,
        intent_kind="explicit_change",
        value_source="utterance",
    )
    calls = []

    async def stub(*args):
        calls.append(args)
        return ModelInterpretation(fields=[field])

    monkeypatch.setattr(vendor_assist, "interpret", stub)
    request = AssistRequest(
        request_id="r5-resume-valid",
        resource_id=UUID("f686e57c-b7c1-5463-b599-7fb004659307"),
        form="quote",
        text=text,
        current=CURRENT,
        previous_candidate=None,
        rule_fields=[field.model_copy(update={"evidence": text})],
    )
    trace = {}
    result = asyncio.run(interpret(request, CTX, use_model=use_model, trace=trace))
    assert len(calls) == int(use_model)
    return next(f for f in result.fields if f.field == "valid"), trace


@pytest.mark.parametrize("use_model", [True, False])
def test_fresh40b_validity_extension_replay(monkeypatch, use_model):
    text = "발주처 검토가 늦어진다네요. 견적 유효기간은 12월 30일까지로 늘려 주시겠어요?"
    candidate, trace = _run(
        monkeypatch,
        text,
        "2026-12-30",
        use_model=use_model,
        evidence="견적 유효기간은 12월 30일까지로 늘려",
    )
    assert (candidate.status, candidate.action, candidate.value) == (
        "valid",
        "set",
        "2026-12-30",
    ), trace
    assert not candidate.alternatives


@pytest.mark.parametrize("use_model", [True, False])
@pytest.mark.parametrize(
    "suffix",
    [
        "늘려도 될까요?",
        "늘리지는 마세요",
        "안 늘리겠습니다",
        "안 늘려 주시겠어요?",
        "가능하면 연장해 주실 수 있을까요?",
        "늘려 주시겠어요? 그 요청은 취소합니다",
    ],
)
def test_full_request_guards_survive_clipped_evidence(monkeypatch, suffix, use_model):
    text = "견적 유효기간을 12월 30일까지로 " + suffix
    candidate, trace = _run(
        monkeypatch, text, "2026-12-30", use_model=use_model, evidence="12월 30일"
    )
    assert candidate.status in ("invalid", "uncertain"), trace
    assert candidate.value is None and not candidate.alternatives
