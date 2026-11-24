"""일자 한 건 이동 및 제외일 전체 해제의 원문·이전 목록 검증."""

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
    PreviousCandidate,
)
from app.vendor.assist_service import interpret

RESOURCE = UUID("00000000-0000-4000-8000-000000000179")


def _run(monkeypatch, form, text, current, *, prior=None, model=None, required_days=None):
    field = "excluded" if form == "period" else "days"
    request = AssistRequest(
        request_id="new-move",
        resource_id=RESOURCE,
        form=form,
        text=text,
        current=current,
        previous_candidate=prior,
        rule_fields=[],
    )

    async def fake_model(_request, _context):
        return ModelInterpretation(
            fields=model
            or [FieldIntent(field=field, action="uncertain", value=None, evidence=text)]
        )

    monkeypatch.setattr(vendor_assist, "interpret", fake_model)
    context = AssistContext(
        today=date(2026, 11, 23),
        base_month=date(2026, 12, 1),
        required_days=required_days,
    )
    trace = {}
    response = asyncio.run(interpret(request, context, trace=trace))
    return {item.field: item for item in response.fields}, trace


def _prior(field, current, text, value, *, history=None):
    return PreviousCandidate(
        request_id="previous",
        text=history + "\n" + text if history else text,
        fields=[FieldIntent(field=field, action="set", value=value, evidence=text)],
        baselines={field: current[field]},
        sources={field: "llm"},
        origins={field: "previous"},
    )


def _carry(field, current, history, text, candidate):
    return PreviousCandidate(
        request_id=candidate.origin_request_id,
        text=history + "\n" + text if history else text,
        fields=[
            FieldIntent(
                field=field,
                action="set",
                value=candidate.value,
                evidence=candidate.evidence,
                value_source="derived",
                correction=candidate.correction,
            )
        ],
        baselines={field: current[field]},
        sources={field: candidate.source},
        origins={field: candidate.origin_request_id},
    )


def _period(excluded):
    return {"start": "2026-12-04", "end": "2026-12-24", "excluded": excluded}


def test_previous_exclusion_move_keeps_neighbour_and_replays_after_other_field(monkeypatch):
    current = _period(["2026-12-09", "2026-12-20"])
    prior = _prior(
        "excluded",
        current,
        "휴무는 12월 9일하고 16일로 잡아 주세요.",
        ["2026-12-09", "2026-12-16"],
        history="마감일은 12월 25일로 변경해 주세요.",
    )
    text = "아, 그중에 9일 쉬는 걸 12월 10일 쉬는 걸로 옮겨 주세요. 다른 휴무는 그대로예요."
    fields, trace = _run(monkeypatch, "period", text, current, prior=prior)
    candidate = fields["excluded"]
    assert trace["previous_rejected"] == {}
    assert (candidate.status, candidate.value, candidate.value_source) == (
        "valid",
        ["2026-12-10", "2026-12-16"],
        "derived",
    )
    assert candidate.correction.rejected_value == prior.fields[0].value
    assert candidate.origin_request_id == "new-move"
    assert current["excluded"] == ["2026-12-09", "2026-12-20"]

    carried = _carry("excluded", current, prior.text, text, candidate)
    third, trace = _run(
        monkeypatch,
        "period",
        "아까 정정한 제외일은 그대로 유지해 주세요.",
        current,
        prior=carried,
        model=[
            FieldIntent(
                field="excluded",
                action="keep",
                value=None,
                evidence="아까 정정한 제외일은 그대로 유지해 주세요.",
                intent_kind="retain_previous",
                value_source="previous",
            )
        ],
    )
    assert trace["previous_rejected"] == {}
    assert third["excluded"].value == candidate.value


def test_previous_exclusion_move_after_manual_boundary_update(monkeypatch):
    current = _period(["2026-12-09"])
    prior = _prior("excluded", current, "휴무는 12월 9일과 17일", ["2026-12-09", "2026-12-17"])
    text = (
        "종료일은 제가 직접 변경했으니 현재 값 유지해 주세요, "
        "아까 정한 휴무 중 17일만 12월 18일로 옮겨 주세요."
    )
    fields, trace = _run(monkeypatch, "period", text, current, prior=prior)
    assert trace["date_edit_reviewed"]
    assert fields["excluded"].value == ["2026-12-09", "2026-12-18"]
    assert fields["end"].action == "keep"


def test_current_workday_postponement_with_anaphor_replays(monkeypatch):
    current = {"days": ["2026-12-09", "2026-12-16"], "crew": 3}
    text = "12월 9일에 건물 행사가 있어요. 그날 공사만 12월 10일로 미뤄 주세요."
    fields, _ = _run(monkeypatch, "dates", text, current, required_days=2)
    candidate = fields["days"]
    assert candidate.status == "valid"
    assert candidate.value == ["2026-12-10", "2026-12-16"]
    assert fields["crew"].action == "keep"
    carried = _carry("days", current, None, text, candidate)
    _, trace = _run(
        monkeypatch, "dates", "작업일은 그대로 둘게요.", current, prior=carried, required_days=2
    )
    assert trace["previous_rejected"] == {}


@pytest.mark.parametrize(
    "text,expected",
    [
        ("아까 말씀드린 작업일 중 9일만 11일로 옮기죠.", ["2026-12-11", "2026-12-16"]),
        ("현재 폼의 작업일 중 9일만 11일로 옮길게요.", ["2026-12-11", "2026-12-20"]),
        ("이전 제안과 현재 폼의 작업일 중 9일만 11일로 옮기죠.", None),
        ("작업일 9일만 11일로 옮기죠.", None),
    ],
)
def test_date_move_resolves_current_and_previous_independently(monkeypatch, text, expected):
    current = {"days": ["2026-12-09", "2026-12-20"], "crew": 2}
    prior = _prior("days", current, "작업일은 12월 9일과 16일", ["2026-12-09", "2026-12-16"])
    fields, _ = _run(monkeypatch, "dates", text, current, prior=prior, required_days=2)
    assert fields["days"].value == expected
    assert (fields["days"].status == "valid") is (expected is not None)


@pytest.mark.parametrize(
    "text",
    [
        "그중에 9일 쉬는 걸 12월 10일 쉬는 걸로 옮겨 주세요.",
        "아까 요청한 휴무 중 9일만 12월 10일로 옮겨 주세요.",
    ],
)
def test_stale_previous_move_cannot_use_matching_current(monkeypatch, text):
    current = _period(["2026-12-09", "2026-12-20"])
    prior = _prior("excluded", current, "휴무는 12월 9일과 16일", ["2026-12-09", "2026-12-16"])
    prior.baselines["excluded"] = ["2026-12-08"]
    fields, trace = _run(monkeypatch, "period", text, current, prior=prior)
    assert trace["previous_rejected"]["excluded"] == "stale_context"
    assert not (
        fields["excluded"].status == "valid" and fields["excluded"].action in ("set", "clear")
    )


@pytest.mark.parametrize(
    "text",
    [
        "현재 제외일 중 9일만 20일로 옮겨 주세요.",  # 중복
        "현재 제외일 중 9일만 24일로 옮겨 주세요.",  # 종료일
        "현재 제외일 중 9일만 32일로 옮겨 주세요.",  # 잘못된 날짜
        "현재 제외일 중 9일만 1월 10일로 옮겨 주세요.",  # 월 경계
        "현재 제외일 중 9일만 10일로 옮길까요?",  # 문의
        "현재 제외일 중 9일만 10일로 옮겨 주세요. 다른 날은 21일로 바꿔 주세요.",
    ],
)
def test_exclusion_move_does_not_release_invalid_or_ambiguous_changes(monkeypatch, text):
    current = _period(["2026-12-09", "2026-12-20"])
    fields, trace = _run(monkeypatch, "period", text, current)
    assert not (
        fields["excluded"].status == "valid" and fields["excluded"].action in ("set", "clear")
    )
    assert (
        not trace["date_edit_reviewed"] or fields["excluded"].reason_code == "combined_constraint"
    )


@pytest.mark.parametrize(
    "text,valid",
    [
        ("기존 제외일은 전부 해제해 주세요.", True),
        ("등록된 제외일은 다 해제해주세요.", True),
        ("기존 제외일은 모두 해제하지 마세요.", False),
        ("기존 제외일 중 9일만 해제해 주세요.", False),
        ("기존 제외일은 모두 해제해 주세요?", False),
        ("기존 제외일은 모두 해제해 주세요. 그 요청은 취소합니다.", False),
    ],
)
def test_explicit_whole_release_is_clear_only(monkeypatch, text, valid):
    current = _period(["2026-12-09", "2026-12-20"])
    fields, _ = _run(
        monkeypatch,
        "period",
        text,
        current,
        model=[FieldIntent(field="excluded", action="clear", value=None, evidence=text)],
    )
    assert (fields["excluded"].action == "clear" and fields["excluded"].status == "valid") is valid
    assert fields["start"].action == fields["end"].action == "keep"
