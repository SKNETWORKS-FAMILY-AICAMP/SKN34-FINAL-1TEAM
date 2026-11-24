"""기간 종료일 허용·이전 종료일 유지의 독립 검증과 미적용 후보 보존."""

import asyncio
from datetime import date
from uuid import UUID

import pytest

from app.agent import vendor_assist
from app.vendor import assist_service
from app.vendor.assist_schemas import (
    AssistContext,
    AssistRequest,
    FieldIntent,
    ModelInterpretation,
    PreviousCandidate,
)

RESOURCE = UUID("00000000-0000-4000-8000-000000000179")
CURRENT = {"start": "2026-12-05", "end": "2026-12-18", "excluded": []}


def field(name, action="keep", value=None, evidence="", **extras):
    return FieldIntent(field=name, action=action, value=value, evidence=evidence, **extras)


def request(text, *, current=None, previous=None, request_id="boundary-turn"):
    return AssistRequest(
        request_id=request_id,
        form="period",
        resource_id=RESOURCE,
        text=text,
        current=current or CURRENT,
        previous_candidate=previous,
        rule_fields=[field(name) for name in ("start", "end", "excluded")],
    )


def run(monkeypatch, data, model_fields, *, month=date(2026, 12, 1)):
    async def fake_model(_safe_request, _context):
        return ModelInterpretation(fields=model_fields)

    monkeypatch.setattr(vendor_assist, "interpret", fake_model)
    trace = {}
    response = asyncio.run(
        assist_service.interpret(
            data,
            AssistContext(today=date(2026, 11, 23), base_month=month),
            trace=trace,
        )
    )
    return {item.field: item for item in response.fields}, trace, response


def ambiguous_end(evidence):
    return field("end", "uncertain", None, evidence, intent_kind="ambiguous")


def assert_end(rows, expected, origin):
    end = rows["end"]
    assert (end.status, end.action, end.value, end.origin_request_id) == (
        "valid",
        "set",
        expected,
        origin,
    ), end


def carry(rows, response, original, *, earlier=None, text=None):
    accepted = [
        item for item in rows.values() if item.status == "valid" and item.action in ("set", "clear")
    ]
    assert accepted
    return PreviousCandidate(
        request_id=response.request_id,
        text=f"{earlier.text}\n{text}" if earlier else text,
        fields=[field(item.field, item.action, item.value, item.evidence) for item in accepted],
        sources={item.field: item.source for item in accepted},
        origins={item.field: item.origin_request_id or response.request_id for item in accepted},
        baselines={
            item.field: (
                earlier.baselines[item.field]
                if earlier
                and item.field in earlier.baselines
                and item.origin_request_id == earlier.origins.get(item.field, earlier.request_id)
                else original[item.field]
            )
            for item in accepted
        },
    )


def test_affirmative_end_and_two_verified_retentions_preserve_origin(monkeypatch):
    first_text = "26일까지는 일정 잡아도 돼요"
    first, _, response = run(
        monkeypatch,
        request(first_text, request_id="boundary-origin"),
        [ambiguous_end(first_text)],
    )
    assert_end(first, "2026-12-26", "boundary-origin")
    assert first["end"].value_source == "utterance"
    previous = carry(first, response, CURRENT, text=first_text)

    second_text = "끝은 방금 말한 대로 하고 14일 하루만 빼주세요"
    second, second_trace, response = run(
        monkeypatch,
        request(second_text, previous=previous, request_id="boundary-second"),
        [
            ambiguous_end("끝은 방금 말한 대로 하고"),
            field("excluded", "set", ["2026-12-14"], "14일 하루만 빼주세요"),
        ],
    )
    assert_end(second, "2026-12-26", "boundary-origin")
    assert second["end"].intent_kind == "retain_previous"
    assert second["excluded"].value == ["2026-12-14"]
    assert second_trace["previous_verified"]["origins"]["end"] == "boundary-origin"
    previous = carry(second, response, CURRENT, earlier=previous, text=second_text)

    third_text = (
        "쉬는 날은 제가 직접 수정했으니 놔두시고 마지막 날짜는 아까 말씀드린 그대로 부탁합니다."
    )
    third_current = {**CURRENT, "excluded": ["2026-12-15"]}
    third, third_trace, _ = run(
        monkeypatch,
        request(
            third_text,
            current=third_current,
            previous=previous,
            request_id="boundary-third",
        ),
        [
            ambiguous_end("마지막 날짜는 아까 말씀드린 그대로 부탁합니다"),
            field(
                "excluded",
                "keep",
                None,
                "쉬는 날은 제가 직접 수정했으니 놔두시고",
                intent_kind="retain_current",
                value_source="current",
            ),
        ],
    )
    assert_end(third, "2026-12-26", "boundary-origin")
    assert third["end"].evidence == first_text
    assert third["excluded"].action != "set"
    assert third_trace["previous_rejected"]["excluded"] == "stale_context"
    assert third_trace["previous_verified"]["origins"]["end"] == "boundary-origin"


@pytest.mark.parametrize(
    "text",
    [
        "26일까지는 일정 잡아도 될까요?",
        "26일까지는 일정 잡아도 돼요?",
        "26일까지는 일정 잡아도 될 것 같아요",
        "26일까지는 일정 잡아도 되겠죠",
        "26일까지는 일정 잡으면 안 돼요",
        "26일까지는 일정 잡아도 안 돼요",
        "26일까지는 일정 잡아도 돼요, 아니 27일까지요",
        "26일까지는 일정 잡아도 돼요 말고 25일까지",
        "26일까지는 일정 잡아도 돼요. 27일까지도 가능",
        "26일까지는 일정 잡아도 돼요 2명",
        "26일이나 27일까지는 일정 잡아도 돼요",
        "26일까지는 일정 잡아도 돼요 취소할게요",
    ],
)
def test_uncertain_negated_or_competing_deadlines_cannot_be_invented(monkeypatch, text):
    rows, _, _ = run(monkeypatch, request(text), [ambiguous_end(text)])
    assert not (rows["end"].status == "valid" and rows["end"].action == "set")


def test_day_without_month_baseline_does_not_impute_date(monkeypatch):
    text = "26일까지는 일정 잡아도 돼요"
    rows, _, _ = run(monkeypatch, request(text), [ambiguous_end(text)], month=None)
    assert rows["end"].action == "uncertain"


@pytest.mark.parametrize(
    "text",
    [
        "끝은 방금 말한 대로 하지 말고 27일로 바꿔 주세요.",
        "마지막 날짜는 아까 말씀드린 그대로 부탁합니다?",
        "끝은 방금 말한 대로 하는 게 좋을까요?",
        "끝은 방금 말한 대로 하고 마지막 날짜는 27일까지 바꿔 주세요.",
        "끝은 방금 말한 대로 하고 27일까지 일정 잡아도 돼요.",
        "끝은 지금 폼 값 그대로 두고 14일만 빼주세요",
        "끝은 방금 말한 대로 하고 싶지 않아요",
        "끝은 방금 말한 대로 해 주세요라고 썼지만 그 요청은 거둘게요",
        "지난번엔 끝은 방금 말한 대로 하고 14일 하루만 빼주세요라고 했어요",
    ],
)
def test_prior_retention_requires_unambiguous_affirmative_scope(monkeypatch, text):
    origin = "26일까지는 일정 잡아도 돼요"
    first, _, response = run(
        monkeypatch, request(origin, request_id="origin"), [ambiguous_end(origin)]
    )
    previous = carry(first, response, CURRENT, text=origin)
    rows, _, _ = run(
        monkeypatch,
        request(text, previous=previous),
        [ambiguous_end(text)],
    )
    assert rows["end"].action == "uncertain"


def test_previous_end_does_not_survive_manual_change_or_missing_history(monkeypatch):
    origin = "26일까지는 일정 잡아도 돼요"
    first, _, response = run(
        monkeypatch, request(origin, request_id="origin"), [ambiguous_end(origin)]
    )
    previous = carry(first, response, CURRENT, text=origin)
    text = "끝은 방금 말한 대로 하고 14일 하루만 빼주세요"
    stale = {**CURRENT, "end": "2026-12-20"}
    rows, trace, _ = run(
        monkeypatch, request(text, current=stale, previous=previous), [ambiguous_end(text)]
    )
    assert rows["end"].action == "uncertain"
    assert trace["previous_rejected"]["end"] == "stale_context"
    rows, _, _ = run(monkeypatch, request(text), [ambiguous_end(text)])
    assert rows["end"].action == "uncertain"


def test_previous_end_with_unverified_original_text_is_not_reused(monkeypatch):
    previous = PreviousCandidate(
        request_id="origin",
        text="일정을 아직 모르겠어요",
        fields=[field("end", "set", "2026-12-26", "26일까지는 일정 잡아도 돼요")],
        baselines={"end": "2026-12-18"},
        origins={"end": "origin"},
    )
    text = "끝은 방금 말한 대로 하고 14일 하루만 빼주세요"
    rows, trace, _ = run(monkeypatch, request(text, previous=previous), [ambiguous_end(text)])
    assert rows["end"].action == "uncertain"
    assert trace["previous_rejected"]["end"] == "evidence_missing"


def test_previous_speculative_date_is_not_end_retention_evidence(monkeypatch):
    original = "26일까지는 일정 잡아도 될 것 같아요"
    previous = PreviousCandidate(
        request_id="origin",
        text=original,
        fields=[field("end", "set", "2026-12-26", original)],
        baselines={"end": "2026-12-18"},
        origins={"end": "origin"},
    )
    text = "끝은 방금 말한 대로 하고 14일 하루만 빼주세요"
    rows, trace, _ = run(monkeypatch, request(text, previous=previous), [ambiguous_end(text)])
    # 타입·달력 검증만 통과한 오래된 추측은 명시적 확정 제안이 아니다.
    assert trace["previous_verified"] is not None
    assert rows["end"].action == "uncertain"
