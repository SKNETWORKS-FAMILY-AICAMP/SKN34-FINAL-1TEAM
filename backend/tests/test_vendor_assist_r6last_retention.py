"""실행된 quote-09와 독립적인 이전 후보 유지 대조. 실제 서비스, 모델 스텁."""

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
        "seed": "일단 견적 금액은 693만 5천 원, 기간은 12일로 제시할게요.",
        "other": ("amount", 6935000, "견적 금액은 693만 5천 원"),
        "keep": ("days", 12, "기간은 12일", "기간"),
        "uncertain": "금액 710만원이나 730만원 중에 고민하고 있어요",
        "wrong": "금액",
        "replacement": "기간은 9일로 변경합니다.",
    },
    "period": {
        "current": {"start": "2026-12-02", "end": "2026-12-24", "excluded": []},
        "seed": "시작일은 12월 5일, 종료일은 12월 22일로 제안합니다.",
        "other": ("start", "2026-12-05", "시작일은 12월 5일"),
        "keep": ("end", "2026-12-22", "종료일은 12월 22일", "종료일"),
        "uncertain": "시작일은 12월 3일이나 4일 중에 고민하고 있어요",
        "wrong": "금액",
        "replacement": "종료일은 12월 23일로 변경합니다.",
    },
    "dates": {
        "current": {"days": ["2026-12-09", "2026-12-16"], "crew": 2},
        "seed": "시공 날짜는 12월 8일과 21일, 인원은 3명으로 제안합니다.",
        "other": ("crew", 3, "인원은 3명"),
        "keep": ("days", ["2026-12-08", "2026-12-21"], "시공 날짜는 12월 8일과 21일", "시공 날짜"),
        "uncertain": "인원은 4명이나 5명 중에 고민하고 있어요",
        "wrong": "금액",
        "replacement": "시공 날짜는 12월 11일과 24일로 변경합니다.",
    },
    "extra": {
        "current": {"title": "보강", "reason": "현장 추가", "amount": 1000000, "vat": "included"},
        "seed": "추가 사유는 문짝 하단 긁힘, 비용은 28만 6천 원으로 먼저 띄워주세요.",
        "other": ("amount", 286000, "비용은 28만 6천 원"),
        "keep": ("reason", "문짝 하단 긁힘", "추가 사유는 문짝 하단 긁힘", "사유"),
        "uncertain": "추가금 31만원이나 35만원 중에 고민하고 있어요",
        "wrong": "인원",
        "replacement": "사유는 바닥 파손으로 변경합니다.",
    },
}

VARIANTS = [
    "앞서 잡은 대로 둬주세요",
    "앞서 잡은대로 둬 주세요",
    "앞서 제시한 값 그대로 둬 주세요",
    "앞서 정한 대로 두세요",
    "앞서 잡은 대로 두어 주세요",
    "앞서 제안한 값 그대로 둘게요",
    "앞서 정한 대로 두겠습니다",
]


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


def _seed(monkeypatch, form, use_model):
    c = CASES[form]
    model = [_set(*c["other"]), _set(*c["keep"][:3])]
    rules = [
        _field(f.field, "set", f.value, c["seed"] if form in ("quote", "extra") else f.evidence)
        for f in model
    ]
    fields, _ = _run(
        monkeypatch, form, c["seed"], model, use_model=use_model, rules=rules, request_id="seed"
    )
    selected = [fields[f.field] for f in model]
    for f in model:
        _assert_set(fields[f.field], f.value)
    return PreviousCandidate(
        request_id="seed",
        text=c["seed"],
        fields=[_field(f.field, f.action, f.value, f.evidence) for f in selected],
        origins={f.field: f.origin_request_id for f in selected},
        sources={f.field: f.source for f in selected},
        baselines={f.field: c["current"][f.field] for f in selected},
    )


def _carried(field, prior):
    old = next(f for f in prior.fields if f.field == field.field)
    _assert_set(field, old.value)
    assert field.intent_kind == "retain_previous" and field.value_source == "previous"
    assert field.origin_request_id == prior.origins[field.field]
    assert field.source == prior.sources[field.field] and field.evidence == old.evidence


def _uncertain(c):
    return _field(c["other"][0], "uncertain", evidence=c["uncertain"], intent="ambiguous")


def _subject(label):
    return label + ("는" if label.endswith(("날짜", "사유")) else "은")


@pytest.mark.parametrize("form", list(CASES))
@pytest.mark.parametrize("use_model", [False, True])
@pytest.mark.parametrize("phrase", VARIANTS)
def test_prior_retention_with_independent_uncertainty(monkeypatch, form, use_model, phrase):
    c = CASES[form]
    prior = _seed(monkeypatch, form, use_model)
    retain = _subject(c["keep"][3]) + " " + phrase
    # 정확한 quote-09 T2도 이 데이터에 포함된다. 실행된 모델 근거를 유지한다.
    text = c["uncertain"] + ". " + retain + "."
    fields, trace = _run(
        monkeypatch,
        form,
        text,
        [_uncertain(c), _retain(c["keep"][0], retain)],
        use_model=use_model,
        prior=prior,
    )
    _carried(fields[c["keep"][0]], prior)
    _no_offer(fields[c["other"][0]])
    assert fields[c["other"][0]].status != "valid", fields[c["other"][0]]
    assert trace["previous_rejected"] == {}


@pytest.mark.parametrize("form", list(CASES))
@pytest.mark.parametrize("use_model", [False, True])
def test_explicit_current_not_previous(monkeypatch, form, use_model):
    c = CASES[form]
    prior = _seed(monkeypatch, form, use_model)
    retain = _subject(c["keep"][3]) + " 현재 입력값 그대로 둬 주세요"
    fields, _ = _run(
        monkeypatch,
        form,
        c["uncertain"] + ". " + retain + ".",
        [_uncertain(c), _retain(c["keep"][0], retain, "current")],
        use_model=use_model,
        prior=prior,
    )
    field = fields[c["keep"][0]]
    assert (field.status, field.action, field.intent_kind, field.value_source) == (
        "valid",
        "keep",
        "retain_current",
        "current",
    ), field
    assert field.origin_request_id == "followup"
    _no_offer(fields[c["other"][0]])


@pytest.mark.parametrize("form", list(CASES))
@pytest.mark.parametrize("use_model", [False, True])
@pytest.mark.parametrize(
    "control", ["missing", "stale", "wrong_type", "forged_evidence", "missing_baseline"]
)
def test_prior_retention_requires_eligible_same_field(monkeypatch, form, use_model, control):
    c = CASES[form]
    prior = _seed(monkeypatch, form, use_model)
    name, value, _, label = c["keep"]
    current = dict(c["current"])
    retain = _subject(label) + " 앞서 잡은 대로 둬주세요"
    if control == "missing":
        prior = None
    elif control == "stale":
        current[name] = value
    elif control == "wrong_type":
        retain = _subject(label) + " 앞서 정한 " + c["wrong"] + " 그대로 둬주세요"
    elif control == "forged_evidence":
        prior = prior.model_copy(
            update={
                "fields": [
                    f.model_copy(update={"evidence": "원문에 없는 근거"}) if f.field == name else f
                    for f in prior.fields
                ]
            }
        )
    else:
        prior = prior.model_copy(
            update={"baselines": {key: val for key, val in prior.baselines.items() if key != name}}
        )
    fields, trace = _run(
        monkeypatch,
        form,
        c["uncertain"] + ". " + retain + ".",
        [_uncertain(c), _retain(name, retain)],
        use_model=use_model,
        prior=prior,
        current=current,
    )
    _no_offer(fields[name])
    _no_offer(fields[c["other"][0]])
    if control in ("stale", "missing_baseline"):
        assert trace["previous_rejected"][name] == "stale_context"


@pytest.mark.parametrize("form", list(CASES))
@pytest.mark.parametrize("use_model", [False, True])
@pytest.mark.parametrize(
    "suffix",
    [
        "둬도 될까요?",
        "두지 마세요.",
        "안 둘게요.",
        "둬주세요. {label} 변경은 취소합니다.",
        "둬주세요. {replacement}",
        "두면 좋을 것 같아요.",
    ],
)
def test_uncertain_or_competing_previous_retention(monkeypatch, form, use_model, suffix):
    c = CASES[form]
    prior = _seed(monkeypatch, form, use_model)
    name, value, _, label = c["keep"]
    retain = (
        _subject(label)
        + " 앞서 잡은 대로 "
        + suffix.format(label=label, replacement=c["replacement"])
    )
    fields, _ = _run(
        monkeypatch,
        form,
        c["uncertain"] + ". " + retain,
        [_uncertain(c), _retain(name, retain)],
        use_model=use_model,
        prior=prior,
    )
    field = fields[name]
    assert not (field.status == "valid" and field.action == "set" and field.value == value), field
    assert not field.alternatives
    _no_offer(fields[c["other"][0]])


@pytest.mark.parametrize("form", list(CASES))
@pytest.mark.parametrize("use_model", [False, True])
def test_conditional_keep_cannot_promote_previous(monkeypatch, form, use_model):
    c = CASES[form]
    prior = _seed(monkeypatch, form, use_model)
    name, _, _, label = c["keep"]
    retain = "가능하면 " + _subject(label) + " 앞서 잡은 대로 둬 주세요"
    fields, _ = _run(
        monkeypatch,
        form,
        c["uncertain"] + ". " + retain + ".",
        [_uncertain(c), _retain(name, retain)],
        use_model=use_model,
        prior=prior,
    )
    _no_offer(fields[name])
