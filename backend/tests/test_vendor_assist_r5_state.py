"""R5: 독립된 실제 필드 범위, 세금, 유지 및 결합 제약 회귀."""

import asyncio
from datetime import date
from uuid import UUID

import pytest

from app.agent import vendor_assist
from app.config import settings
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
    "period": {"start": "2026-12-02", "end": "2026-12-24", "excluded": []},
    "dates": {"days": ["2026-12-09", "2026-12-16"], "crew": 2},
    "extra": {"title": "보강", "reason": "현장 추가", "amount": 1000000, "vat": "included"},
}


def _set(field, value, evidence):
    return FieldIntent(field=field, action="set", value=value, evidence=evidence)


def _keep(field, evidence="", kind="no_mention"):
    return FieldIntent(
        field=field,
        action="keep",
        value=None,
        evidence=evidence,
        intent_kind=kind,
        value_source={"retain_current": "current", "retain_previous": "previous"}.get(kind),
    )


def _ambiguous(field, evidence):
    return FieldIntent(
        field=field, action="uncertain", value=None, evidence=evidence, intent_kind="ambiguous"
    )


def _prior(form, history, *fields):
    return PreviousCandidate(
        request_id="r5-origin",
        text=history,
        fields=list(fields),
        sources={item.field: "llm" for item in fields},
        origins={item.field: "r5-origin" for item in fields},
        baselines={item.field: CURRENT[form][item.field] for item in fields},
    )


def _run(monkeypatch, form, text, model, *, rules=(), prior=None, current=None):
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
    q = AssistRequest(
        request_id="r5-current",
        resource_id=UUID(int=179),
        form=form,
        text=text,
        current=CURRENT[form] if current is None else current,
        previous_candidate=prior,
        rule_fields=list(rules),
    )
    calls = []

    async def stub(request, context):
        calls.append(request)
        return ModelInterpretation(fields=list(model))

    monkeypatch.setattr(vendor_assist, "interpret", stub)
    trace = {}
    result = asyncio.run(interpret(q, ctx, trace=trace))
    assert len(calls) == 1 and result.source == "llm"
    return {f.field: f for f in result.fields}, trace


def _held(field):
    assert field.status in ("invalid", "uncertain") and field.action == "uncertain"
    assert field.value is None and not field.alternatives


def _valid(field, value):
    assert field.status == "valid" and field.action == "set" and field.value == value
    assert not field.alternatives


def _carried(field, prior):
    old = next(f for f in prior.fields if f.field == field.field)
    _valid(field, old.value)
    assert field.evidence == old.evidence and field.source == prior.sources[field.field]
    assert field.origin_request_id == prior.origins[field.field]
    assert field.intent_kind == "retain_previous"


def test_dates_inquiry_does_not_block_independent_crew(monkeypatch):
    text = "작업일을 12월 11일과 24일로 하면 가능할까요? 인원은 3명으로 정하겠습니다"
    crew = _set("crew", 3, "인원은 3명으로 정하겠습니다")
    fields, _ = _run(
        monkeypatch,
        "dates",
        text,
        [_set("days", ["2026-12-11", "2026-12-24"], text.split("?")[0] + "?"), crew],
        rules=[_set("crew", 3, text)],
    )
    _valid(fields["crew"], 3)
    _held(fields["days"])


@pytest.mark.parametrize(
    "text",
    [
        "작업일을 12월 11일로 하면 인원은 3명으로 정하겠습니다",
        "작업일은 12월 11일로 정합니다. 인원은 3명으로 하면 가능할까요?",
        "가능하면 인원은 3명으로 정하겠습니다",
    ],
)
def test_crew_conditions_are_not_independent_inquiries(monkeypatch, text):
    crew = _set("crew", 3, "3명")
    fields, _ = _run(monkeypatch, "dates", text, [crew], rules=[crew])
    _held(fields["crew"])


def test_crew_history_survives_confirmed_date_cancellation(monkeypatch):
    prior = _prior(
        "dates",
        "시공 날짜 12월 11일하고 22일, 작업자 6명 초안 부탁합니다\n"
        "공사 날짜 변경 요청은 취소하고 작업자 수는 앞서 제안한 값 유지",
        _set("crew", 6, "작업자 6명"),
    )
    text = "공사 날짜는 12월 12일과 29일로 다시 제안합니다"
    fields, trace = _run(
        monkeypatch,
        "dates",
        text,
        [_set("days", ["2026-12-12", "2026-12-29"], text), _keep("crew")],
        prior=prior,
    )
    _carried(fields["crew"], prior)
    _valid(fields["days"], ["2026-12-12", "2026-12-29"])
    assert trace["previous_rejected"] == {}


@pytest.mark.parametrize(
    "control",
    [
        "작업자 수 변경 요청은 취소하고 날짜는 앞서 제안한 값 유지",
        "작업자 수는 현재 화면 값으로 유지해 주세요",
        "가능하면 작업자 수는 앞서 제안한 값 유지",
    ],
)
def test_crew_history_cannot_resurrect_after_competing_control(monkeypatch, control):
    prior = _prior("dates", "작업자 6명 초안 부탁합니다\n" + control, _set("crew", 6, "작업자 6명"))
    fields, trace = _run(
        monkeypatch,
        "dates",
        "날짜는 12월 12일과 29일로 정합니다",
        [_keep("crew")],
        prior=prior,
    )
    _held(fields["crew"])
    assert "crew" in trace["previous_rejected"]


def test_negative_inclusion_affirmative_tax_with_separate_inquiry(monkeypatch):
    text = "유효기한을 12월 29일까지 해도 될까요? 세금은 포함하지 않는 방식으로 확정합니다"
    fields, _ = _run(
        monkeypatch,
        "quote",
        text,
        [
            _ambiguous("valid", text.split("?")[0] + "?"),
            _set("vat", "excluded", "세금은 포함하지 않는 방식으로 확정합니다"),
        ],
    )
    _valid(fields["vat"], "excluded")
    _held(fields["valid"])


@pytest.mark.parametrize(
    "text",
    [
        "세금은 포함하지 않는 방식으로 확정할까요?",
        "세금은 포함하지 않는 방식으로 확정하지 마세요",
        "세금은 포함하지 않는 방식으로 확정합니다. 다시 고민 중입니다",
        "세금은 포함하지 않는 방식으로 확정합니다. 세금은 포함으로 변경합니다",
        "세금은 포함하지 않는 방식으로 확정한다는 말은 아니에요",
        "가능하면 세금은 포함하지 않는 방식으로 확정합니다",
    ],
)
def test_negative_inclusion_not_accepted_for_unresolved_tax(monkeypatch, text):
    candidate = _set("vat", "excluded", text)
    fields, _ = _run(monkeypatch, "quote", text, [candidate], rules=[candidate])
    _held(fields["vat"])


def test_reverse_tax_label_and_previous_evidence(monkeypatch):
    text = "견적 총액 537만원과 별도 세금으로 먼저 제시해주세요"
    amount = _set("amount", 5370000, "견적 총액 537만원")
    tax = _set("vat", "excluded", "별도 세금")
    fields, _ = _run(monkeypatch, "quote", text, [amount, tax], rules=[amount])
    _valid(fields["amount"], 5370000)
    _valid(fields["vat"], "excluded")
    prior = _prior("quote", text, amount, tax)
    fields, _ = _run(
        monkeypatch,
        "quote",
        "공사기간은 8일로 확정합니다",
        [_set("days", 8, "8일"), _keep("vat"), _keep("amount")],
        prior=prior,
    )
    _carried(fields["vat"], prior)
    _carried(fields["amount"], prior)


def test_invalid_tax_business_mode_does_not_hold_title(monkeypatch):
    prior = _prior(
        "extra",
        "추가 작업명은 창틀 몰딩 재부착, 추가 비용 67만원으로 후보 잡아주세요",
        _set("title", "창틀 몰딩 재부착", "추가 작업명은 창틀 몰딩 재부착"),
        _set("amount", 670000, "추가 비용 67만원"),
    )
    text = "세금은 면세 처리로 넣어주세요"
    vat = _set("vat", "exempt", text)
    fields, _ = _run(
        monkeypatch,
        "extra",
        text,
        [_keep("title"), _keep("amount"), vat],
        rules=[_set("title", "세금은", text), vat],
        prior=prior,
    )
    _carried(fields["title"], prior)
    _carried(fields["amount"], prior)
    _held(fields["vat"])


@pytest.mark.parametrize(
    "text, value, model_no_mention",
    [
        ("실측 접수 마감일은 12월 28일로 바꿔주세요", "2026-12-28", True),
        ("마지막 실측 날짜는 12월 31일로 지정해주세요", "2026-12-31", False),
        ("방문 접수 종료일을 12월 28일로 정해주세요", "2026-12-28", False),
    ],
)
def test_period_end_alias_with_independent_affirmative_proof(
    monkeypatch, text, value, model_no_mention
):
    model = _keep("end") if model_no_mention else _ambiguous("end", text)
    fields, _ = _run(
        monkeypatch,
        "period",
        text,
        [_keep("start"), model, _keep("excluded")],
        rules=[_set("start", value, text), _set("end", value, text)],
    )
    _valid(fields["end"], value)
    assert fields["start"].action == "keep" and fields["start"].status == "valid"


@pytest.mark.parametrize(
    "text",
    [
        "마지막 실측 날짜는 12월 31일로 지정할까요?",
        "실측 접수 마감일은 12월 28일로 바꿔주세요라고 했지만 취소합니다",
        "방문 접수 종료일을 12월 28일로 정해주세요. 시작일은 12월 30일로 정해주세요",
        "가능하면 방문 접수 종료일을 12월 28일로 정해주세요",
        "마지막 실측 날짜는 12월 31일로 지정하지 마세요",
    ],
)
def test_period_end_alias_does_not_bypass_inquiry_or_conflict(monkeypatch, text):
    fields, _ = _run(monkeypatch, "period", text, [_ambiguous("end", text)])
    _held(fields["end"])


def test_period_current_restore_preserves_sibling_and_exclusion(monkeypatch):
    prior = _prior(
        "period",
        "실측 시작일 12월 12일, 종료일 12월 29일로 후보 지정",
        _set("start", "2026-12-12", "시작일 12월 12일"),
        _set("end", "2026-12-29", "종료일 12월 29일"),
    )
    text = "시작일은 현재 화면 값으로 돌리고 제외일은 12월 5일로 정합니다"
    fields, _ = _run(
        monkeypatch,
        "period",
        text,
        [
            _keep("start", "시작일은 현재 화면 값으로 돌리고", "retain_current"),
            _keep("end"),
            _set("excluded", ["2026-12-05"], "제외일은 12월 5일로 정합니다"),
        ],
        prior=prior,
    )
    assert fields["start"].intent_kind == "retain_current" and fields["start"].status == "valid"
    _carried(fields["end"], prior)
    _valid(fields["excluded"], ["2026-12-05"])


def _quote_prior():
    return _prior(
        "quote",
        "견적 총액은 537만원으로 확정합니다\n부가세는 별도로 정합니다\n"
        "공사기간은 8일로 정합니다\n견적 유효기간은 12월 20일로 확정합니다",
        _set("amount", 5370000, "견적 총액은 537만원"),
        _set("vat", "excluded", "부가세는 별도로 정합니다"),
        _set("days", 8, "공사기간은 8일로 정합니다"),
        _set("valid", "2026-12-20", "견적 유효기간은 12월 20일"),
    )


def test_quote_current_restore_and_remaining_candidates(monkeypatch):
    prior = _quote_prior()
    text = "공사기간은 지금 폼에 적힌 값으로 되돌리고 나머지 후보는 유지"
    fields, _ = _run(
        monkeypatch,
        "quote",
        text,
        [
            _keep("days", "공사기간은 지금 폼에 적힌 값으로 되돌리고", "retain_current"),
            *[
                _keep(f, "나머지 후보는 유지", "retain_previous")
                for f in ("amount", "vat", "valid")
            ],
        ],
        prior=prior,
    )
    assert fields["days"].status == "valid" and fields["days"].intent_kind == "retain_current"
    for field in ("amount", "vat", "valid"):
        _carried(fields[field], prior)


@pytest.mark.parametrize(
    "text",
    [
        "금액, 공사 일수 및 세금, 유효기간 모두 앞서 제안한 값으로 적용 부탁",
        "견적 금액과 세금, 공사 기간, 만료일 모두 직전까지 제안한 값으로 유지합니다",
    ],
)
def test_group_previous_retention(monkeypatch, text):
    prior = _quote_prior()
    fields, _ = _run(
        monkeypatch,
        "quote",
        text,
        [_keep(f, text, "retain_previous") for f in CURRENT["quote"]],
        prior=prior,
    )
    for field in CURRENT["quote"]:
        _carried(fields[field], prior)


def test_group_raw_independent_proof_keeps_original_provenance(monkeypatch):
    prior = _quote_prior()
    text = "앞서 정한 견적 금액, 세금 조건, 유효기간은 그대로 사용해주세요"
    model = [
        _keep(f, "앞서 정한 ... 그대로 사용해주세요", "retain_previous")
        for f in ("amount", "vat", "valid", "days")
    ]
    fields, trace = _run(monkeypatch, "quote", text, model, prior=prior)
    for field in ("amount", "vat", "valid"):
        _carried(fields[field], prior)
    assert all(f["reason_code"] == "evidence_missing" for f in trace["model_reviewed"])
    _held(fields["days"])


@pytest.mark.parametrize(
    "text",
    [
        "앞서 정한 견적 금액, 세금 조건은 그대로 사용해주세요. 금액은 취소합니다",
        "금액과 세금은 현재 화면 값으로 유지해 주세요",
        "금액은 현재 화면 값으로 돌리고 세금과 유효기간은 이전 제안대로 유지해 주세요",
        "가능하면 금액과 세금은 앞서 제안한 값으로 유지해 주세요",
        "금액과 세금은 앞서 제안한 값으로 유지해 주세요. 금액은 다시 고민 중입니다",
    ],
)
def test_group_independent_proof_cannot_hide_competing_scope(monkeypatch, text):
    prior = _quote_prior()
    fields, _ = _run(
        monkeypatch,
        "quote",
        text,
        [_keep("amount", "존재하지 않는 이전 금액", "retain_previous")],
        prior=prior,
    )
    assert not (fields["amount"].status == "valid" and fields["amount"].action == "set")


def test_group_without_prior_does_not_create_value(monkeypatch):
    text = "금액과 세금은 앞서 제안한 값으로 유지해 주세요"
    fields, _ = _run(
        monkeypatch,
        "quote",
        text,
        [_keep(f, text, "retain_previous") for f in ("amount", "vat")],
    )
    _held(fields["amount"])
    _held(fields["vat"])


@pytest.mark.parametrize("last, safe", [("24일", True), ("31일", False)])
def test_excluded_list_sibling_scope_and_combination(monkeypatch, last, safe):
    prior = _prior(
        "period",
        "시작일 12월 7일, 종료일 12월 29일로 확정합니다",
        _set("start", "2026-12-07", "시작일 12월 7일"),
        _set("end", "2026-12-29", "종료일 12월 29일"),
    )
    text = f"제외일은 12월 11일, 17일, {last}로 새로 확정합니다"
    value = ["2026-12-11", "2026-12-17", f"2026-12-{last[:-1]}"]
    fields, _ = _run(
        monkeypatch,
        "period",
        text,
        [_keep("start"), _keep("end"), _set("excluded", value, text)],
        rules=[_set("start", value[-1], text), _set("end", value[-1], text)],
        prior=prior,
    )
    _carried(fields["start"], prior)
    _carried(fields["end"], prior)
    if safe:
        _valid(fields["excluded"], value)
    else:
        _held(fields["excluded"])
