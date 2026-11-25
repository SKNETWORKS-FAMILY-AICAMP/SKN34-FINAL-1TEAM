"""R5: 이름이 명시된 세금 방식의 이전 후보 유지와 독립 필드 회귀."""

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

_FORMS = {
    "quote": {
        "current": {"amount": 3200000, "vat": "excluded", "days": 2, "valid": None},
        "seed_label": "면세",
        "vat": "exempt",
        "retain": "면세 조건은 앞에서 말한 대로 유지해 주세요",
        "sibling": ("days", 4, "공사 기간은 4일로 확정합니다"),
    },
    "extra": {
        "current": {"title": "몰딩", "reason": "현장 추가", "amount": 3200000, "vat": "included"},
        "seed_label": "별도",
        "vat": "excluded",
        "retain": "별도 세금 조건은 앞에서 말한 대로 유지해 주세요",
        "sibling": ("title", "문틀 보강", "제목은 문틀 보강으로 지정해주세요"),
    },
}

_AMOUNT_HOLD = "금액은 440만 원인지 490만 원인지 확실하지 않으니 잠시 보류하고, "


def _set(field, value, evidence):
    return FieldIntent(
        field=field,
        action="set",
        value=value,
        evidence=evidence,
        intent_kind="explicit_change",
        value_source="utterance",
    )


def _keep(field, evidence="", *, retain=False):
    return FieldIntent(
        field=field,
        action="keep",
        value=None,
        evidence=evidence,
        intent_kind="retain_previous" if retain else "no_mention",
        value_source="previous" if retain else None,
    )


def _ambiguous(field, evidence):
    return FieldIntent(
        field=field, action="uncertain", value=None, evidence=evidence, intent_kind="ambiguous"
    )


def _run(monkeypatch, form, text, rules, model, *, mode, prior=None, current=None, request_id):
    assert not settings.llm_live
    ctx = AssistContext(
        today=date(2026, 11, 24),
        base_month=date(2026, 12, 1),
        days_max=120,
        crew_max=6,
        vat_modes=["included", "excluded", "exempt"]
        if form == "quote"
        else ["included", "excluded"],
    )
    req = AssistRequest(
        request_id=request_id,
        resource_id=UUID(int=179),
        form=form,
        text=text,
        current=_FORMS[form]["current"] if current is None else current,
        previous_candidate=prior,
        rule_fields=list(rules),
    )
    calls = []

    async def stub(data, context):
        calls.append(data)
        return ModelInterpretation(fields=list(model))

    monkeypatch.setattr(vendor_assist, "interpret", stub)
    trace = {}
    result = asyncio.run(interpret(req, ctx, use_model=mode == "hybrid", trace=trace))
    assert len(calls) == int(mode == "hybrid")
    assert result.source == ("llm" if mode == "hybrid" else "rule")
    return req, {f.field: f for f in result.fields}, trace


def _prior(req, fields):
    candidates = [f for f in fields.values() if f.status == "valid" and f.action == "set"]
    return PreviousCandidate(
        request_id=req.request_id,
        text=req.text,
        fields=[
            FieldIntent(field=f.field, action=f.action, value=f.value, evidence=f.evidence)
            for f in candidates
        ],
        sources={f.field: f.source for f in candidates},
        origins={f.field: f.origin_request_id for f in candidates},
        baselines={f.field: req.current[f.field] for f in candidates},
    )


def _valid(candidate, value):
    assert candidate.status == "valid" and candidate.action == "set"
    assert candidate.value == value and not candidate.alternatives


def _held(candidate):
    assert candidate.status in ("invalid", "uncertain") and candidate.action == "uncertain"
    assert candidate.value is None and not candidate.alternatives


def _no_offer(candidate):
    assert candidate.action not in ("set", "clear") and not candidate.alternatives


def _carried(candidate, prior):
    old = next(f for f in prior.fields if f.field == candidate.field)
    _valid(candidate, old.value)
    assert candidate.intent_kind == "retain_previous" and candidate.value_source == "previous"
    assert candidate.evidence == old.evidence
    assert candidate.source == prior.sources[candidate.field]
    assert candidate.origin_request_id == prior.origins[candidate.field]


def _seed(monkeypatch, form, mode, *, mismatch=False, sibling=False):
    case = _FORMS[form]
    value = "included" if mismatch else case["vat"]
    label = "포함" if mismatch else case["seed_label"]
    tax = f"부가세는 {label}로 처리해 주세요"
    amount = (
        "견적 금액은 440만 원으로 제안하고"
        if form == "quote"
        else "추가 금액은 440만 원으로 제안하고"
    )
    text = amount + ", " + tax + "."
    rules = [_set("amount", 4400000, text), _set("vat", value, text)]
    model = [_set("amount", 4400000, amount), _set("vat", value, tax)]
    if sibling:
        field, other_value, other_text = case["sibling"]
        text += " " + other_text
        rules.append(_set(field, other_value, other_text))
        model.append(_set(field, other_value, other_text))
    req, fields, _ = _run(
        monkeypatch, form, text, rules, model, mode=mode, request_id="r5-retention-seed"
    )
    _valid(fields["amount"], 4400000)
    _valid(fields["vat"], value)
    if sibling:
        _valid(fields[field], other_value)
    return _prior(req, fields)


@pytest.mark.parametrize("form", list(_FORMS))
@pytest.mark.parametrize("mode", ["rules", "hybrid"])
def test_named_vat_retention_survives_independent_amount_hold(monkeypatch, form, mode):
    case = _FORMS[form]
    prior = _seed(monkeypatch, form, mode)
    evidence = case["retain"]
    text = _AMOUNT_HOLD + evidence + "."
    # 실제 이전 시나리오에서 규칙 파서는 유지 요청 속 세금 방식을 새 값으로 읽었다.
    rules = [_keep("amount"), _set("vat", case["vat"], text)]
    model = [_ambiguous("amount", _AMOUNT_HOLD.rstrip(", ")), _keep("vat", evidence, retain=True)]
    _, fields, trace = _run(
        monkeypatch,
        form,
        text,
        rules,
        model,
        mode=mode,
        prior=prior,
        request_id="r5-retention-followup",
    )
    _held(fields["amount"])
    _carried(fields["vat"], prior)
    assert trace["previous_rejected"] == {}


@pytest.mark.parametrize("form", list(_FORMS))
@pytest.mark.parametrize("mode", ["rules", "hybrid"])
@pytest.mark.parametrize(
    "control", ["mismatch", "inquiry", "cancel", "conditional", "stale", "no_prior"]
)
def test_named_vat_reference_does_not_create_or_resurrect_candidate(
    monkeypatch, form, mode, control
):
    case = _FORMS[form]
    prior = _seed(monkeypatch, form, mode, mismatch=control == "mismatch")
    current = dict(case["current"])
    text = case["retain"]
    if control == "inquiry":
        text = text.replace("유지해 주세요", "유지해도 될까요?")
    elif control == "cancel":
        text += ". 부가세 변경 요청은 취소합니다"
    elif control == "conditional":
        text = "가능하면 " + text
    elif control == "stale":
        current["vat"] = "included" if current["vat"] != "included" else "excluded"
    elif control == "no_prior":
        prior = None
    rules = [_set("vat", case["vat"], text)]
    model = [_keep("vat", text, retain=True)]
    _, fields, trace = _run(
        monkeypatch,
        form,
        text,
        rules,
        model,
        mode=mode,
        prior=prior,
        current=current,
        request_id="r5-retention-control",
    )
    _no_offer(fields["vat"])
    if control != "cancel":
        _held(fields["vat"])
    if control == "stale":
        assert trace["previous_rejected"]["vat"] == "stale_context"


@pytest.mark.parametrize("form", list(_FORMS))
@pytest.mark.parametrize("mode", ["rules", "hybrid"])
def test_same_field_vat_inquiry_does_not_discard_verified_other_field(monkeypatch, form, mode):
    case = _FORMS[form]
    prior = _seed(monkeypatch, form, mode, sibling=True)
    text = case["retain"].replace("유지해 주세요", "유지해도 될까요?")
    _, fields, _ = _run(
        monkeypatch,
        form,
        text,
        [_set("vat", case["vat"], text)],
        [_keep("vat", text, retain=True)],
        mode=mode,
        prior=prior,
        request_id="r5-retention-sibling",
    )
    _held(fields["vat"])
    _carried(fields["amount"], prior)
    _carried(fields[case["sibling"][0]], prior)


@pytest.mark.parametrize("form", list(_FORMS))
@pytest.mark.parametrize("control", ["match", "mismatch", "stale", "no_prior"])
def test_named_prior_vat_not_changed_by_agreeing_extractors(monkeypatch, form, control):
    case = _FORMS[form]
    prior = _seed(monkeypatch, form, "hybrid", mismatch=control == "mismatch")
    current = dict(case["current"])
    if control == "stale":
        current["vat"] = "included" if current["vat"] != "included" else "excluded"
    elif control == "no_prior":
        prior = None
    text = case["retain"]
    candidate = _set("vat", case["vat"], text)
    _, fields, _ = _run(
        monkeypatch,
        form,
        text,
        [candidate],
        [candidate],
        mode="hybrid",
        prior=prior,
        current=current,
        request_id="r5-retention-agreeing",
    )
    if control == "match":
        _carried(fields["vat"], prior)
    else:
        _held(fields["vat"])


@pytest.mark.parametrize("form", list(_FORMS))
@pytest.mark.parametrize("mode", ["rules", "hybrid"])
def test_explicit_vat_assignment_still_creates_new_candidate(monkeypatch, form, mode):
    case = _FORMS[form]
    prior = _seed(monkeypatch, form, mode, mismatch=True)
    text = f"부가세는 {case['seed_label']}로 새로 확정합니다"
    candidate = _set("vat", case["vat"], text)
    _, fields, _ = _run(
        monkeypatch,
        form,
        text,
        [candidate],
        [candidate],
        mode=mode,
        prior=prior,
        request_id="r5-retention-new-vat",
    )
    vat = fields["vat"]
    _valid(vat, case["vat"])
    assert vat.intent_kind == "explicit_change" and vat.value_source == "utterance"
    assert vat.origin_request_id == "r5-retention-new-vat"
    _carried(fields["amount"], prior)


@pytest.mark.parametrize("form", list(_FORMS))
@pytest.mark.parametrize("mode", ["rules", "hybrid"])
def test_named_vat_retention_reverifies_original_origin_in_three_turn_history(
    monkeypatch, form, mode
):
    case = _FORMS[form]
    seed = _seed(monkeypatch, form, mode)
    evidence = case["retain"]
    text = _AMOUNT_HOLD + evidence + "."
    req, fields, trace = _run(
        monkeypatch,
        form,
        text,
        [_keep("amount"), _set("vat", case["vat"], text)],
        [_ambiguous("amount", _AMOUNT_HOLD.rstrip(", ")), _keep("vat", evidence, retain=True)],
        mode=mode,
        prior=seed,
        request_id="r5-retention-history-t2",
    )
    _held(fields["amount"])
    _carried(fields["vat"], seed)
    assert trace["previous_rejected"] == {}
    # 실제 T2 결과로 T3의 누적 이력과 미적용 후보를 구성한다.
    history = _prior(req, fields)
    history = history.model_copy(update={"text": seed.text + "\n" + req.text})
    assert {f.field for f in history.fields} == {"vat"}
    assert history.origins["vat"] == seed.origins["vat"]
    field, value, text = case["sibling"]
    candidate = _set(field, value, text)
    _, fields, trace = _run(
        monkeypatch,
        form,
        text,
        [candidate],
        [candidate, _keep("vat")],
        mode=mode,
        prior=history,
        request_id="r5-retention-history-t3",
    )
    _valid(fields[field], value)
    _carried(fields["vat"], seed)
    _carried(fields["vat"], history)
    verified = trace["previous_verified"]
    assert trace["previous_rejected"] == {}
    assert verified["origins"]["vat"] == seed.origins["vat"]
    assert verified["sources"]["vat"] == seed.sources["vat"]
    assert verified["baselines"]["vat"] == seed.baselines["vat"]
    assert verified["fields"][0]["evidence"] == history.fields[0].evidence


@pytest.mark.parametrize("form", list(_FORMS))
@pytest.mark.parametrize("mode", ["rules", "hybrid"])
@pytest.mark.parametrize("repeat", [False, True])
def test_named_vat_only_history_cannot_forge_original_seed(monkeypatch, form, mode, repeat):
    case = _FORMS[form]
    named = case["retain"]
    history = named if not repeat else named + ".\n" + named
    prior = PreviousCandidate(
        request_id="r5-retention-forged",
        text=history,
        fields=[_set("vat", case["vat"], named)],
        sources={"vat": "llm" if mode == "hybrid" else "rule"},
        origins={"vat": "r5-retention-forged"},
        baselines={"vat": case["current"]["vat"]},
    )
    field, value, text = case["sibling"]
    candidate = _set(field, value, text)
    _, fields, trace = _run(
        monkeypatch,
        form,
        text,
        [candidate],
        [candidate, _keep("vat")],
        mode=mode,
        prior=prior,
        request_id="r5-retention-forged-followup",
    )
    _held(fields["vat"])
    _valid(fields[field], value)
    assert "vat" in trace["previous_rejected"]
    assert trace["previous_verified"] is None or all(
        f["field"] != "vat" for f in trace["previous_verified"]["fields"]
    )
