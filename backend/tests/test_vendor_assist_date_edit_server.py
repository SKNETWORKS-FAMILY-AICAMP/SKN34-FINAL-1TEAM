"""현재 폼 기준 날짜 정정의 보존과 잘못된 이전 참조의 차단."""

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


def _context(form):
    return AssistContext(
        today=date(2026, 11, 23),
        base_month=date(2026, 12, 1),
        required_days=3 if form == "dates" else None,
    )


def _request(form, text, current, previous=None, request_id="new-edit"):
    return AssistRequest(
        request_id=request_id,
        form=form,
        resource_id=RESOURCE,
        text=text,
        current=current,
        previous_candidate=previous,
        rule_fields=[],
    )


def _model_field(field, action="uncertain", evidence=""):
    return FieldIntent(
        field=field,
        action=action,
        value=None,
        evidence=evidence,
        intent_kind="ambiguous" if action == "uncertain" else "no_mention",
    )


def _run(monkeypatch, request, model_fields, *, context=None):
    async def fake_model(_request, _context):
        return ModelInterpretation(fields=model_fields)

    monkeypatch.setattr(vendor_assist, "interpret", fake_model)
    trace = {}
    response = asyncio.run(interpret(request, context or _context(request.form), trace=trace))
    return {item.field: item for item in response.fields}, trace


def _carry(original, text, field, result, history=None):
    return PreviousCandidate(
        request_id=result.origin_request_id,
        text=f"{history}\n{text}" if history else text,
        fields=[
            FieldIntent(
                field=field,
                action="set",
                value=result.value,
                evidence=result.evidence,
                intent_kind="explicit_change",
                value_source="derived",
                correction=result.correction,
            )
        ],
        baselines={field: original[field]},
        origins={field: result.origin_request_id},
        sources={field: result.source},
    )


def test_current_form_date_edit_revalidates_next_turn_without_original_chat(monkeypatch):
    current = {"days": ["2026-12-08", "2026-12-09", "2026-12-10"], "crew": 2}
    text = "현재 폼의 작업일 9일 대신 11일로 변경해 주세요."
    initial, _ = _run(
        monkeypatch,
        _request("dates", text, current),
        [_model_field("days")],
    )
    edited = initial["days"]
    assert (edited.status, edited.source, edited.value_source) == ("valid", "rule", "derived")
    assert edited.value == ["2026-12-08", "2026-12-11", "2026-12-10"]
    assert edited.correction.rejected_value == current["days"]

    previous = _carry(current, text, "days", edited)
    following, trace = _run(
        monkeypatch,
        _request(
            "dates",
            "작업일은 앞서 정정한 대로 유지하고 인원은 기존대로 두세요.",
            current,
            previous=previous,
            request_id="followup",
        ),
        [_model_field("days", "keep")],
    )
    assert trace["previous_rejected"] == {}
    assert [entry["field"] for entry in trace["previous_verified"]["fields"]] == ["days"]
    retained = following["days"]
    assert (retained.status, retained.value, retained.source, retained.origin_request_id) == (
        "valid",
        edited.value,
        "rule",
        "new-edit",
    )


def test_current_form_history_replays_two_sequential_edits(monkeypatch):
    current = {"days": ["2026-12-08", "2026-12-09", "2026-12-10"], "crew": 2}
    first_text = "현재 폼의 작업일 9일 대신 11일로 변경해 주세요."
    first, _ = _run(
        monkeypatch,
        _request("dates", first_text, current, request_id="one"),
        [_model_field("days")],
    )
    previous = _carry(current, first_text, "days", first["days"])
    second_text = "앞에서 말한 작업일 11일 대신 12일로 바꿔 주세요."
    second, trace = _run(
        monkeypatch,
        _request("dates", second_text, current, previous=previous, request_id="two"),
        [_model_field("days")],
    )
    assert second["days"].value == ["2026-12-08", "2026-12-12", "2026-12-10"], trace
    assert second["days"].correction.rejected_value == first["days"].value
    chained = _carry(current, second_text, "days", second["days"], history=first_text)

    third, trace = _run(
        monkeypatch,
        _request("dates", "작업일은 앞서 제안대로 유지해 주세요.", current, previous=chained),
        [
            FieldIntent(
                field="days",
                action="keep",
                value=None,
                evidence="작업일은 앞서 제안대로 유지해 주세요",
                intent_kind="retain_previous",
                value_source="previous",
            )
        ],
    )
    assert trace["previous_rejected"] == {}
    assert third["days"].status == "valid", trace
    assert third["days"].value == second["days"].value
    assert third["days"].source == "rule"


def test_stale_or_missing_prior_reference_never_uses_matching_current_list(monkeypatch):
    current = {"days": ["2026-12-08", "2026-12-09", "2026-12-10"], "crew": 2}
    text = "앞서 제안한 작업일 9일 대신 11일로 변경해 주세요."
    missing, _ = _run(
        monkeypatch,
        _request("dates", text, current),
        [_model_field("days")],
    )
    assert missing["days"].action == "uncertain"

    stale = PreviousCandidate(
        request_id="stale",
        text="작업일 12월 8일, 9일, 10일",
        fields=[
            FieldIntent(
                field="days", action="set", value=current["days"], evidence="12월 8일, 9일, 10일"
            )
        ],
        baselines={"days": []},
    )
    rejected, trace = _run(
        monkeypatch,
        _request("dates", text, current, previous=stale),
        [_model_field("days")],
    )
    assert rejected["days"].action == "uncertain"
    assert trace["previous_rejected"]["days"] == "stale_context"


def test_period_current_list_edit_checks_combined_boundaries(monkeypatch):
    current = {
        "start": "2026-12-02",
        "end": "2026-12-20",
        "excluded": ["2026-12-04", "2026-12-06"],
    }
    text = "현재 폼의 제외일 4일 대신 21일로 바꿔 주세요."
    fields, trace = _run(
        monkeypatch,
        _request("period", text, current),
        [_model_field("excluded"), _model_field("end", "keep")],
    )
    assert fields["excluded"].action == "uncertain"
    assert trace["date_edit_reviewed"] == []
    assert fields["end"].action == "keep"


def test_uncertain_followup_without_new_date_edit_never_forces_retention(monkeypatch):
    current = {
        "start": "2026-12-02",
        "end": "2026-12-20",
        "excluded": ["2026-12-04", "2026-12-06"],
    }
    first_text = "현재 폼의 제외일 4일 대신 5일로 바꿔 주세요."
    first, _ = _run(
        monkeypatch,
        _request("period", first_text, current),
        [_model_field("excluded")],
    )
    previous = _carry(current, first_text, "excluded", first["excluded"])
    fields, trace = _run(
        monkeypatch,
        _request(
            "period",
            "아까 수정한 제외일을 그대로 둬도 될까요?",
            current,
            previous=previous,
            request_id="question",
        ),
        [_model_field("excluded", "uncertain", "그대로 둬도 될까요?")],
    )
    assert trace["previous_verified"] is not None
    assert trace["date_edit_reviewed"] == []
    assert fields["excluded"].action == "uncertain"


@pytest.mark.parametrize(
    "text",
    [
        "지금 제안한 날 중 작업일 9일 대신 11일로 바꿔 주세요.",
        "그 날짜들 중 작업일 9일 대신 11일로 바꿔 주세요.",
        "그 제안 중 작업일 9일 대신 11일로 바꿔 주세요.",
        "그 목록 중 작업일 9일 대신 11일로 바꿔 주세요.",
    ],
)
def test_missing_pending_list_with_prior_reference_does_not_use_current(monkeypatch, text):
    current = {"days": ["2026-12-08", "2026-12-09", "2026-12-10"], "crew": 2}
    fields, trace = _run(monkeypatch, _request("dates", text, current), [_model_field("days")])
    assert fields["days"].action == "uncertain"
    assert trace["date_edit_reviewed"] == []


@pytest.mark.parametrize("use_model", [False, True])
@pytest.mark.parametrize(
    "text,values",
    [
        ("아까 말씀드린 작업일 중 9일만 11일로 옮겨 주세요.", ["2026-12-09", "2026-12-11"]),
        ("작업일 9일 대신 16일로 변경해 주세요.", ["2026-12-09", "2026-12-16"]),
        ("작업일 9일 대신 11일로 변경할까요?", ["2026-12-09", "2026-12-11"]),
    ],
)
def test_failed_replacement_cannot_be_approved_as_plain_list(monkeypatch, use_model, text, values):
    current = {"days": ["2026-12-09", "2026-12-16"], "crew": 2}
    candidate = FieldIntent(field="days", action="set", value=values, evidence=text)
    data = _request("dates", text, current).model_copy(update={"rule_fields": [candidate]})
    context = _context("dates").model_copy(update={"required_days": 2})
    if use_model:
        fields, _ = _run(monkeypatch, data, [candidate], context=context)
    else:
        result = asyncio.run(interpret(data, context, use_model=False))
        fields = {item.field: item for item in result.fields}
    assert fields["days"].action == "uncertain"
    assert fields["days"].status != "valid"
    assert not fields["days"].alternatives


def test_rejected_exclusions_revalidate_remaining_period_boundary(monkeypatch):
    current = {"start": "2026-12-02", "end": "2026-12-24", "excluded": ["2026-12-15"]}
    text = "종료일은 12월 10일로 바꾸고, 제외일은 12월 10일로 변경해 주세요."
    candidates = [
        FieldIntent(
            field="end",
            action="set",
            value="2026-12-10",
            evidence="종료일은 12월 10일로 바꾸고",
            intent_kind="explicit_change",
        ),
        FieldIntent(
            field="excluded",
            action="set",
            value=["2026-12-10"],
            evidence="제외일은 12월 10일로 변경해 주세요",
            intent_kind="explicit_change",
        ),
    ]
    fields, _ = _run(monkeypatch, _request("period", text, current), candidates)
    for field in ("end", "excluded"):
        assert fields[field].action == "uncertain"
        assert fields[field].status != "valid"


@pytest.mark.parametrize("candidate_source", ["llm", "rule"])
def test_singleton_candidate_cannot_drop_other_exclusions(monkeypatch, candidate_source):
    current = {
        "start": "2026-12-02",
        "end": "2026-12-20",
        "excluded": ["2026-12-04", "2026-12-06", "2026-12-08"],
    }
    text = "현재 폼의 제외일 4일 대신 5일로 바꿔 주세요."
    singleton = FieldIntent(
        field="excluded",
        action="set",
        value=["2026-12-05"],
        evidence="5일",
        intent_kind="explicit_change",
    )
    data = _request("period", text, current)
    model = [_model_field("excluded")]
    if candidate_source == "llm":
        model = [singleton]
    else:
        data = data.model_copy(update={"rule_fields": [singleton]})
    fields, trace = _run(monkeypatch, data, model)
    excluded = fields["excluded"]
    assert excluded.status == "valid"
    assert excluded.source == "rule"
    assert excluded.value == ["2026-12-05", "2026-12-06", "2026-12-08"]
    assert trace["date_edit_reviewed"][0]["value_source"] == "derived"


def test_opposed_valid_alternative_is_held_without_partial_overwrite(monkeypatch):
    current = {
        "start": "2026-12-02",
        "end": "2026-12-20",
        "excluded": ["2026-12-04", "2026-12-06"],
    }
    text = "현재 폼의 제외일 4일 대신 5일로 바꿔 주세요."
    contrary = FieldIntent(
        field="excluded",
        action="set",
        value=["2026-12-04"],
        evidence="4일",
        intent_kind="explicit_change",
    )
    fields, _ = _run(monkeypatch, _request("period", text, current), [contrary])
    assert fields["excluded"].action == "uncertain"
    assert fields["excluded"].status == "uncertain"


def test_date_edit_current_reference_ignores_other_field_previous_reference(monkeypatch):
    current = {"days": ["2026-12-08", "2026-12-09", "2026-12-10"], "crew": 2}
    prior_crew = PreviousCandidate(
        request_id="crew-initial",
        text="작업 인원은 3명",
        fields=[FieldIntent(field="crew", action="set", value=3, evidence="3명")],
        baselines={"crew": 2},
    )
    text = "현재 폼의 작업일 9일 대신 11일로 바꿔 주세요. 인원은 이전 제안 유지해 주세요."
    fields, trace = _run(
        monkeypatch,
        _request("dates", text, current, previous=prior_crew),
        [
            _model_field("days"),
            FieldIntent(
                field="crew",
                action="keep",
                value=None,
                evidence="인원은 이전 제안 유지해 주세요",
                intent_kind="retain_previous",
                value_source="previous",
            ),
        ],
    )
    assert trace["date_edit_reviewed"]
    assert fields["days"].value == ["2026-12-08", "2026-12-11", "2026-12-10"]
    assert fields["crew"].value == 3


@pytest.mark.parametrize("third,valid", [("2026-12-12", True), ("2026-12-19", False)])
def test_normalized_first_turn_list_requires_exact_grounding(monkeypatch, third, valid):
    current = {"days": ["2026-12-03", "2026-12-04", "2026-12-05"], "crew": 2}
    evidence = "12월 10일, 11일, 12일"
    prior = PreviousCandidate(
        request_id="first",
        text="작업 날짜는 " + evidence,
        fields=[
            FieldIntent(
                field="days",
                action="set",
                value=["2026-12-10", "2026-12-11", third],
                evidence=evidence,
                value_source="derived",
            )
        ],
        baselines={"days": current["days"]},
    )
    result, _ = _run(
        monkeypatch,
        _request("dates", "지금 제안한 날 중 11일 대신 13일을 넣겠습니다.", current, prior),
        [_model_field("days")],
    )
    assert (result["days"].status == "valid") is valid
    if valid:
        assert result["days"].value == ["2026-12-10", "2026-12-13", "2026-12-12"]


def test_current_date_single_replacement_with_stored_model_relation(monkeypatch):
    current = {"days": ["2026-12-09", "2026-12-15"], "crew": 2}
    utterance = (
        "아, 날짜 하나 잘못 말씀드렸네요. 12월 15일 대신 16일로 고쳐주세요. 9일은 그대로고요."
    )
    replacement = ["2026-12-09", "2026-12-16"]
    fields, trace = _run(
        monkeypatch,
        _request("dates", utterance, current),
        [
            FieldIntent(
                field="days",
                action="set",
                value=replacement,
                evidence="12월 15일 대신 16일로 고쳐주세요. 9일은 그대로고요.",
                intent_kind="explicit_change",
                value_source="utterance",
                correction=CorrectionRelation(
                    kind="replace",
                    rejected_value=["2026-12-15"],
                    rejected_evidence="12월 15일 대신",
                    accepted_evidence="16일로 고쳐주세요",
                ),
            ),
            _model_field("crew", "keep"),
        ],
        context=_context("dates").model_copy(update={"required_days": 2}),
    )
    assert fields["days"].status == "valid", trace
    assert fields["days"].value == replacement
    assert fields["days"].value_source == "derived"
    assert fields["days"].correction.rejected_value == current["days"]
    assert fields["crew"].action == "keep"


def test_previous_dates_shift_preserves_neighbors_with_stored_model_fields(monkeypatch):
    current = {"days": ["2026-12-09", "2026-12-15"], "crew": 2}
    prior_days = ["2026-12-07", "2026-12-14", "2026-12-21"]
    previous = PreviousCandidate(
        request_id="earlier",
        text=(
            "이번 작업은 12월 7일, 14일, 21일 이렇게 세 번 나갑니다. 날짜는 이 세 개로 잡아주세요."
        ),
        fields=[
            FieldIntent(
                field="days",
                action="set",
                value=prior_days,
                evidence="12월 7일, 14일, 21일",
            )
        ],
        sources={"days": "llm"},
        origins={"days": "earlier"},
        baselines={"days": current["days"]},
    )
    utterance = "아까 말한 14일만 하루 밀려서 12월 15일로요. 7일이랑 21일은 그대로입니다."
    fields, trace = _run(
        monkeypatch,
        _request("dates", utterance, current, previous=previous),
        [
            FieldIntent(
                field="days",
                action="set",
                value=["2026-12-07", "2026-12-15", "2026-12-21"],
                evidence="14일만 하루 밀려서 12월 15일로요. 7일이랑 21일은 그대로입니다.",
                intent_kind="explicit_change",
                value_source="utterance",
            ),
            _model_field("crew", "keep"),
        ],
    )
    assert trace["previous_rejected"] == {}
    assert trace["date_edit_reviewed"], trace
    assert fields["days"].status == "valid", trace
    assert fields["days"].value == ["2026-12-07", "2026-12-15", "2026-12-21"]
    assert fields["days"].correction.rejected_value == prior_days
    assert fields["days"].source == "rule"


def test_previous_period_exclusion_replacement_with_stored_model_abstention(monkeypatch):
    current = {"start": "2026-12-05", "end": "2026-12-18", "excluded": []}
    prior_days = ["2026-12-09", "2026-12-16"]
    previous = PreviousCandidate(
        request_id="earlier",
        text="12/9, 12/16 작업 불가입니다",
        fields=[
            FieldIntent(
                field="excluded",
                action="set",
                value=prior_days,
                evidence="12/9, 12/16 작업 불가",
            )
        ],
        sources={"excluded": "llm"},
        origins={"excluded": "earlier"},
        baselines={"excluded": []},
    )
    utterance = "아 16일 대신 17일 쉬는 걸로요 나머진 그대로"
    fields, trace = _run(
        monkeypatch,
        _request("period", utterance, current, previous=previous),
        [
            FieldIntent(
                field="start",
                action="keep",
                value=None,
                evidence="나머진 그대로",
                intent_kind="retain_current",
                value_source="current",
            ),
            FieldIntent(
                field="end",
                action="keep",
                value=None,
                evidence="나머진 그대로",
                intent_kind="retain_current",
                value_source="current",
            ),
            _model_field("excluded", "uncertain", "16일 대신 17일 쉬는 걸로요 나머진 그대로"),
        ],
    )
    assert trace["previous_rejected"] == {}
    assert trace["date_edit_reviewed"], trace
    assert fields["excluded"].status == "valid", trace
    assert fields["excluded"].value == ["2026-12-09", "2026-12-17"]
    assert fields["excluded"].correction.rejected_value == prior_days
    assert fields["excluded"].origin_request_id == "new-edit"


def test_current_model_incorrect_rejected_date_cannot_override_local_list(monkeypatch):
    current = {"days": ["2026-12-09", "2026-12-15"], "crew": 2}
    text = "12월 15일 대신 16일로 고쳐주세요. 9일은 그대로고요."
    fields, trace = _run(
        monkeypatch,
        _request("dates", text, current),
        [
            FieldIntent(
                field="days",
                action="set",
                value=["2026-12-09", "2026-12-16"],
                evidence=text,
                intent_kind="explicit_change",
                correction=CorrectionRelation(
                    kind="replace",
                    rejected_value=["2026-12-09"],
                    rejected_evidence="9일은 그대로",
                    accepted_evidence="16일로 고쳐주세요",
                ),
            )
        ],
        context=_context("dates").model_copy(update={"required_days": 2}),
    )
    assert fields["days"].action == "uncertain", trace
    assert trace["date_edit_reviewed"] == []


def test_stale_previous_date_reference_does_not_fall_back_to_current(monkeypatch):
    current = {"days": ["2026-12-09", "2026-12-15"], "crew": 2}
    previous = PreviousCandidate(
        request_id="earlier",
        text="작업일은 12월 9일, 15일입니다",
        fields=[
            FieldIntent(
                field="days",
                action="set",
                value=current["days"],
                evidence="12월 9일, 15일",
            )
        ],
        baselines={"days": ["2026-12-08", "2026-12-15"]},
    )
    text = "아까 제안한 작업일 15일 대신 16일로 고쳐주세요. 9일은 그대로고요."
    fields, trace = _run(
        monkeypatch,
        _request("dates", text, current, previous=previous),
        [_model_field("days", "uncertain", text)],
        context=_context("dates").model_copy(update={"required_days": 2}),
    )
    assert trace["previous_rejected"]["days"] == "stale_context"
    assert trace["date_edit_reviewed"] == []
    assert fields["days"].status != "valid"


def test_partially_stale_previous_with_valid_crew_cannot_restore_date_edit(monkeypatch):
    current = {"days": ["2026-12-09", "2026-12-15"], "crew": 2}
    previous = PreviousCandidate(
        request_id="earlier",
        text="작업 날짜는 12월 9일, 15일이고 인원은 3명입니다.",
        fields=[
            FieldIntent(
                field="days",
                action="set",
                value=current["days"],
                evidence="12월 9일, 15일",
            ),
            FieldIntent(field="crew", action="set", value=3, evidence="인원은 3명"),
        ],
        baselines={"days": ["2026-12-08", "2026-12-15"], "crew": 2},
    )
    text = "12월 15일 대신 16일로 고쳐주세요. 9일은 그대로고요."
    fields, trace = _run(
        monkeypatch,
        _request("dates", text, current, previous=previous),
        [
            FieldIntent(
                field="days",
                action="set",
                value=["2026-12-09", "2026-12-16"],
                evidence=text,
                intent_kind="explicit_change",
                correction=CorrectionRelation(
                    kind="replace",
                    rejected_value=["2026-12-15"],
                    rejected_evidence="12월 15일 대신",
                    accepted_evidence="16일로 고쳐주세요",
                ),
            ),
            _model_field("crew", "keep"),
        ],
        context=_context("dates").model_copy(update={"required_days": 2}),
    )
    assert trace["previous_rejected"]["days"] == "stale_context"
    assert trace["previous_verified"]["fields"][0]["field"] == "crew"
    assert trace["date_edit_reviewed"] == []
    assert fields["days"].status != "valid"
