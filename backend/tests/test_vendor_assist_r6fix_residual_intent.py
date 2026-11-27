"""R6 독립 재생 잔여: 실제 문자열 대상과 조건/문의의 절 경계."""

import asyncio
from datetime import date
from uuid import UUID

import pytest

from app.agent import vendor_assist
from app.config import settings
from app.vendor.assist_intent import intent_clauses, retention_scopes
from app.vendor.assist_intent_safety import (
    assignment_directive_in_value,
    date_set_is_inquiry,
    text_value_scope_ok,
)
from app.vendor.assist_schemas import (
    AssistContext,
    AssistRequest,
    FieldIntent,
    ModelInterpretation,
    PreviousCandidate,
)
from app.vendor.assist_service import interpret

CURRENT = {
    "quote": {"amount": 4550000, "vat": "included", "days": 3, "valid": "2026-12-17"},
    "extra": {"title": "보강", "reason": "현장 추가", "amount": 1000000, "vat": "included"},
    "period": {"start": "2026-12-02", "end": "2026-12-24", "excluded": []},
    "dates": {"days": ["2026-12-09", "2026-12-16"], "crew": 2},
}


def _set(field, value, evidence):
    return FieldIntent(
        field=field,
        action="set",
        value=value,
        evidence=evidence,
        intent_kind="explicit_change",
        value_source="utterance",
    )


def _keep(field, text="", *, retain=False):
    return FieldIntent(
        field=field,
        action="keep",
        value=None,
        evidence=text,
        intent_kind="retain_previous" if retain else "no_mention",
        value_source="previous" if retain else None,
    )


def _run(
    monkeypatch,
    form,
    text,
    rule,
    model,
    *,
    use_model=True,
    prior=None,
    current=None,
    request_id="r6fix-residual",
):
    assert not settings.llm_live
    ctx = AssistContext(
        today=date(2026, 11, 24),
        base_month=date(2026, 12, 1),
        days_max=120,
        crew_max=6,
        required_days=2 if form == "dates" else None,
        vat_modes=["included", "excluded"]
        if form == "extra"
        else ["included", "excluded", "exempt"],
    )
    req = AssistRequest(
        request_id=request_id,
        form=form,
        resource_id=UUID(int=179),
        text=text,
        current=CURRENT[form] if current is None else current,
        previous_candidate=prior,
        rule_fields=rule,
    )
    calls = []

    async def stub(data, context):
        calls.append(data)
        return ModelInterpretation(fields=model)

    monkeypatch.setattr(vendor_assist, "interpret", stub)
    trace = {}
    result = asyncio.run(interpret(req, ctx, use_model=use_model, trace=trace))
    assert len(calls) == int(use_model)
    return req, {f.field: f for f in result.fields}, trace


def _valid(field, value):
    assert field.status == "valid" and field.action == "set" and field.value == value
    assert not field.alternatives


def _not_offered(field):
    assert field.action not in ("set", "clear") and not field.alternatives


@pytest.mark.parametrize(
    "field,value,text,ok",
    [
        (
            "title",
            "추가 작업 비용은",
            "추가 작업 비용은 48만 2천 원, 별도 세금 방식으로 입력 부탁드려요.",
            False,
        ),
        (
            "title",
            "추가 사유는 접합부 균열로 지정합니다.",
            "추가 사유는 접합부 균열로 지정합니다.",
            False,
        ),
        ("title", "접합부 균열", "추가 사유는 접합부 균열로 지정합니다.", False),
        ("reason", "창틀 수직 보정", "추가 항목 제목을 창틀 수직 보정으로 지정합니다.", False),
        ("title", "별도 세금 방식", "별도 세금 방식으로 입력 부탁드려요.", False),
        (
            "title",
            "이전 후보를 쓰고 세금은 별도로 처리해 주세요",
            "작업명은 이전 후보를 쓰고 세금은 별도로 처리해 주세요.",
            False,
        ),
        (
            "title",
            "이전 후보를 쓰고",
            "작업명은 이전 후보를 쓰고 세금은 별도로 처리해 주세요.",
            False,
        ),
        (
            "title",
            "'사흘간 건조 후 보수'로 지정 바랍니다",
            "추가 작업명은 '사흘간 건조 후 보수'로 지정 바랍니다. 추가금 27만원으로 확정합니다.",
            False,
        ),
        (
            "title",
            "사흘간 건조 후 보수",
            "추가 작업명은 '사흘간 건조 후 보수'로 지정 바랍니다. 추가금 27만원으로 확정합니다.",
            True,
        ),
        (
            "reason",
            "작업자 두 명 추가 투입",
            "추가 사유는 '작업자 두 명 추가 투입'으로 적고 금액 52만 6천 원으로 정해주세요.",
            True,
        ),
        ("title", "이전 후보를 쓰고", "작업명은 '이전 후보를 쓰고'로 지정합니다.", True),
        ("reason", "사유 확인 누락", "사유는 사유 확인 누락으로 입력 바랍니다.", True),
        ("title", "금액 조정", "작업명은 금액 조정으로 지정 바랍니다.", True),
        ("title", "누수 보수", "실리콘 보수 말고 누수 보수", True),
        ("reason", "고객 요청으로 공정 변경", "사유는 고객 요청으로 공정 변경", True),
        (
            "title",
            "접합부 균열",
            '작업명은 "접합부 균열"로 지정합니다. 사유는 습기 침투입니다.',
            True,
        ),
    ],
)
def test_string_scope_and_actual_value_directive_are_distinct(field, value, text, ok):
    assert (
        text_value_scope_ok(field, value, text)
        and not assignment_directive_in_value(field, value, text)
    ) is ok


@pytest.mark.parametrize("use_model", [False, True])
@pytest.mark.parametrize(
    "text,bad,good",
    [
        (
            "추가 작업 비용은 48만 2천 원, 별도 세금 방식으로 입력 부탁드려요.",
            ("title", "추가 작업 비용은"),
            ("amount", 482000, "추가 작업 비용은 48만 2천 원"),
        ),
        (
            "추가 사유는 접합부 균열로 지정합니다.",
            ("title", "추가 사유는 접합부 균열로 지정합니다."),
            ("reason", "접합부 균열", "추가 사유는 접합부 균열로 지정합니다"),
        ),
        (
            "추가 사유는 접합부 균열로 지정합니다.",
            ("title", "접합부 균열"),
            ("reason", "접합부 균열", "추가 사유는 접합부 균열로 지정합니다"),
        ),
        (
            "작업명은 이전 후보를 쓰고 세금은 별도로 처리해 주세요.",
            ("title", "이전 후보를 쓰고 세금은 별도로 처리해 주세요"),
            ("vat", "excluded", "세금은 별도로 처리해 주세요"),
        ),
    ],
)
def test_service_wrong_text_is_not_selectable_with_independent_valid_sibling(
    monkeypatch, use_model, text, bad, good
):
    bad_candidate = _set(*bad, text)
    good_candidate = _set(*good)
    candidates = [bad_candidate, good_candidate]
    _, fields, _ = _run(monkeypatch, "extra", text, candidates, candidates, use_model=use_model)
    _not_offered(fields[bad[0]])
    _valid(fields[good[0]], good[1])


def test_wrong_quoted_command_tail_cannot_be_a_conflict_alternative(monkeypatch):
    text = "추가 작업명은 '사흘간 건조 후 보수'로 지정 바랍니다. 추가금 27만원으로 확정합니다."
    bad = _set("title", "'사흘간 건조 후 보수'로 지정 바랍니다", text)
    good = _set(
        "title", "사흘간 건조 후 보수", "추가 작업명은 '사흘간 건조 후 보수'로 지정 바랍니다"
    )
    amount = _set("amount", 270000, "추가금 27만원으로 확정합니다")
    _, fields, _ = _run(monkeypatch, "extra", text, [bad, amount], [good, amount])
    _valid(fields["title"], good.value)
    _valid(fields["amount"], amount.value)


@pytest.mark.parametrize("use_model", [False, True])
def test_completed_crew_condition_does_not_contaminate_independent_dates(monkeypatch, use_model):
    text = "일정이 맞으면 투입 인원은 세 명으로 할 생각입니다. 시공일은 12월 8일과 21일로 정합니다."
    assert [p.fields for p in intent_clauses("dates", text)] == [{"crew"}, {"days"}]
    assert not date_set_is_inquiry("dates", "days", text)
    candidates = [
        _set("crew", 3, "투입 인원은 세 명으로"),
        _set("days", ["2026-12-08", "2026-12-21"], "시공일은 12월 8일과 21일로 정합니다"),
    ]
    _, fields, _ = _run(monkeypatch, "dates", text, candidates, candidates, use_model=use_model)
    _not_offered(fields["crew"])
    _valid(fields["days"], ["2026-12-08", "2026-12-21"])


def test_same_condition_with_date_as_consequent_is_not_definite():
    text = "인원이 맞으면 시공일은 12월 8일과 21일로 정합니다."
    assert date_set_is_inquiry("dates", "days", text)
    assert intent_clauses("dates", text)[0].fields == {"days"}


@pytest.mark.parametrize("use_model", [False, True])
@pytest.mark.parametrize(
    "text",
    [
        "효력 기간을 12월 21일까지 연장 가능한지 궁금합니다 세금은 별도 방식으로 처리해 주세요.",
        "효력 기간을 12월 21일까지 연장해도 될까요 세금은 별도 방식으로 처리해 주세요.",
    ],
)
def test_unpunctuated_validity_inquiry_does_not_hide_vat_request(monkeypatch, use_model, text):
    assert date_set_is_inquiry("quote", "valid", text)
    parts = intent_clauses("quote", text)
    assert [p.fields for p in parts] == [{"valid"}, {"vat"}]
    assert parts[0].boundary == "question"
    candidates = [
        _set("valid", "2026-12-21", "효력 기간을 12월 21일까지"),
        _set("vat", "excluded", "세금은 별도 방식으로 처리해 주세요"),
    ]
    _, fields, _ = _run(monkeypatch, "quote", text, candidates, candidates, use_model=use_model)
    _not_offered(fields["valid"])
    _valid(fields["vat"], "excluded")


@pytest.mark.parametrize("use_model", [False, True])
def test_unpunctuated_vat_inquiry_does_not_hide_validity_request(monkeypatch, use_model):
    text = "세금 별도 처리해도 될까요 견적 효력은 12월 28일까지 확정합니다."
    assert [p.fields for p in intent_clauses("quote", text)] == [{"vat"}, {"valid"}]
    candidates = [
        _set("vat", "excluded", "세금 별도 처리"),
        _set("valid", "2026-12-28", "견적 효력은 12월 28일까지"),
    ]
    _, fields, _ = _run(monkeypatch, "quote", text, candidates, candidates, use_model=use_model)
    _not_offered(fields["vat"])
    _valid(fields["valid"], "2026-12-28")


@pytest.mark.parametrize("use_model", [False, True])
def test_completed_proposal_reference_preserves_actual_validity_origin(monkeypatch, use_model):
    origin = "견적 효력은 12월 28일까지 확정합니다"
    candidate = _set("valid", "2026-12-28", origin)
    req, fields, _ = _run(
        monkeypatch,
        "quote",
        origin,
        [candidate],
        [candidate],
        use_model=use_model,
        request_id="r6-origin",
    )
    _valid(fields["valid"], "2026-12-28")
    old = fields["valid"]
    prior = PreviousCandidate(
        request_id=req.request_id,
        text=req.text,
        fields=[candidate],
        sources={"valid": old.source},
        origins={"valid": old.origin_request_id},
        baselines={"valid": req.current["valid"]},
    )
    text = "부가세 포함으로 넣어주세요. 유효기간은 앞서 제시한 날짜로 유지합니다."
    retain = _keep("valid", "유효기간은 앞서 제시한 날짜로 유지합니다", retain=True)
    vat = _set("vat", "included", "부가세 포함으로 넣어주세요")
    assert any(
        s.fields == {"valid"} and s.reference == "previous" for s in retention_scopes("quote", text)
    )
    _, fields, _ = _run(
        monkeypatch, "quote", text, [vat], [vat, retain], prior=prior, use_model=use_model
    )
    _valid(fields["valid"], old.value)
    assert fields["valid"].origin_request_id == old.origin_request_id
    assert fields["valid"].evidence == old.evidence and fields["valid"].source == old.source


@pytest.mark.parametrize("use_model", [False, True])
def test_forged_previous_reason_cannot_be_carried_as_title(monkeypatch, use_model):
    origin = "추가 사유는 접합부 균열로 지정합니다."
    prior = PreviousCandidate(
        request_id="forged",
        text=origin,
        fields=[_set("title", "접합부 균열", origin)],
        sources={"title": "llm"},
        origins={"title": "forged"},
        baselines={"title": CURRENT["extra"]["title"]},
    )
    text = "작업명은 이전 후보를 쓰고 세금은 별도로 처리해 주세요."
    vat = _set("vat", "excluded", "세금은 별도로 처리해 주세요")
    retain = _keep("title", "작업명은 이전 후보를 쓰고", retain=True)
    _, fields, trace = _run(
        monkeypatch, "extra", text, [vat], [vat, retain], prior=prior, use_model=use_model
    )
    _not_offered(fields["title"])
    _valid(fields["vat"], "excluded")
    assert "title" in trace["previous_rejected"]


@pytest.mark.parametrize("use_model", [False, True])
@pytest.mark.parametrize("control", ["valid", "stale", "missing"])
def test_explicit_candidate_reference_keeps_only_verified_pending_dates(
    monkeypatch, use_model, control
):
    origin = "작업일은 12월 8일과 21일, 투입 인원은 3명으로 확정합니다"
    days = _set("days", ["2026-12-08", "2026-12-21"], "작업일은 12월 8일과 21일")
    crew = _set("crew", 3, "투입 인원은 3명으로 확정합니다")
    req, fields, _ = _run(
        monkeypatch,
        "dates",
        origin,
        [days, crew],
        [days, crew],
        use_model=use_model,
        request_id="r6-candidate-origin",
    )
    _valid(fields["days"], days.value)
    _valid(fields["crew"], 3)
    old = fields["days"]
    pending = [days, crew] if control != "missing" else [crew]
    prior = PreviousCandidate(
        request_id=req.request_id,
        text=req.text,
        fields=pending,
        sources={f.field: fields[f.field].source for f in pending},
        origins={f.field: fields[f.field].origin_request_id for f in pending},
        baselines={f.field: req.current[f.field] for f in pending},
    )
    current = dict(CURRENT["dates"])
    if control == "stale":
        current["days"] = ["2026-12-10", "2026-12-16"]
    text = "투입 인원 변경은 없던 걸로 하겠습니다. 작업일 후보는 그대로 두세요."
    scopes = retention_scopes("dates", text)
    assert len(scopes) == 1 and scopes[0].reference == "previous"
    assert scopes[0].fields == {"days"} and scopes[0].compatible
    retain = _keep("days", "작업일 후보는 그대로 두세요.", retain=True)
    _, fields, trace = _run(
        monkeypatch,
        "dates",
        text,
        [],
        [retain],
        use_model=use_model,
        prior=prior,
        current=current,
    )
    _not_offered(fields["crew"])
    if control == "valid":
        _valid(fields["days"], old.value)
        assert fields["days"].origin_request_id == old.origin_request_id
        assert fields["days"].source == old.source
        assert fields["days"].evidence == old.evidence
    else:
        _not_offered(fields["days"])
        if control == "stale":
            assert trace["previous_rejected"]["days"] == "stale_context"
