"""연결된 전체 날짜 목록은 마지막 절의 부분 목록으로 축소하지 않는다."""

import asyncio
from datetime import date
from uuid import UUID

import pytest

from app.agent import vendor_assist
from app.vendor.assist_schemas import (
    AssistContext,
    AssistRequest,
    FieldIntent,
    ModelInterpretation,
)
from app.vendor.assist_service import interpret

CTX = AssistContext(today=date(2026, 11, 26), base_month=date(2026, 12, 1))
CURRENT = {"start": "2026-12-02", "end": "2026-12-24", "excluded": []}
VALUES = ["2026-12-07", "2026-12-14", "2026-12-21"]


def _set(value, text):
    return FieldIntent(
        field="excluded",
        action="set",
        value=value,
        evidence=text,
        intent_kind="explicit_change",
        value_source="utterance",
    )


def _run(monkeypatch, text, rules, model):
    async def stub(*args, **kwargs):
        return ModelInterpretation(fields=model)

    monkeypatch.setattr(vendor_assist, "interpret", stub)
    request = AssistRequest(
        request_id="h2-calendar",
        resource_id=UUID(int=179),
        form="period",
        text=text,
        current=CURRENT,
        previous_candidate=None,
        rule_fields=rules,
    )
    response = asyncio.run(interpret(request, CTX, use_model=bool(model)))
    return {part.field: part for part in response.fields}


@pytest.mark.parametrize("mode", ["rule", "model", "both"])
@pytest.mark.parametrize(
    "text",
    [
        "12월 7일, 14일, 21일은 현장에 들어갈 수 없으니 세 날짜를 제외일로 지정해 주세요.",
        "12월 7일, 14일, 21일은 방문이 어려우니 3개 날짜를 제외일로 설정해 주세요.",
        "12월 7일, 14일, 21일은 현장에 들어갈 수 없으니 제외일로 지정해 주세요.",
    ],
)
def test_linked_exclusion_list_is_not_truncated(monkeypatch, text, mode):
    # 규칙이 최종 일자만 추출해도 연결된 전체 열거를 검증한다.
    partial = _set(VALUES[-1:], text[text.index("21일") :])
    full = _set(VALUES, text)
    rows = _run(
        monkeypatch,
        text,
        [partial] if mode != "model" else [],
        [full] if mode != "rule" else [],
    )
    result = rows["excluded"]
    assert (result.status, result.action, result.value) == ("valid", "set", VALUES)
    assert rows["start"].action != "set" and rows["end"].action != "set"
    if mode != "rule":
        assert result.source == "llm"


@pytest.mark.parametrize(
    "text",
    [
        "12월 7일, 14일, 21일은 현장에 들어갈 수 없으니 두 날짜를 제외일로 지정해 주세요.",
        "12월 7일, 14일, 21일은 방문이 어려우니 네 날짜를 제외일로 설정해 주세요.",
        "12월 7일, 14일, 21일은 방문이 어려우니 한 날짜를 제외일로 설정해 주세요.",
        "승인되면 12월 7일, 14일, 21일은 세 날짜를 제외일로 지정해 주세요.",
    ],
)
def test_count_or_condition_cannot_authorize_partial_calendar(monkeypatch, text):
    rows = _run(monkeypatch, text, [_set(VALUES[-1:], text)], [_set(VALUES[-1:], text)])
    assert rows["excluded"].action != "set"


def test_unresolved_list_recovery_does_not_override_grounded_model(monkeypatch):
    text = "12월 7일, 14일, 21일은 현장 출입 불가로 제외일로 지정해 주세요."
    result = _run(monkeypatch, text, [], [_set(VALUES, text)])["excluded"]
    assert (result.status, result.action, result.value, result.source) == (
        "valid",
        "set",
        VALUES,
        "llm",
    )
