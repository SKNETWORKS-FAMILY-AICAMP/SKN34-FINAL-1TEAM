"""날짜 한 원소 정정의 서비스 경로: 불확실한 모델·이전 후보·수동 수정.

실제 업체 도우미 서비스에 요청을 보내고 모델 어댑터만 고정한다.
테스트는 화면에 적용할 수 있는 최종 상태만 검증한다.
"""

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
    PreviousCandidate,
)
from app.vendor.assist_service import interpret

RESOURCE = UUID("00000000-0000-4000-8000-000000000179")
EXCLUDED = ["2026-12-04", "2026-12-06", "2026-12-08"]
DAYS = ["2026-12-08", "2026-12-09", "2026-12-10"]


def _field(name, action, value=None, evidence="", **options):
    return FieldIntent(field=name, action=action, value=value, evidence=evidence, **options)


def _uncertain(name, evidence=""):
    return _field(name, "uncertain", evidence=evidence, intent_kind="ambiguous")


def _untouched(name):
    return _field(name, "keep", intent_kind="no_mention")


def _retain(name, evidence):
    return _field(
        name, "keep", evidence=evidence, intent_kind="retain_previous", value_source="previous"
    )


def _context(form):
    return AssistContext(
        today=date(2026, 11, 23),
        base_month=date(2026, 12, 1),
        required_days=3 if form == "dates" else None,
        days_max=30,
        crew_max=6,
    )


def _request(form, text, current, *, name="date-edit-turn", previous=None, rules=None):
    return AssistRequest(
        request_id=name,
        resource_id=RESOURCE,
        form=form,
        text=text,
        current=current,
        previous_candidate=previous,
        rule_fields=rules or [],
    )


def _interpret(monkeypatch, data, model_fields):
    calls = []

    async def fake_model(safe_request, _context):
        calls.append(safe_request)
        return ModelInterpretation(fields=model_fields)

    monkeypatch.setattr(vendor_assist, "interpret", fake_model)
    response = asyncio.run(interpret(data, _context(data.form)))
    assert len(calls) == 1
    assert response.source == "llm"
    assert response.request_id == data.request_id
    return response


def _fields(response):
    return {item.field: item for item in response.fields}


def _project(current, response):
    """화면에서 사용자 승인 후 반영되는 값만 상태에 합친다."""
    state = {
        key: value.copy() if isinstance(value, list) else value for key, value in current.items()
    }
    for item in response.fields:
        if item.status == "valid" and item.action == "set":
            state[item.field] = item.value
        elif item.status == "valid" and item.action == "clear":
            state[item.field] = None
    return state


def _assert_applied(response, current, field, expected_value, expected_state):
    item = _fields(response)[field]
    assert (item.status, item.action, item.value) == ("valid", "set", expected_value), item
    assert _project(current, response) == expected_state


def _assert_held(response, current, field):
    item = _fields(response)[field]
    assert not (item.status == "valid" and item.action in ("set", "clear")), item
    assert _project(current, response)[field] == current[field]


def _period_current(*, excluded=None, end="2026-12-20"):
    return {
        "start": "2026-12-02",
        "end": end,
        "excluded": list(EXCLUDED if excluded is None else excluded),
    }


def _period_prior():
    return PreviousCandidate(
        request_id="period-initial",
        text="12월 2일부터 12월 20일까지 가능, 제외일은 12월 4일, 12월 6일, 12월 8일",
        fields=[
            _field("end", "set", "2026-12-20", "12월 20일까지"),
            _field("excluded", "set", EXCLUDED.copy(), "제외일은 12월 4일, 12월 6일, 12월 8일"),
        ],
        baselines={"end": "2026-12-22", "excluded": []},
        sources={"end": "rule", "excluded": "llm"},
        origins={"end": "period-end-original", "excluded": "period-excluded-original"},
    )


def _dates_prior():
    return PreviousCandidate(
        request_id="dates-initial",
        text="작업일은 12월 8일, 12월 9일, 12월 10일입니다. 작업 인원은 2명입니다.",
        fields=[
            _field("days", "set", DAYS.copy(), "작업일은 12월 8일, 12월 9일, 12월 10일"),
            _field("crew", "set", 2, "작업 인원은 2명"),
        ],
        baselines={"days": [], "crew": 1},
        sources={"days": "llm", "crew": "rule"},
        origins={"days": "dates-original", "crew": "crew-original"},
    )


def _corrected_prior(prior, text, name, value):
    """클라이언트가 이전 정정 후보를 보존하여 다시 보내는 공개 계약."""
    field_name = "excluded" if name == "period" else "days"
    old_values = EXCLUDED if name == "period" else DAYS
    old = "4일" if name == "period" else "9일"
    new = "5일" if name == "period" else "11일"
    fields = [item for item in prior.fields if item.field != field_name]
    fields.append(
        _field(
            field_name,
            "set",
            value,
            f"{old} 대신 {new}",
            value_source="derived",
            correction=CorrectionRelation(
                kind="replace",
                rejected_value=old_values.copy(),
                rejected_evidence=old,
                accepted_evidence=new,
            ),
        )
    )
    return prior.model_copy(
        update={
            "text": prior.text + "\n" + text,
            "fields": fields,
            "sources": {**prior.sources, field_name: "rule"},
            "origins": {**prior.origins, field_name: f"{name}-edit"},
        }
    )


def _carry(response, utterance, current, previous):
    """검증된 보류 후보를 다음 턴의 공개 요청 계약으로 직렬화한다."""
    accepted = [
        item
        for item in response.fields
        if item.status == "valid" and item.action in ("set", "clear")
    ]
    assert accepted
    return PreviousCandidate(
        request_id=response.request_id,
        text=previous.text + "\n" + utterance,
        fields=[
            _field(
                item.field,
                item.action,
                item.value,
                item.evidence,
                **(
                    {"value_source": "derived", "correction": item.correction}
                    if item.value_source == "derived"
                    else {}
                ),
            )
            for item in accepted
        ],
        baselines={
            item.field: (
                previous.baselines[item.field]
                if item.origin_request_id == previous.origins.get(item.field, previous.request_id)
                and item.field in previous.baselines
                else current[item.field]
            )
            for item in accepted
        },
        sources={item.field: item.source for item in accepted},
        origins={item.field: item.origin_request_id or response.request_id for item in accepted},
    )


def test_period_current_excluded_replaces_one_day_and_preserves_end(monkeypatch):
    current = _period_current(excluded=["2026-12-04", "2026-12-06"])
    utterance = "제외일은 4일 대신 5일로 변경해 주세요."
    data = _request("period", utterance, current)
    response = _interpret(
        monkeypatch,
        data,
        [_uncertain("excluded", utterance), _untouched("start"), _untouched("end")],
    )
    _assert_applied(
        response,
        current,
        "excluded",
        ["2026-12-05", "2026-12-06"],
        {**current, "excluded": ["2026-12-05", "2026-12-06"]},
    )


def test_verified_prior_can_still_retain_period_end_and_exclusions(monkeypatch):
    current = _period_current(excluded=[], end="2026-12-22")
    response = _interpret(
        monkeypatch,
        _request("period", "나머지 설명을 부탁드립니다.", current, previous=_period_prior()),
        [_untouched("start"), _untouched("end"), _untouched("excluded")],
    )
    assert _project(current, response) == {**current, "end": "2026-12-20", "excluded": EXCLUDED}


def test_period_prior_excluded_single_edit_with_unrelated_day_and_third_turn(monkeypatch):
    current = _period_current(excluded=[], end="2026-12-22")
    prior = _period_prior()
    utterance = "제외일은 4일 대신 5일로 바꿔 주세요. 8일은 그대로 제외해 주세요."
    second = _interpret(
        monkeypatch,
        _request("period", utterance, current, name="period-edit", previous=prior),
        [_uncertain("excluded", "4일 대신 5일"), _untouched("start"), _untouched("end")],
    )
    expected = {
        **current,
        "end": "2026-12-20",
        "excluded": ["2026-12-05", "2026-12-06", "2026-12-08"],
    }
    _assert_applied(second, current, "excluded", expected["excluded"], expected)

    retained = _carry(second, utterance, current, prior)
    third_text = "제외일은 앞에서 정정한 대로 유지하고, 종료일도 이전 제안 그대로 유지해 주세요."
    third = _interpret(
        monkeypatch,
        _request("period", third_text, current, name="period-third", previous=retained),
        [
            _retain("excluded", "제외일은 앞에서 정정한 대로 유지"),
            _retain("end", "종료일도 이전 제안 그대로 유지"),
            _untouched("start"),
        ],
    )
    _assert_applied(third, current, "excluded", expected["excluded"], expected)


def test_period_derived_correction_is_rechecked_on_third_turn(monkeypatch):
    current = _period_current(excluded=[], end="2026-12-22")
    correction_text = "제외일은 4일 대신 5일로 바꿔 주세요. 8일은 그대로 제외해 주세요."
    prior = _corrected_prior(
        _period_prior(),
        correction_text,
        "period",
        ["2026-12-05", "2026-12-06", "2026-12-08"],
    )
    text = "제외일은 앞서 정정한 대로 유지하고 종료일도 이전 제안대로 유지해 주세요."
    response = _interpret(
        monkeypatch,
        _request("period", text, current, previous=prior, name="period-third-direct"),
        [
            _retain("excluded", "제외일은 앞서 정정한 대로 유지"),
            _retain("end", "종료일도 이전 제안대로 유지"),
        ],
    )
    _assert_applied(
        response,
        current,
        "excluded",
        ["2026-12-05", "2026-12-06", "2026-12-08"],
        {**current, "end": "2026-12-20", "excluded": ["2026-12-05", "2026-12-06", "2026-12-08"]},
    )


def test_dates_prior_one_day_edit_preserves_crew_through_followup(monkeypatch):
    current = {"days": [], "crew": 1}
    prior = _dates_prior()
    utterance = "작업일은 9일 대신 11일로 바꿔 주세요. 인원은 앞서 정한 그대로 유지해 주세요."
    second = _interpret(
        monkeypatch,
        _request("dates", utterance, current, name="dates-edit", previous=prior),
        [
            _uncertain("days", "9일 대신 11일"),
            _retain("crew", "인원은 앞서 정한 그대로 유지"),
        ],
    )
    expected = {"days": ["2026-12-08", "2026-12-11", "2026-12-10"], "crew": 2}
    _assert_applied(second, current, "days", expected["days"], expected)

    retained = _carry(second, utterance, current, prior)
    third_text = "작업 날짜와 인원 모두 앞서 정리한 안으로 유지해 주세요."
    third = _interpret(
        monkeypatch,
        _request("dates", third_text, current, name="dates-third", previous=retained),
        [
            _retain("days", "작업 날짜와 인원 모두 앞서 정리한 안으로 유지"),
            _retain("crew", "작업 날짜와 인원 모두 앞서 정리한 안으로 유지"),
        ],
    )
    _assert_applied(third, current, "days", expected["days"], expected)


def test_verified_prior_can_still_retain_dates_and_crew(monkeypatch):
    current = {"days": [], "crew": 1}
    response = _interpret(
        monkeypatch,
        _request("dates", "내용을 다시 살펴 주세요.", current, previous=_dates_prior()),
        [_untouched("days"), _untouched("crew")],
    )
    assert _project(current, response) == {"days": DAYS, "crew": 2}, _fields(response)


def test_dates_derived_correction_retains_crew_on_third_turn(monkeypatch):
    current = {"days": [], "crew": 1}
    prior = _corrected_prior(
        _dates_prior(),
        "작업일은 9일 대신 11일로 바꿔 주세요.",
        "dates",
        ["2026-12-08", "2026-12-11", "2026-12-10"],
    )
    text = "작업 날짜와 인원 모두 앞서 정리한 안으로 유지해 주세요."
    response = _interpret(
        monkeypatch,
        _request("dates", text, current, name="dates-third-direct", previous=prior),
        [
            _retain("days", text),
            _retain("crew", text),
        ],
    )
    _assert_applied(
        response,
        current,
        "days",
        ["2026-12-08", "2026-12-11", "2026-12-10"],
        {"days": ["2026-12-08", "2026-12-11", "2026-12-10"], "crew": 2},
    )


@pytest.mark.parametrize(
    "utterance",
    [
        "제외일 4일이나 6일 대신 5일로 변경해 주세요.",
        "제외일 4일 대신 5일이나 7일로 변경해 주세요.",
        "제외일 4일 대신 5일, 6일 대신 7일로 바꿔 주세요.",
        "4일 대신 5일로 변경해 주세요.",
        "종료일 4일 대신 5일로 변경해 주세요.",
        "제외일은 아마 4일 대신 5일로 바꿀 것 같아요.",
        "제외일 4일 대신 5일로 바꿀까요?",
        "제외일 4일 대신 5일로 바꾸지 마세요.",
        "제외일은 4일 대신 32일로 변경해 주세요.",
    ],
)
def test_period_unsafe_or_ambiguous_words_do_not_change_list(monkeypatch, utterance):
    current = _period_current()
    response = _interpret(
        monkeypatch,
        _request("period", utterance, current),
        [_uncertain("excluded", utterance)],
    )
    _assert_held(response, current, "excluded")
    assert _project(current, response)["end"] == current["end"]


@pytest.mark.parametrize(
    "old_text,prior_days",
    [
        (
            "제외일은 12월 4일만 있습니다.",
            ["2026-12-04", "2026-12-06"],
        ),
        (
            "제외일은 12월 4일, 12월 25일입니다.",
            ["2026-12-04", "2026-12-25"],
        ),
    ],
)
def test_period_invalid_previous_candidate_cannot_supply_replacement(
    monkeypatch, old_text, prior_days
):
    current = _period_current(excluded=[])
    prior = PreviousCandidate(
        request_id="forged-period",
        text=old_text,
        fields=[_field("excluded", "set", prior_days, old_text)],
        baselines={"excluded": []},
    )
    utterance = "제외일 4일 대신 5일로 변경해 주세요."
    response = _interpret(
        monkeypatch,
        _request("period", utterance, current, previous=prior),
        [_uncertain("excluded", utterance)],
    )
    _assert_held(response, current, "excluded")


def test_period_two_matching_old_days_in_different_months_are_ambiguous(monkeypatch):
    current = {
        "start": "2026-12-01",
        "end": "2027-01-20",
        "excluded": ["2026-12-04", "2027-01-04"],
    }
    utterance = "제외일 4일 대신 5일로 수정해 주세요."
    response = _interpret(
        monkeypatch,
        _request("period", utterance, current),
        [_uncertain("excluded", utterance)],
    )
    _assert_held(response, current, "excluded")


def test_period_stale_prior_after_manual_excluded_edit_cannot_replace(monkeypatch):
    prior = _period_prior()
    current = _period_current(excluded=["2026-12-12"], end="2026-12-22")
    utterance = "제외일은 4일 대신 5일로 바꿔 주세요."
    response = _interpret(
        monkeypatch,
        _request("period", utterance, current, previous=prior),
        [_uncertain("excluded", utterance), _untouched("end")],
    )
    _assert_held(response, current, "excluded")
    # 수동으로 변경하지 않은 별도 필드의 이전 제안은 독립적으로 유지된다.
    assert _project(current, response)["end"] == "2026-12-20"


def test_dates_stale_prior_after_manual_days_edit_keeps_crew(monkeypatch):
    current = {"days": ["2026-12-12", "2026-12-13", "2026-12-14"], "crew": 1}
    utterance = "작업일 9일 대신 11일로 바꾸고 인원은 기존 제안 그대로 유지해 주세요."
    response = _interpret(
        monkeypatch,
        _request("dates", utterance, current, previous=_dates_prior()),
        [_uncertain("days", "9일 대신 11일"), _retain("crew", "인원은 기존 제안 그대로 유지")],
    )
    _assert_held(response, current, "days")
    assert _project(current, response)["crew"] == 2
