"""R6: 날짜의 실제 대상, 경쟁값, 독립적으로 검증된 정정의 서비스 회귀."""

import asyncio
from datetime import date
from uuid import UUID

import pytest

from app.agent import vendor_assist
from app.config import settings
from app.vendor.assist_schemas import (
    AssistContext,
    AssistRequest,
    CorrectionRelation,
    FieldIntent,
    ModelInterpretation,
    PreviousCandidate,
)
from app.vendor.assist_service import interpret

CURRENT = {
    "quote": {"amount": 4550000, "vat": "included", "days": 3, "valid": "2026-12-17"},
    "period": {"start": "2026-12-02", "end": "2026-12-30", "excluded": []},
}
CONTEXT = AssistContext(
    today=date(2026, 11, 24),
    base_month=date(2026, 12, 1),
    days_max=120,
    crew_max=6,
    vat_modes=["included", "excluded", "exempt"],
)


def _set(field, value, evidence, *, correction=None):
    return FieldIntent(
        field=field,
        action="set",
        value=value,
        evidence=evidence,
        intent_kind="explicit_change",
        value_source="utterance",
        correction=correction,
    )


def _keep(field):
    return FieldIntent(
        field=field, action="keep", value=None, evidence="", intent_kind="no_mention"
    )


def _run(
    monkeypatch,
    form,
    text,
    rules,
    model,
    *,
    use_model=True,
    prior=None,
    current=None,
    request_id="r6-date-scope",
):
    assert not settings.llm_live
    request = AssistRequest(
        request_id=request_id,
        form=form,
        resource_id=UUID(int=179),
        text=text,
        current=CURRENT[form] if current is None else current,
        previous_candidate=prior,
        rule_fields=list(rules),
    )
    original = request.model_dump(mode="json")
    calls = []

    async def stub(data, context):
        calls.append(data)
        return ModelInterpretation(fields=list(model))

    monkeypatch.setattr(vendor_assist, "interpret", stub)
    trace = {}
    response = asyncio.run(interpret(request, CONTEXT, use_model=use_model, trace=trace))
    assert len(calls) == int(use_model)
    assert response.source == ("llm" if use_model else "rule")
    assert request.model_dump(mode="json") == original
    assert response.request_id == request_id
    return {f.field: f for f in response.fields}, trace


def _held(candidate):
    assert candidate.status in ("invalid", "uncertain"), candidate
    assert candidate.action == "uncertain" and candidate.value is None
    assert not candidate.alternatives


def _valid(candidate, value):
    assert candidate.status == "valid" and candidate.action == "set", candidate
    assert candidate.value == value and not candidate.alternatives


def _conflict(candidate, values):
    assert candidate.status == "conflict" and candidate.action == "uncertain", candidate
    assert candidate.value is None
    assert {a.value for a in candidate.alternatives} == set(values)
    assert {a.source for a in candidate.alternatives} == {"rule", "llm"}
    assert all(a.action == "set" for a in candidate.alternatives)


@pytest.mark.parametrize("use_model", [False, True])
@pytest.mark.parametrize(
    "form,text,bad,good",
    [
        (
            "quote",
            "시공 기간은 12월 12일까지 가능할까요? 부가세는 별도로 확정합니다",
            ("valid", "2026-12-12", "12월 12일"),
            ("vat", "excluded", "부가세는 별도로 확정합니다"),
        ),
        (
            "quote",
            "시공 기간은 12월 12일까지 잡아 주세요. 부가세는 별도로 확정합니다",
            ("valid", "2026-12-12", "12월 12일"),
            ("vat", "excluded", "부가세는 별도로 확정합니다"),
        ),
        (
            "period",
            "마지막 실측 날짜는 12월 21일로 확정합니다",
            ("start", "2026-12-21", "12월 21일"),
            ("end", "2026-12-21", "마지막 실측 날짜는 12월 21일"),
        ),
        (
            "period",
            "첫 방문 가능일은 12월 8일로 확정합니다",
            ("end", "2026-12-08", "12월 8일"),
            ("start", "2026-12-08", "첫 방문 가능일은 12월 8일"),
        ),
        (
            "period",
            "휴무일은 12월 12일로 지정합니다",
            ("start", "2026-12-12", "12월 12일"),
            ("excluded", ["2026-12-12"], "휴무일은 12월 12일로 지정합니다"),
        ),
        (
            "period",
            "12/8 착공, 12/27 준공",
            ("start", "2026-12-27", "12/27"),
            ("end", "2026-12-27", "12/27 준공"),
        ),
        (
            "period",
            "12/8 착공, 12/27 준공",
            ("end", "2026-12-08", "12/8"),
            ("start", "2026-12-08", "12/8 착공"),
        ),
    ],
)
def test_agreeing_extractors_cannot_use_a_sibling_date(
    monkeypatch, use_model, form, text, bad, good
):
    candidates = [_set(*bad), _set(*good)]
    fields, _ = _run(monkeypatch, form, text, candidates, candidates, use_model=use_model)
    _held(fields[bad[0]])
    _valid(fields[good[0]], good[1])


_SINGLE = [
    ("quote", "valid", "견적서 효력 날짜", "2026-12-19", "2026-12-28", "12월 19일", "12월 28일"),
    ("period", "start", "첫 방문일", "2026-12-07", "2026-12-10", "12월 7일", "12월 10일"),
    ("period", "end", "마지막 실측 날짜", "2026-12-24", "2026-12-28", "12월 24일", "12월 28일"),
]


@pytest.mark.parametrize("form,field,label,a,b,ea,eb", _SINGLE)
@pytest.mark.parametrize("shape", ["repeated", "alternative"])
@pytest.mark.parametrize("producer", ["rule_only", "model_only", "agree"])
def test_competing_single_date_does_not_become_an_implicit_selection(
    monkeypatch, form, field, label, a, b, ea, eb, shape, producer
):
    text = (
        f"{label}은 {ea}로 확정합니다. {label}은 {eb}로 확정합니다"
        if shape == "repeated"
        else f"{label}은 {ea} 또는 {eb}로 변경해 주세요"
    )
    proposed = _set(field, a, ea)
    rules = [proposed] if producer != "model_only" else []
    model = [proposed] if producer != "rule_only" else []
    fields, _ = _run(monkeypatch, form, text, rules, model, use_model=producer != "rule_only")
    _held(fields[field])


@pytest.mark.parametrize("form,field,label,a,b,ea,eb", _SINGLE)
def test_two_grounded_explicit_alternatives_still_form_a_selectable_conflict(
    monkeypatch, form, field, label, a, b, ea, eb
):
    text = f"{label}은 {ea} 또는 {eb}로 변경해 주세요"
    fields, trace = _run(monkeypatch, form, text, [_set(field, a, ea)], [_set(field, b, eb)])
    _conflict(fields[field], [a, b])
    assert trace["decisions"][field]["choice"] == "conflict"


def test_competing_date_conflict_cannot_offer_date_from_other_field(monkeypatch):
    text = "첫 방문일은 12월 8일로 확정합니다. 종료일은 12월 27일로 확정합니다"
    fields, _ = _run(
        monkeypatch,
        "period",
        text,
        [_set("start", "2026-12-27", "12월 27일")],
        [_set("start", "2026-12-08", "12월 8일"), _set("end", "2026-12-27", "12월 27일")],
    )
    assert fields["start"].status != "conflict"
    assert fields["start"].value != "2026-12-27" and not fields["start"].alternatives
    _valid(fields["end"], "2026-12-27")


@pytest.mark.parametrize("start_day,conflict_valid", [(20, False), (5, True)])
def test_conflict_still_checks_every_alternative_against_merged_period(
    monkeypatch, start_day, conflict_valid
):
    text = f"종료일 12월 10일 또는 12월 25일로 변경, 시작일 12월 {start_day}일"
    fields, _ = _run(
        monkeypatch,
        "period",
        text,
        [_set("end", "2026-12-10", "12월 10일")],
        [
            _set("end", "2026-12-25", "12월 25일"),
            _set("start", f"2026-12-{start_day:02d}", f"12월 {start_day}일"),
        ],
    )
    _valid(fields["start"], f"2026-12-{start_day:02d}")
    if conflict_valid:
        _conflict(fields["end"], ["2026-12-10", "2026-12-25"])
    else:
        _held(fields["end"])
        assert fields["end"].reason_code == "combined_constraint"


@pytest.mark.parametrize("use_model", [False, True])
@pytest.mark.parametrize(
    "text,proofs",
    [
        (
            "방문 가능 기간은 12월 8일부터 27일까지로 확정합니다",
            ("12월 8일부터 27일까지", "12월 8일부터 27일까지"),
        ),
        (
            "실측 가능 기간 12월 8일에서 12월 27일까지로 잡아 주세요",
            ("12월 8일에서 12월 27일까지", "12월 8일에서 12월 27일까지"),
        ),
        ("12/8 착공, 12/27 준공", ("12/8 착공", "12/27 준공")),
        ("첫 방문일 12월 8일, 방문 접수 마감일 12월 27일", ("12월 8일", "12월 27일")),
    ],
)
def test_genuine_range_and_independent_boundary_labels_are_preserved(
    monkeypatch, use_model, text, proofs
):
    candidates = [
        _set("start", "2026-12-08", proofs[0]),
        _set("end", "2026-12-27", proofs[1]),
    ]
    fields, _ = _run(monkeypatch, "period", text, candidates, candidates, use_model=use_model)
    _valid(fields["start"], "2026-12-08")
    _valid(fields["end"], "2026-12-27")


@pytest.mark.parametrize("form,field,label,a,b,ea,eb", _SINGLE)
@pytest.mark.parametrize("producer", ["rule_only", "agree", "old_rule_new_model"])
def test_verified_same_field_correction_is_not_an_unresolved_competition(
    monkeypatch, form, field, label, a, b, ea, eb, producer
):
    text = f"{label}은 {ea} 말고 {eb}로 변경해주세요"
    relation = CorrectionRelation(
        kind="replace", rejected_value=a, rejected_evidence=ea, accepted_evidence=eb
    )
    new = _set(field, b, text, correction=relation)
    old = _set(field, a, ea)
    rules = [old] if producer == "old_rule_new_model" else [new]
    model = [] if producer == "rule_only" else [new]
    fields, _ = _run(monkeypatch, form, text, rules, model, use_model=producer != "rule_only")
    _valid(fields[field], b)
    assert fields[field].correction == relation
    assert fields[field].origin_request_id == "r6-date-scope"


@pytest.mark.parametrize("form,field,label,a,b,ea,eb", _SINGLE)
def test_forged_correction_cannot_turn_alternatives_into_a_definite_date(
    monkeypatch, form, field, label, a, b, ea, eb
):
    text = f"{label}은 {ea} 또는 {eb}로 변경해주세요"
    relation = CorrectionRelation(
        kind="replace", rejected_value=a, rejected_evidence=ea, accepted_evidence=eb
    )
    candidate = _set(field, b, text, correction=relation)
    fields, _ = _run(monkeypatch, form, text, [candidate], [candidate])
    _held(fields[field])


@pytest.mark.parametrize("use_model", [False, True])
def test_wrong_field_origin_cannot_be_reverified_as_previous_boundary(monkeypatch, use_model):
    text = "마지막 실측 날짜는 12월 21일로 확정합니다"
    prior = PreviousCandidate(
        request_id="forged-start",
        text=text,
        fields=[_set("start", "2026-12-21", "12월 21일")],
        sources={"start": "llm"},
        origins={"start": "forged-start"},
        baselines={"start": CURRENT["period"]["start"]},
    )
    now = "제외일은 12월 14일로 확정합니다"
    sibling = _set("excluded", ["2026-12-14"], now)
    fields, trace = _run(
        monkeypatch,
        "period",
        now,
        [sibling],
        [sibling, _keep("start")],
        prior=prior,
        use_model=use_model,
    )
    _held(fields["start"])
    _valid(fields["excluded"], ["2026-12-14"])
    assert "start" in trace["previous_rejected"]


@pytest.mark.parametrize("form,field,label,a,b,ea,eb", _SINGLE)
@pytest.mark.parametrize("use_model", [False, True])
def test_repeating_same_confirmed_date_is_not_a_competing_value(
    monkeypatch, form, field, label, a, b, ea, eb, use_model
):
    text = f"{label}은 {ea}로 확정합니다. {label}도 {ea}로 지정합니다"
    candidate = _set(field, a, ea)
    fields, _ = _run(monkeypatch, form, text, [candidate], [candidate], use_model=use_model)
    _valid(fields[field], a)


@pytest.mark.parametrize("form,field,label,a,b,ea,eb", _SINGLE)
@pytest.mark.parametrize("use_model", [False, True])
def test_competing_date_hold_preserves_independent_affirmative_sibling(
    monkeypatch, form, field, label, a, b, ea, eb, use_model
):
    sibling = {
        "valid": ("vat", "excluded", "부가세는 별도로 확정합니다"),
        "start": ("end", "2026-12-29", "종료일은 12월 29일로 확정합니다"),
        "end": ("start", "2026-12-05", "시작일은 12월 5일로 확정합니다"),
    }[field]
    text = f"{label}은 {ea} 또는 {eb}로 변경해 주세요. {sibling[2]}"
    candidates = [_set(field, a, ea), _set(*sibling)]
    fields, _ = _run(monkeypatch, form, text, candidates, candidates, use_model=use_model)
    _held(fields[field])
    _valid(fields[sibling[0]], sibling[1])


@pytest.mark.parametrize("form,field,label,a,b,ea,eb", _SINGLE)
def test_two_extractors_do_not_make_inquiry_values_selectable(
    monkeypatch, form, field, label, a, b, ea, eb
):
    text = f"{label}은 {ea} 또는 {eb}로 변경해도 될까요?"
    fields, _ = _run(monkeypatch, form, text, [_set(field, a, ea)], [_set(field, b, eb)])
    _held(fields[field])
