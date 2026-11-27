"""실행된 quote-10 및 독립적인 첫/최근 참조의 실제 서비스 회귀."""

import asyncio
from datetime import date
from uuid import UUID

import pytest

from app.agent import vendor_assist
from app.config import settings
from app.vendor.assist_schemas import (
    FORM_FIELDS,
    AssistContext,
    AssistRequest,
    FieldIntent,
    ModelInterpretation,
    PreviousCandidate,
)
from app.vendor.assist_service import interpret

CASES = {
    "quote": {
        "current": {"amount": 4550000, "vat": "included", "days": 3, "valid": "2026-12-17"},
        "first": ("vat", "excluded", "세금은 별도 조건", "세금 조건은", "방식"),
        "initial": ("valid", "2026-12-21", "견적 유효기간은 12월 21일"),
        "recent": (
            "valid",
            "2026-12-27",
            "견적 유효기한을 12월 27일로 늘려",
            "만료 날짜는",
            "날짜",
        ),
        "seed": "견적 유효기간은 12월 21일, 세금은 별도 조건으로 후보 등록해 주세요.",
        "update": "견적 유효기한을 12월 27일로 늘려 주실래요?",
        "changed": ("vat", "exempt", "세금은 면세로 변경합니다"),
        "wrong": "금액",
    },
    "period": {
        "current": {"start": "2026-12-02", "end": "2026-12-24", "excluded": []},
        "first": ("start", "2026-12-05", "시작일은 12월 5일", "시작일은", "날짜"),
        "initial": ("end", "2026-12-22", "종료일은 12월 22일"),
        "recent": ("end", "2026-12-23", "종료일은 12월 23일", "종료일은", "날짜"),
        "seed": "시작일은 12월 5일, 종료일은 12월 22일로 제안합니다.",
        "update": "종료일은 12월 23일로 변경합니다.",
        "changed": ("start", "2026-12-06", "시작일은 12월 6일로 변경합니다"),
        "wrong": "금액",
    },
    "dates": {
        "current": {"days": ["2026-12-09", "2026-12-16"], "crew": 2},
        "first": ("crew", 3, "인원은 3명", "인원은", "인원"),
        "initial": ("days", ["2026-12-08", "2026-12-21"], "시공 날짜는 12월 8일과 21일"),
        "recent": (
            "days",
            ["2026-12-11", "2026-12-24"],
            "시공 날짜는 12월 11일과 24일",
            "시공 날짜는",
            "날짜",
        ),
        "seed": "시공 날짜는 12월 8일과 21일, 인원은 3명으로 제안합니다.",
        "update": "시공 날짜는 12월 11일과 24일로 변경합니다.",
        "changed": ("crew", 4, "인원은 4명으로 변경합니다"),
        "wrong": "금액",
    },
    "extra": {
        "current": {"title": "보강", "reason": "현장 추가", "amount": 1000000, "vat": "included"},
        "first": ("reason", "문짝 하단 긁힘", "추가 사유는 문짝 하단 긁힘", "사유는", "사유"),
        "initial": ("amount", 286000, "비용은 28만 6천 원"),
        "recent": ("amount", 350000, "추가금은 35만원", "추가금은", "금액"),
        "seed": "추가 사유는 문짝 하단 긁힘, 비용은 28만 6천 원으로 먼저 띄워주세요.",
        "update": "추가금은 35만원으로 변경합니다.",
        "changed": ("reason", "바닥 파손", "사유는 바닥 파손으로 변경합니다"),
        "wrong": "인원",
    },
}


def _field(name, action="keep", value=None, evidence="", intent=None, source=None):
    return FieldIntent(
        field=name,
        action=action,
        value=value,
        evidence=evidence,
        intent_kind=intent,
        value_source=source,
    )


def _set(name, value, evidence):
    return _field(name, "set", value, evidence, "explicit_change", "utterance")


def _retain(name, evidence, reference="previous"):
    return _field(name, evidence=evidence, intent="retain_" + reference, source=reference)


def _run(
    monkeypatch,
    form,
    text,
    model,
    *,
    use_model,
    prior=None,
    current=None,
    rules=(),
    request_id="followup",
):
    assert not settings.llm_live
    current = CASES[form]["current"] if current is None else current
    req = AssistRequest(
        request_id=request_id,
        resource_id=UUID(int=179),
        form=form,
        text=text,
        current=current,
        previous_candidate=prior,
        rule_fields=list(rules),
    )
    context = AssistContext(
        today=date(2026, 11, 25),
        base_month=date(2026, 12, 1),
        days_max=120,
        crew_max=6,
        required_days=2 if form == "dates" else None,
        vat_modes=["included", "excluded"]
        if form == "extra"
        else ["included", "excluded", "exempt"],
    )
    calls = []

    async def stub(*args):
        calls.append(args)
        return ModelInterpretation(
            fields=[
                next((f for f in model if f.field == name), _field(name, intent="no_mention"))
                for name in FORM_FIELDS[form]
            ]
        )

    monkeypatch.setattr(vendor_assist, "interpret", stub)
    trace = {}
    response = asyncio.run(interpret(req, context, use_model=use_model, trace=trace))
    assert len(calls) == int(use_model)
    assert req.current == current and response.request_id == request_id
    return {f.field: f for f in response.fields}, trace


def _assert_set(field, value):
    assert (field.status, field.action, field.value) == ("valid", "set", value), field
    assert not field.alternatives


def _no_offer(field):
    assert field.action not in ("set", "clear") and not field.alternatives, field


def _previous(form, text, request_id, fields, names):
    selected = [fields[name] for name in names]
    assert all(f.status == "valid" and f.action == "set" for f in selected), selected
    return PreviousCandidate(
        request_id=request_id,
        text=text,
        fields=[_field(f.field, f.action, f.value, f.evidence) for f in selected],
        origins={f.field: f.origin_request_id for f in selected},
        sources={f.field: f.source for f in selected},
        baselines={f.field: CASES[form]["current"][f.field] for f in selected},
    )


def _history(monkeypatch, form, use_model, *, update_first=False, exact=False):
    c = CASES[form]
    first, initial, recent = c["first"], c["initial"], c["recent"]
    model = [_set(*first[:3]), _set(*initial)]
    rules = [_field(f.field, "set", f.value, c["seed"]) for f in model]
    if exact:
        assert form == "quote" and use_model and not update_first
        # 실행된 quote-10 T1 규칙은 유효기간을 추출하지 못했다.
        rules = [rules[0]]
    fields, _ = _run(
        monkeypatch,
        form,
        c["seed"],
        model,
        use_model=use_model,
        rules=rules,
        request_id="first",
    )
    _assert_set(fields[first[0]], first[1])
    _assert_set(fields[initial[0]], initial[1])
    p1 = _previous(form, c["seed"], "first", fields, [first[0], initial[0]])
    text = c["update"]
    model = [_set(*recent[:3])]
    rules = [_field(recent[0], "set", recent[1], text)]
    if update_first:
        text += " " + c["changed"][2] + "."
        model.append(_set(*c["changed"]))
        rules.append(_field(c["changed"][0], "set", c["changed"][1], c["changed"][2]))
    fields, _ = _run(
        monkeypatch,
        form,
        text,
        model,
        use_model=use_model,
        rules=rules,
        prior=p1,
        request_id="recent",
    )
    _assert_set(fields[first[0]], c["changed"][1] if update_first else first[1])
    _assert_set(fields[recent[0]], recent[1])
    assert fields[first[0]].origin_request_id == ("recent" if update_first else "first")
    assert fields[recent[0]].origin_request_id == "recent"
    return _previous(form, c["seed"] + "\n" + text, "recent", fields, [first[0], recent[0]])


def _assert_carried(field, prior):
    old = next(f for f in prior.fields if f.field == field.field)
    _assert_set(field, old.value)
    assert field.intent_kind == "retain_previous" and field.value_source == "previous"
    assert field.origin_request_id == prior.origins[field.field]
    assert field.source == prior.sources[field.field] and field.evidence == old.evidence


def _clauses(form, first_word="처음 정했던", recent_word=None):
    c = CASES[form]
    first, recent = c["first"], c["recent"]
    recent_word = recent_word or ("방금 연장한" if form == "quote" else "방금 변경한")
    return f"{first[3]} {first_word} {first[4]}", f"{recent[3]} {recent_word} {recent[4]}"


def _instrumented_followup(
    monkeypatch, form, text, prior, use_model, first_evidence, recent_evidence, **kwargs
):
    c = CASES[form]
    first, recent = c["first"], c["recent"]
    # 실행된 모델은 앞 절을 ambiguous, 뒤 절은 retain_previous로 반환했다.
    model = [
        _field(first[0], "uncertain", evidence=first_evidence, intent="ambiguous"),
        _retain(recent[0], recent_evidence),
    ]
    return _run(monkeypatch, form, text, model, use_model=use_model, prior=prior, **kwargs)


@pytest.mark.parametrize("use_model", [False, True])
def test_exact_executed_quote10_three_turns(monkeypatch, use_model):
    prior = _history(monkeypatch, "quote", True, exact=True)
    first, recent = _clauses("quote")
    assert first == "세금 조건은 처음 정했던 방식"
    assert recent == "만료 날짜는 방금 연장한 날짜"
    fields, trace = _instrumented_followup(
        monkeypatch,
        "quote",
        first + ", " + recent + "로 적용할게요.",
        prior,
        use_model,
        first,
        recent + "로",
    )
    _assert_carried(fields["vat"], prior)
    _assert_carried(fields["valid"], prior)
    assert fields["vat"].origin_request_id == "first"
    assert fields["valid"].origin_request_id == "recent"
    assert trace["previous_rejected"] == {}


@pytest.mark.parametrize("form", list(CASES))
@pytest.mark.parametrize("use_model", [False, True])
@pytest.mark.parametrize("first_word", ["처음 정했던", "처음에 정한", "처음 제안한"])
def test_shared_final_predicate_retains_distinct_first_and_recent_origins(
    monkeypatch, form, use_model, first_word
):
    c = CASES[form]
    prior = _history(monkeypatch, form, use_model)
    first, recent = _clauses(form, first_word)
    fields, trace = _instrumented_followup(
        monkeypatch,
        form,
        first + ", " + recent + "로 적용할게요.",
        prior,
        use_model,
        first,
        recent + "로",
    )
    _assert_carried(fields[c["first"][0]], prior)
    _assert_carried(fields[c["recent"][0]], prior)
    assert trace["previous_rejected"] == {}


@pytest.mark.parametrize("form", list(CASES))
@pytest.mark.parametrize("use_model", [False, True])
@pytest.mark.parametrize(
    "control", ["missing", "stale", "wrong_origin", "updated_first", "wrong_type"]
)
def test_first_reference_is_not_latest_or_other_field(monkeypatch, form, use_model, control):
    c = CASES[form]
    name = c["first"][0]
    prior = _history(monkeypatch, form, use_model, update_first=control == "updated_first")
    current = dict(c["current"])
    first, recent = _clauses(form)
    if control == "missing":
        prior = prior.model_copy(
            update={
                "fields": [f for f in prior.fields if f.field != name],
                "origins": {k: v for k, v in prior.origins.items() if k != name},
                "sources": {k: v for k, v in prior.sources.items() if k != name},
                "baselines": {k: v for k, v in prior.baselines.items() if k != name},
            }
        )
    elif control == "stale":
        current[name] = c["changed"][1]
    elif control == "wrong_origin":
        prior = prior.model_copy(update={"origins": {**prior.origins, name: "recent"}})
    elif control == "wrong_type":
        first = c["first"][3] + " 처음 정했던 " + c["wrong"]
    fields, trace = _instrumented_followup(
        monkeypatch,
        form,
        first + ", " + recent + "로 적용할게요.",
        prior,
        use_model,
        first,
        recent + "로",
        current=current,
    )
    _no_offer(fields[name])
    _assert_carried(fields[c["recent"][0]], prior)
    if control == "stale":
        assert trace["previous_rejected"][name] == "stale_context"


@pytest.mark.parametrize("form", list(CASES))
@pytest.mark.parametrize("use_model", [False, True])
def test_shared_predicate_keeps_current_and_prior_separate(monkeypatch, form, use_model):
    c = CASES[form]
    prior = _history(monkeypatch, form, use_model)
    first = c["first"][3] + " 현재 입력값"
    _, recent = _clauses(form)
    fields, _ = _run(
        monkeypatch,
        form,
        first + ", " + recent + "로 적용할게요.",
        [_retain(c["first"][0], first, "current"), _retain(c["recent"][0], recent + "로")],
        use_model=use_model,
        prior=prior,
    )
    f = fields[c["first"][0]]
    assert (f.status, f.action, f.intent_kind, f.value_source) == (
        "valid",
        "keep",
        "retain_current",
        "current",
    ), f
    assert f.origin_request_id == "followup"
    _assert_carried(fields[c["recent"][0]], prior)


@pytest.mark.parametrize("form", list(CASES))
@pytest.mark.parametrize("use_model", [False, True])
@pytest.mark.parametrize(
    "control", ["inquiry", "condition", "negation", "no_predicate", "standalone"]
)
def test_incomplete_or_unsafe_shared_predicate_never_revives_prior(
    monkeypatch, form, use_model, control
):
    c = CASES[form]
    prior = _history(monkeypatch, form, use_model)
    first, recent = _clauses(form)
    text = first + ", " + recent + "로"
    if control == "inquiry":
        text += " 적용해도 될까요?"
    elif control == "condition":
        text = "가능하면 " + text + " 적용할게요."
    elif control == "negation":
        text += " 적용하지 마세요."
    elif control == "no_predicate":
        text += "."
    else:
        text = first + "."
    # 틀린 모델 유지 판정도 규칙 검증에서 거부해야 한다.
    fields, _ = _run(
        monkeypatch,
        form,
        text,
        [_retain(c["first"][0], first), _retain(c["recent"][0], recent)],
        use_model=use_model,
        prior=prior,
    )
    _no_offer(fields[c["first"][0]])
    if control != "standalone":
        _no_offer(fields[c["recent"][0]])


@pytest.mark.parametrize("form", list(CASES))
@pytest.mark.parametrize("use_model", [False, True])
def test_competing_change_does_not_reintroduce_first_candidate(monkeypatch, form, use_model):
    c = CASES[form]
    prior = _history(monkeypatch, form, use_model)
    first, recent = _clauses(form)
    text = first + ", " + recent + "로 적용할게요. " + c["changed"][2] + "."
    fields, _ = _run(
        monkeypatch,
        form,
        text,
        [_retain(c["first"][0], first), _retain(c["recent"][0], recent + "로")],
        use_model=use_model,
        prior=prior,
    )
    f = fields[c["first"][0]]
    assert not (f.status == "valid" and f.action == "set" and f.value == c["first"][1]), f
    assert not f.alternatives
    _assert_carried(fields[c["recent"][0]], prior)
