"""Counted adjacent-date lists: new controls and archived normalization regression."""

import asyncio
from datetime import date

import pytest

from app.agent import vendor_assist
from app.config import settings
from app.vendor.assist_normalize import date_mentions, date_tokens
from app.vendor.assist_schemas import AssistContext, AssistRequest, FieldIntent, ModelInterpretation
from app.vendor.assist_service import interpret

CTX = AssistContext(today=date(2026, 11, 24), base_month=date(2026, 11, 1))


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("작업 12월 13일 14일 이틀로 해주세여 인원 3명", [13, 14]),
        ("공사 12월 8일 19일 두 날짜로 지정합니다", [8, 19]),
        ("작업일 12월 3일 8일 17일 세 날짜로 정해주세요", [3, 8, 17]),
        ("12월 5일 7일 21일 3개 날짜로 설정", [5, 7, 21]),
        ("12월 9일 11일 23일 사흘로 정합니다", [9, 11, 23]),
        ("12월 19일\t23일 2개 일자로 지정", [19, 23]),
    ],
)
def test_counted_adjacent_date_list(text, expected):
    tokens = date_tokens(text, CTX)
    assert [day for _, _, day in tokens] == [date(2026, 12, d) for d in expected]
    assert date_mentions(text, CTX, expand=True) == {date(2026, 12, d) for d in expected}
    assert all(text[a:b].strip().endswith("일") for a, b, _ in tokens)


@pytest.mark.parametrize(
    "text",
    [
        "12월 13일 14일",
        "12월 13일 14일간",
        "12월 13일 14일 동안",
        "12월 13일 14일 사흘로",
        "12월 13일 14일 두 날짜인지 모르겠어요",
        "12월 13일\n14일 이틀로",
        "12월 13일 13일 이틀로",
        "12월 13일 31일 32일 세 날짜로",
        "12월 13일 14명 이틀로",
        "12월 13일 14일 두 날짜 범위",
        "12월 13일부터 14일 이틀로",
        "12월 13일 작업 기간 14일 이틀로",
    ],
)
def test_uncounted_or_ambiguous_whitespace_does_not_supply_second_date(text):
    assert date_tokens(text, CTX) == [(0, len("12월 13일"), date(2026, 12, 13))]


@pytest.mark.parametrize("use_model", [False, True])
@pytest.mark.parametrize(
    ("utterance", "expected"),
    [
        ("작업 12월 13일 14일 이틀로 해주세여 인원 3명", True),
        ("작업 12월 13일 14일간 공사할게요 인원 3명", False),
        ("작업일 12월 13일 14일 이틀로 하면 될까요?", False),
        ("작업일 12월 13일 14일 두 날짜로 확정하지 마세요", False),
        ("작업일 12월 13일 14일 이틀로 정하지는 마세요", False),
        ("작업일 12월 13일 14일 이틀로 안 잡겠습니다", False),
        ("작업일 12월 13일 14일 이틀로 정해주세요. 그 요청은 취소합니다", False),
        ("작업일 12월 13일 14일 이틀로 정해주세요. 인원 변경 요청은 취소합니다", True),
    ],
)
def test_service_independent_counted_list_proof(monkeypatch, use_model, utterance, expected):
    assert not settings.llm_live
    proposed = ["2026-12-13", "2026-12-14"]
    rule = FieldIntent(field="days", action="set", value=proposed, evidence=utterance)
    model = FieldIntent(
        field="days",
        action="set",
        value=proposed,
        evidence=utterance,
        intent_kind="explicit_change",
        value_source="utterance",
    )
    calls = 0

    async def stub(*_):
        nonlocal calls
        calls += 1
        return ModelInterpretation(fields=[model])

    monkeypatch.setattr(vendor_assist, "interpret", stub)
    request = AssistRequest(
        request_id="normalization-proof",
        form="dates",
        resource_id="00000000-0000-4000-8000-000000000179",
        text=utterance,
        current={"days": ["2026-12-09", "2026-12-16"], "crew": 2},
        previous_candidate=None,
        rule_fields=[rule],
    )
    context = CTX.model_copy(update={"base_month": date(2026, 12, 1), "required_days": 2})
    result = asyncio.run(interpret(request, context, use_model=use_model))
    candidate = next(field for field in result.fields if field.field == "days")
    assert calls == int(use_model)
    assert (candidate.status == "valid" and candidate.action == "set") == expected
    if expected:
        assert candidate.value == proposed
    else:
        assert not candidate.alternatives
