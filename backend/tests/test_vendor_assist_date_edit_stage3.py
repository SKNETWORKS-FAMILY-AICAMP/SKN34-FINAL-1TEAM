"""3단계 날짜 교체: 보호 날짜, 연결형 유지, 출처 및 업무 검증."""

import asyncio
from datetime import date
from uuid import UUID

import pytest

from app.agent import vendor_assist
from app.vendor.assist_date_edit import validated_date_edit
from app.vendor.assist_schemas import (
    AssistContext,
    AssistRequest,
    CorrectionRelation,
    FieldIntent,
    ModelInterpretation,
    PreviousCandidate,
)
from app.vendor.assist_service import interpret

CTX = AssistContext(today=date(2026, 11, 24), base_month=date(2026, 12, 1), required_days=2)
CURRENT = {"days": ["2026-12-09", "2026-12-16"], "crew": 2}


@pytest.mark.parametrize(
    "text",
    [
        "현재 입력한 9일은 건드리지 말고 작업일 16일만 23일로 교체해 주세요.",
        "작업일 9일은 손대지 말고, 16일 대신 23일로 바꿔 주세요.",
        "작업일 16일 대신 23일로 변경해 주세요. 9일은 건드리지 마세요.",
        "작업일 9일은 변경하지 말고, 16일 대신 23일로 옮겨 주세요.",
        "16일만 23일로 옮기고, 인원은 아까 제안한 값대로 유지해 주세요.",
        "16일만 23일로 교체하고 인원은 현재 값 그대로 유지해 주세요.",
        "16일만 23일로 바꾸고, 작업 인원은 그대로 두세요.",
    ],
)
def test_protected_and_connected_replacement(text):
    before = CURRENT["days"].copy()
    assert validated_date_edit("days", text, before, CTX, require_affirmative=True) == [
        "2026-12-09",
        "2026-12-23",
    ]
    assert before == CURRENT["days"]


@pytest.mark.parametrize(
    "text",
    [
        "16일은 건드리지 말고, 16일 대신 23일로 변경해 주세요.",
        "16일은 그대로 두고, 16일 대신 23일로 변경해 주세요.",
        "8일은 건드리지 말고, 16일 대신 23일로 변경해 주세요.",
        "9일은 건드리지 말고, 16일 대신 9일로 변경해 주세요.",
        "9일은 건드리지 말고, 16일 대신 32일로 변경해 주세요.",
        "9일은 건드리지 말고, 16일 대신 23일로 변경할까요?",
        "9일은 건드리지 말고 싶지만 아직 미정, 16일 대신 23일로 변경해 주세요.",
        "16일만 23일로 옮기고, 인원은 3명으로 변경해 주세요.",
        "16일만 23일로 옮기고, 인원은 유지할지 고민 중입니다.",
        "16일만 23일로 옮기고, 인원은 그대로 두세요. 작업일은 취소합니다.",
        "16일만 23일로 옮기고, 작업일은 9일 대신 10일로 바꿔 주세요.",
        "16일만 23일로 옮기고, 인원은 현재 값 그대로 두지 마세요.",
        "16일만 23일로 옮기고, 인원은 그대로 둘 수 있는지 확인해 주세요.",
        "16일만 23일로 옮기고, 다른 날짜도 그대로 둡니다.",
        "16일만 23일로 옮기고",
        "9일은 건드리지 말고, 16일 대신 23일로 변경해 주세요. 9일은 10일로 변경해 주세요.",
    ],
)
def test_competing_or_unconfirmed_date_edits_are_held(text):
    assert validated_date_edit("days", text, CURRENT["days"], CTX, require_affirmative=True) is None


@pytest.mark.parametrize(
    "context",
    [
        CTX.model_copy(update={"required_days": 3}),
        CTX.model_copy(update={"days_max": 1}),
        CTX.model_copy(update={"holidays": [date(2026, 12, 23)]}),
        CTX.model_copy(update={"today": date(2026, 12, 9)}),
    ],
)
def test_protection_does_not_bypass_business_constraints(context):
    assert (
        validated_date_edit(
            "days",
            "9일은 건드리지 말고, 16일 대신 23일로 변경해 주세요.",
            CURRENT["days"],
            context,
            require_affirmative=True,
        )
        is None
    )


def prior_dates(*, stale=False):
    old = ["2026-12-09", "2026-12-16"]
    if stale:
        old = ["2026-12-08", "2026-12-16"]
    return PreviousCandidate(
        request_id="old",
        text="작업일은 12월 7일과 16일, 인원은 3명으로 제안해 주세요.",
        fields=[
            FieldIntent(
                field="days",
                action="set",
                value=["2026-12-07", "2026-12-16"],
                evidence="12월 7일과 16일",
            ),
            FieldIntent(field="crew", action="set", value=3, evidence="3명"),
        ],
        baselines={"days": old, "crew": 2},
        sources={"days": "llm", "crew": "rule"},
        origins={"days": "old-days", "crew": "old-crew"},
    )


def run(
    monkeypatch,
    text,
    *,
    current=CURRENT,
    previous=None,
    fields=None,
    rules=None,
    mode="model",
    context=CTX,
    form="dates",
):
    calls = []

    async def model(data, _context):
        calls.append(data)
        if mode == "fallback":
            raise RuntimeError("offline test")
        return ModelInterpretation(fields=fields or [])

    monkeypatch.setattr(vendor_assist, "interpret", model)
    request = AssistRequest(
        request_id="date-edit-stage3",
        resource_id=UUID(int=179),
        form=form,
        text=text,
        current=current,
        previous_candidate=previous,
        rule_fields=rules or [],
    )
    trace = {}
    result = asyncio.run(interpret(request, context, use_model=mode != "rules", trace=trace))
    assert len(calls) == int(mode != "rules")
    assert result.source == ("llm" if mode == "model" else "rule"), trace
    return {item.field: item for item in result.fields}, trace


@pytest.mark.parametrize("mode", ["model", "rules", "fallback"])
def test_current_source_protection_preserves_actual_form(monkeypatch, mode):
    text = "현재 입력한 9일은 건드리지 말고, 작업일 16일만 23일로 교체해 주세요."
    fields, _ = run(monkeypatch, text, previous=prior_dates(), mode=mode)
    assert fields["days"].status == "valid"
    assert fields["days"].value == ["2026-12-09", "2026-12-23"]
    assert fields["days"].correction.rejected_value == CURRENT["days"]
    assert fields["days"].value_source == "derived"


def test_previous_connected_edit_and_independent_retention(monkeypatch):
    text = "앞서 제안한 작업일 중 16일만 23일로 옮기고, 인원은 아까 제안한 값대로 유지해 주세요."
    relation = CorrectionRelation(
        kind="replace",
        rejected_value=["2026-12-16"],
        rejected_evidence="16일",
        accepted_evidence="23일",
    )
    fields, trace = run(
        monkeypatch,
        text,
        previous=prior_dates(),
        fields=[
            FieldIntent(
                field="days",
                action="set",
                value=["2026-12-23"],
                evidence="16일만 23일로 옮기고",
                correction=relation,
            ),
            FieldIntent(
                field="crew",
                action="keep",
                value=None,
                evidence="인원은 아까 제안한 값대로 유지해 주세요",
                intent_kind="retain_previous",
                value_source="previous",
            ),
        ],
    )
    assert fields["days"].status == "valid", trace
    assert fields["days"].value == ["2026-12-07", "2026-12-23"]
    assert fields["days"].correction.rejected_value == ["2026-12-07", "2026-12-16"]
    assert fields["crew"].status == "valid"
    assert fields["crew"].value == 3
    assert fields["crew"].origin_request_id == "old-crew"


@pytest.mark.parametrize(
    "text,stale",
    [
        ("앞서 제안한 7일은 건드리지 말고, 작업일 16일만 23일로 교체해 주세요.", True),
        ("현재 입력한 9일은 건드리지 말고, 앞서 제안한 작업일 16일만 23일로 교체해 주세요.", False),
        ("작업일 16일만 23일로 옮기고, 인원은 이전 제안 그대로 유지해 주세요.", False),
    ],
)
def test_ambiguous_or_stale_source_fails_closed(monkeypatch, text, stale):
    fields, _ = run(monkeypatch, text, previous=prior_dates(stale=stale))
    assert fields["days"].status in ("invalid", "uncertain")
    assert fields["days"].value is None
    assert fields["days"].alternatives == []


def test_excluded_protection_and_boundary_constraints(monkeypatch):
    current = {"start": "2026-12-02", "end": "2026-12-22", "excluded": ["2026-12-09", "2026-12-16"]}
    for target, valid in [(20, True), (23, False)]:
        text = f"현재 제외일 9일은 건드리지 말고, 제외일 16일만 {target}일로 교체해 주세요."
        fields, _ = run(
            monkeypatch,
            text,
            current=current,
            form="period",
            context=CTX.model_copy(update={"required_days": None}),
        )
        if valid:
            assert fields["excluded"].status == "valid"
            assert fields["excluded"].value == ["2026-12-09", "2026-12-20"]
        else:
            assert fields["excluded"].status in ("invalid", "uncertain")
            assert fields["excluded"].value is None


@pytest.mark.parametrize(
    "prefix,expected",
    [
        ("현재 입력한 ", ["2026-12-09", "2026-12-23", "2026-12-30"]),
        ("앞서 제안한 ", ["2026-12-09", "2026-12-23", "2026-12-27"]),
        ("", None),
    ],
)
def test_protected_date_source_disambiguates_two_viable_lists(monkeypatch, prefix, expected):
    current = {"days": ["2026-12-09", "2026-12-16", "2026-12-30"], "crew": 2}
    prior = PreviousCandidate(
        request_id="prior-three",
        text="작업일은 12월 9일, 16일, 27일로 제안해 주세요.",
        fields=[
            FieldIntent(
                field="days",
                action="set",
                value=["2026-12-09", "2026-12-16", "2026-12-27"],
                evidence="12월 9일, 16일, 27일",
            )
        ],
        baselines={"days": current["days"]},
    )
    text = prefix + "9일은 건드리지 말고, 작업일 16일만 23일로 교체해 주세요."
    fields, trace = run(
        monkeypatch,
        text,
        current=current,
        previous=prior,
        context=CTX.model_copy(update={"required_days": 3}),
    )
    assert trace["previous_verified"] is not None
    if expected is None:
        assert fields["days"].status in ("invalid", "uncertain")
        assert not fields["days"].alternatives
    else:
        assert fields["days"].status == "valid"
    assert fields["days"].value == expected


def test_protected_current_source_does_not_restore_stale_proposal(monkeypatch):
    text = "현재 입력한 9일은 건드리지 말고, 작업일 16일만 23일로 교체해 주세요."
    fields, trace = run(monkeypatch, text, previous=prior_dates(stale=True))
    assert trace["previous_rejected"]["days"] == "stale_context"
    assert fields["days"].status == "valid"
    assert fields["days"].value == ["2026-12-09", "2026-12-23"]
    assert fields["days"].correction.rejected_value == CURRENT["days"]


@pytest.mark.parametrize(
    "rejected,accepted,proposed",
    [
        ("9일", "23일", ["2026-12-23"]),
        ("16일", "9일", ["2026-12-23"]),
        ("16일", "23일", ["2026-12-10", "2026-12-23"]),
    ],
)
def test_wrong_model_relation_cannot_use_independent_date_edit(
    monkeypatch, rejected, accepted, proposed
):
    text = "현재 입력한 9일은 건드리지 말고, 작업일 16일만 23일로 교체해 주세요."
    fields, _ = run(
        monkeypatch,
        text,
        fields=[
            FieldIntent(
                field="days",
                action="set",
                value=proposed,
                evidence=text,
                correction=CorrectionRelation(
                    kind="replace",
                    rejected_value=["2026-12-16"],
                    rejected_evidence=rejected,
                    accepted_evidence=accepted,
                ),
            )
        ],
    )
    assert fields["days"].status in ("invalid", "uncertain")
    assert fields["days"].value is None
    assert not fields["days"].alternatives


@pytest.mark.parametrize(
    "text,previous,expected",
    [
        (
            "현재 입력한 9일은 건드리지 말고, 작업일 16일만 23일로 교체해 주세요.",
            False,
            ["2026-12-09", "2026-12-23"],
        ),
        (
            "앞서 제안한 작업일 중 16일만 23일로 옮기고, 인원은 아까 제안한 값대로 유지해 주세요.",
            True,
            ["2026-12-07", "2026-12-23"],
        ),
    ],
)
def test_new_date_edit_is_reverified_when_carried_to_next_turn(
    monkeypatch, text, previous, expected
):
    old = prior_dates() if previous else None
    fields, _ = run(monkeypatch, text, previous=old)
    candidate = fields["days"]
    assert candidate.status == "valid"
    assert candidate.value == expected
    carried = PreviousCandidate(
        request_id="carried",
        text=(old.text + "\n" if old else "") + text,
        fields=[
            FieldIntent(
                field="days",
                action="set",
                value=candidate.value,
                evidence=candidate.evidence,
                correction=candidate.correction,
                value_source="derived",
            )
        ],
        baselines={"days": CURRENT["days"]},
        sources={"days": candidate.source},
        origins={"days": "edit-origin"},
    )
    next_text = "작업일은 앞서 정정한 그대로 유지해 주세요."
    result, trace = run(
        monkeypatch,
        next_text,
        previous=carried,
        fields=[
            FieldIntent(
                field="days",
                action="keep",
                value=None,
                evidence=next_text,
                intent_kind="retain_previous",
                value_source="previous",
            )
        ],
    )
    assert trace["previous_rejected"] == {}
    assert result["days"].status == "valid"
    assert result["days"].value == expected
    assert result["days"].origin_request_id == "edit-origin"


@pytest.mark.parametrize(
    "protected",
    [
        "지금 화면에 있는 9일은 손대지 말고,",
        "현재 입력한 9일은 건드리지 마시고,",
        "현재 달력의 9일은 수정하지 말고,",
    ],
)
@pytest.mark.parametrize("mode", ["model", "rules", "fallback"])
def test_current_date_protection_and_prior_crew_are_independent(monkeypatch, protected, mode):
    text = (
        protected + " 작업일 16일만 23일로 교체해 주세요. 인원은 아까 제안한 그대로 유지해 주세요."
    )
    current = {"days": ["2026-12-09", "2026-12-16", "2026-12-28"], "crew": 2}
    prior = PreviousCandidate(
        request_id="old",
        text="작업일은 12월 9일, 16일, 21일로 잡고 인원은 세 명으로 제안해 주세요.",
        fields=[
            FieldIntent(
                field="days",
                action="set",
                value=["2026-12-09", "2026-12-16", "2026-12-21"],
                evidence="작업일은 12월 9일, 16일, 21일로",
                value_source="derived",
            ),
            FieldIntent(field="crew", action="set", value=3, evidence="인원은 세 명으로"),
        ],
        baselines=current,
        sources={"days": "llm", "crew": "llm"},
        origins={"days": "old-days", "crew": "old-crew"},
    )
    fields, trace = run(
        monkeypatch,
        text,
        current=current,
        previous=prior,
        mode=mode,
        context=CTX.model_copy(update={"required_days": 3}),
        fields=[
            FieldIntent(field="days", action="uncertain", value=None, evidence=text),
            FieldIntent(
                field="crew",
                action="keep",
                value=None,
                evidence="인원은 아까 제안한 그대로 유지해 주세요",
                intent_kind="retain_previous",
                value_source="previous",
            ),
        ],
    )
    assert fields["days"].status == "valid", trace
    assert fields["days"].value == ["2026-12-09", "2026-12-23", "2026-12-28"]
    assert fields["crew"].status == "valid", trace
    assert fields["crew"].value == 3
    assert fields["crew"].origin_request_id == "old-crew"
    assert fields["crew"].source == "llm"


@pytest.mark.parametrize(
    "extra",
    [
        "인원은 4명으로 바꿔 주세요. ",
        "인원은 현재 입력값을 유지해 주세요. ",
        "인원은 아직 미정입니다. ",
        "그 요청은 취소합니다. 인원은 유지할지 고민 중입니다. ",
    ],
)
def test_date_protection_does_not_hide_competing_crew_request(monkeypatch, extra):
    text = (
        "현재 입력한 9일은 건드리지 말고, 작업일 16일만 23일로 교체해 주세요. "
        + extra
        + "인원은 아까 제안한 그대로 유지해 주세요."
    )
    fields, _ = run(
        monkeypatch,
        text,
        previous=prior_dates(),
        fields=[
            FieldIntent(
                field="crew",
                action="keep",
                value=None,
                evidence="인원은 아까 제안한 그대로 유지해 주세요",
                intent_kind="retain_previous",
                value_source="previous",
            ),
        ],
    )
    assert fields["crew"].status != "valid" or fields["crew"].action == "keep"
    assert fields["crew"].value is None
    assert not fields["crew"].alternatives


def test_protected_date_does_not_resurrect_stale_crew(monkeypatch):
    text = (
        "현재 입력한 9일은 건드리지 말고, 작업일 16일만 23일로 교체해 주세요. "
        "인원은 아까 제안한 그대로 유지해 주세요."
    )
    fields, trace = run(
        monkeypatch,
        text,
        current={**CURRENT, "crew": 5},
        previous=prior_dates(),
        fields=[
            FieldIntent(
                field="crew",
                action="keep",
                value=None,
                evidence="인원은 아까 제안한 그대로 유지해 주세요",
                intent_kind="retain_previous",
                value_source="previous",
            ),
        ],
    )
    assert trace["previous_rejected"]["crew"] == "stale_context"
    assert fields["crew"].value is None
    assert fields["crew"].status != "valid"


def test_current_date_edit_and_prior_crew_survive_third_turn(monkeypatch):
    text = (
        "현재 입력한 9일은 건드리지 말고, 작업일 16일만 23일로 교체해 주세요. "
        "인원은 아까 제안한 그대로 유지해 주세요."
    )
    prior = prior_dates()
    fields, _ = run(
        monkeypatch,
        text,
        previous=prior,
        fields=[
            FieldIntent(
                field="crew",
                action="keep",
                value=None,
                evidence="인원은 아까 제안한 그대로 유지해 주세요",
                intent_kind="retain_previous",
                value_source="previous",
            ),
        ],
    )
    assert fields["days"].status == fields["crew"].status == "valid"
    carried = PreviousCandidate(
        request_id="second",
        text=prior.text + "\n" + text,
        fields=[
            FieldIntent(
                field="days",
                action="set",
                value=fields["days"].value,
                evidence=fields["days"].evidence,
                correction=fields["days"].correction,
                value_source="derived",
            ),
            FieldIntent(
                field="crew",
                action="set",
                value=fields["crew"].value,
                evidence=fields["crew"].evidence,
            ),
        ],
        baselines=CURRENT,
        sources={"days": fields["days"].source, "crew": fields["crew"].source},
        origins={"days": "second", "crew": fields["crew"].origin_request_id},
    )
    next_text = "인원은 앞서 제안한 그대로 유지해 주세요."
    result, trace = run(
        monkeypatch,
        next_text,
        previous=carried,
        fields=[
            FieldIntent(
                field="crew",
                action="keep",
                value=None,
                evidence=next_text,
                intent_kind="retain_previous",
                value_source="previous",
            )
        ],
    )
    assert trace["previous_rejected"] == {}
    assert result["crew"].status == "valid"
    assert result["crew"].value == 3
    assert result["crew"].origin_request_id == fields["crew"].origin_request_id


@pytest.mark.parametrize(
    "ending,valid",
    [
        ("유지합니다.", True),
        ("유지해요.", True),
        ("유지합니다?", False),
        ("유지하지 않습니다.", False),
        ("유지합니다. 인원은 아직 미정입니다.", False),
    ],
)
def test_prior_crew_affirmative_endings(monkeypatch, ending, valid):
    evidence = "인원도 아까 제안한 대로 " + ending
    fields, _ = run(
        monkeypatch,
        evidence,
        previous=prior_dates(),
        fields=[
            FieldIntent(
                field="crew",
                action="keep",
                value=None,
                evidence=evidence,
                intent_kind="retain_previous",
                value_source="previous",
            ),
        ],
    )
    assert (fields["crew"].status == "valid" and fields["crew"].value == 3) is valid
    if not valid:
        assert fields["crew"].value is None
        assert not fields["crew"].alternatives


def test_full_verified_date_relation_absorbs_rule_old_new_enumeration(monkeypatch):
    text = "아까 제안한 작업일 중 16일만 23일로 수정하고, 인원은 방금 잡은 그대로 유지해 주세요."
    fields, trace = run(
        monkeypatch,
        text,
        previous=prior_dates(),
        rules=[
            FieldIntent(
                field="days",
                action="set",
                value=["2026-12-16", "2026-12-23"],
                evidence=text,
            ),
        ],
        fields=[
            FieldIntent(
                field="days",
                action="set",
                value=["2026-12-07", "2026-12-23"],
                evidence="16일만 23일로 수정",
                correction=CorrectionRelation(
                    kind="replace",
                    rejected_value=["2026-12-16"],
                    rejected_evidence="16일",
                    accepted_evidence="23일",
                ),
            ),
            FieldIntent(
                field="crew",
                action="keep",
                value=None,
                evidence="인원은 방금 잡은 그대로 유지해 주세요",
                intent_kind="retain_previous",
                value_source="previous",
            ),
        ],
    )
    assert trace["rule_reviewed"][0]["status"] == "valid"
    assert trace["model_reviewed"][0]["status"] == "valid"
    assert fields["days"].status == "valid"
    assert fields["days"].value == ["2026-12-07", "2026-12-23"]
    assert not fields["days"].alternatives
    assert fields["crew"].value == 3


def test_opposed_rule_date_conflict_remains_held(monkeypatch):
    current = {"start": "2026-12-02", "end": "2026-12-22", "excluded": ["2026-12-04", "2026-12-06"]}
    text = "현재 폼의 제외일 4일 대신 5일로 바꿔 주세요."
    fields, trace = run(
        monkeypatch,
        text,
        current=current,
        form="period",
        context=CTX.model_copy(update={"required_days": None}),
        rules=[
            FieldIntent(field="excluded", action="set", value=["2026-12-04"], evidence="4일"),
        ],
        fields=[
            FieldIntent(
                field="excluded",
                action="set",
                value=["2026-12-05", "2026-12-06"],
                evidence="4일 대신 5일",
                correction=CorrectionRelation(
                    kind="replace",
                    rejected_value=["2026-12-04"],
                    rejected_evidence="4일",
                    accepted_evidence="5일",
                ),
            ),
        ],
    )
    assert trace["rule_reviewed"][0]["status"] == "valid"
    assert trace["model_reviewed"][0]["status"] == "valid"
    assert fields["excluded"].status in ("invalid", "uncertain")
    assert fields["excluded"].value is None
    assert not fields["excluded"].alternatives
