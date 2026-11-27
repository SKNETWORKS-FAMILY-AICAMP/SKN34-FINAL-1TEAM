"""실행된 R6 연결절 유지 회귀와 독립 대조. 서비스 모델 호출은 스텁만 사용한다."""

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
    "period": (
        {"start": "2026-12-02", "end": "2026-12-24", "excluded": []},
        "시작일은 12월 5일, 종료일은 12월 22일로 제안합니다.",
        ("start", "2026-12-05", "시작일은 12월 5일", "시작일"),
        ("end", "2026-12-22", "종료일은 12월 22일", "종료일", "날짜"),
    ),
    "quote": (
        {"amount": 4550000, "days": 3, "vat": "included", "valid": "2026-12-17"},
        "공사 기간은 6일, 견적 금액은 526만원으로 제안합니다.",
        ("days", 6, "공사 기간은 6일", "공사 기간"),
        ("amount", 5260000, "견적 금액은 526만원", "견적 금액", "금액"),
    ),
    "dates": (
        {"days": ["2026-12-09", "2026-12-16"], "crew": 2},
        "시공 날짜는 12월 8일과 21일, 인원은 3명으로 제안합니다.",
        ("crew", 3, "인원은 3명", "인원"),
        ("days", ["2026-12-08", "2026-12-21"], "시공 날짜는 12월 8일과 21일", "시공 날짜", "날짜"),
    ),
    "extra": (
        {"title": "보강", "reason": "현장 추가", "amount": 1000000, "vat": "included"},
        "추가 사유는 문짝 하단 긁힘, 비용은 28만 6천 원으로 먼저 띄워주세요.",
        ("reason", "문짝 하단 긁힘", "추가 사유는 문짝 하단 긁힘", "사유"),
        ("amount", 286000, "비용은 28만 6천 원", "추가금", "금액"),
    ),
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


def _retain(name, evidence, reference):
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
    current = CASES[form][0] if current is None else current
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
    assert req.current == current
    assert response.request_id == request_id
    return {f.field: f for f in response.fields}, trace


def _assert_set(field, value):
    assert (field.status, field.action, field.value) == ("valid", "set", value), field
    assert not field.alternatives


def _assert_not_offered(field):
    assert field.action not in ("set", "clear") and not field.alternatives, field


def _assert_release(field):
    assert (field.status, field.action, field.intent_kind, field.value_source) == (
        "valid",
        "keep",
        "retain_current",
        "current",
    ), field
    assert field.origin_request_id == "followup"
    assert field.value is None and not field.alternatives


def _assert_carried(field, prior):
    old = next(f for f in prior.fields if f.field == field.field)
    _assert_set(field, old.value)
    assert field.intent_kind == "retain_previous" and field.value_source == "previous"
    assert field.origin_request_id == prior.origins[field.field]
    assert field.source == prior.sources[field.field]
    assert field.evidence == old.evidence


def _seed(monkeypatch, form, use_model):
    current, text, drop, keep = CASES[form]
    model = [_set(*drop[:3]), _set(*keep[:3])]
    # 실행된 extra-07 T1의 규칙 근거는 전체 발화였다.
    rules = [
        _field(name, "set", value, text if form == "extra" else evidence)
        for name, value, evidence in (drop[:3], keep[:3])
    ]
    fields, _ = _run(
        monkeypatch, form, text, model, use_model=use_model, rules=rules, request_id="seed"
    )
    for f in model:
        _assert_set(fields[f.field], f.value)
    selected = [fields[f.field] for f in model]
    return PreviousCandidate(
        request_id="seed",
        text=text,
        fields=[_field(f.field, f.action, f.value, f.evidence) for f in selected],
        origins={f.field: f.origin_request_id for f in selected},
        sources={f.field: f.source for f in selected},
        baselines={f.field: current[f.field] for f in selected},
    )


def _followup(form):
    _, _, drop, keep = CASES[form]
    cancel = f"{drop[3]} 바꾸는 건 없던 걸로"
    retain = (
        f"{keep[3]}은 아까 {keep[4]}으로 반영"
        if keep[4] == "금액"
        else f"{keep[3]}은 아까 {keep[4]}로 반영"
    )
    # extra-07의 실행된 문장 및 잘린 모델 근거와 동일하다.
    return cancel, retain, cancel + " 하고 " + retain + " 바랍니다."


@pytest.mark.parametrize("form", list(CASES))
@pytest.mark.parametrize("use_model", [False, True])
def test_connected_withdrawal_and_typed_previous(monkeypatch, form, use_model):
    prior = _seed(monkeypatch, form, use_model)
    _, _, drop, keep = CASES[form]
    cancel, retain, text = _followup(form)
    fields, trace = _run(
        monkeypatch,
        form,
        text,
        [_retain(drop[0], cancel, "current"), _retain(keep[0], retain, "previous")],
        use_model=use_model,
        prior=prior,
    )
    _assert_release(fields[drop[0]])
    _assert_carried(fields[keep[0]], prior)
    assert trace["previous_rejected"] == {}
    for name in FORM_FIELDS[form]:
        if name not in (drop[0], keep[0]):
            _assert_not_offered(fields[name])


@pytest.mark.parametrize("form", list(CASES))
@pytest.mark.parametrize("use_model", [False, True])
@pytest.mark.parametrize(
    "control", ["missing", "stale", "wrong_type", "forged_evidence", "missing_baseline"]
)
def test_connected_previous_requires_same_field_proof(monkeypatch, form, use_model, control):
    prior = _seed(monkeypatch, form, use_model)
    base, _, drop, keep = CASES[form]
    current = dict(base)
    cancel, retain, text = _followup(form)
    if control == "missing":
        # 다른 필드의 후보는 남아 있어도 유지할 대상의 후보는 없다.
        prior = prior.model_copy(
            update={
                "fields": [f for f in prior.fields if f.field != keep[0]],
                "sources": {
                    name: value for name, value in prior.sources.items() if name != keep[0]
                },
                "origins": {
                    name: value for name, value in prior.origins.items() if name != keep[0]
                },
                "baselines": {
                    name: value for name, value in prior.baselines.items() if name != keep[0]
                },
            }
        )
    elif control == "stale":
        current[keep[0]] = keep[1]
    elif control == "wrong_type":
        wrong = "인원" if keep[4] == "금액" else "금액"
        text = text.replace("아까 " + keep[4], "아까 " + wrong)
        retain = retain.replace("아까 " + keep[4], "아까 " + wrong)
    elif control == "forged_evidence":
        prior = prior.model_copy(
            update={
                "fields": [
                    f.model_copy(update={"evidence": "원문에 없는 근거"})
                    if f.field == keep[0]
                    else f
                    for f in prior.fields
                ]
            }
        )
    else:
        prior = prior.model_copy(
            update={
                "baselines": {
                    name: value for name, value in prior.baselines.items() if name != keep[0]
                }
            }
        )
    fields, trace = _run(
        monkeypatch,
        form,
        text,
        [_retain(drop[0], cancel, "current"), _retain(keep[0], retain, "previous")],
        use_model=use_model,
        prior=prior,
        current=current,
    )
    _assert_not_offered(fields[keep[0]])
    _assert_release(fields[drop[0]])
    if control in ("stale", "missing_baseline"):
        assert trace["previous_rejected"][keep[0]] == "stale_context"


@pytest.mark.parametrize("form", list(CASES))
@pytest.mark.parametrize("use_model", [False, True])
@pytest.mark.parametrize(
    "suffix",
    [
        "반영해도 될까요?",
        "가능하면 반영 바랍니다.",
        "반영하지 마세요.",
        "반영 바랍니다. {label} 변경은 취소합니다.",
    ],
)
def test_unsafe_or_competing_retention_does_not_restore_previous(
    monkeypatch, form, use_model, suffix
):
    prior = _seed(monkeypatch, form, use_model)
    _, _, drop, keep = CASES[form]
    cancel, retain, _ = _followup(form)
    retain = retain.removesuffix("반영") + suffix.format(label=keep[3])
    fields, _ = _run(
        monkeypatch,
        form,
        cancel + " 하고 " + retain,
        [_retain(drop[0], cancel, "current"), _retain(keep[0], retain, "previous")],
        use_model=use_model,
        prior=prior,
    )
    _assert_not_offered(fields[keep[0]])


@pytest.mark.parametrize("use_model", [False, True])
@pytest.mark.parametrize(
    "cancel",
    [
        "사유 바꾸는 건 없던 걸로 할까요?",
        "사유 바꾸는 건 없던 걸로 하지 마세요.",
        "가능하면 사유 바꾸는 건 없던 걸로 해주세요.",
        "사유 바꾸는 건 없던 걸로.",
        "사유 바꾸는 건 없던 걸로 하고 사유는 바닥 파손으로 바꾸겠습니다.",
    ],
)
def test_withdrawal_requires_completed_unambiguous_intent(monkeypatch, use_model, cancel):
    prior = _seed(monkeypatch, "extra", use_model)
    retain = "추가금은 아까 금액으로 반영 바랍니다."
    fields, _ = _run(
        monkeypatch,
        "extra",
        cancel + " " + retain,
        [_retain("reason", cancel, "current"), _retain("amount", retain, "previous")],
        use_model=use_model,
        prior=prior,
    )
    assert not (
        fields["reason"].status == "valid" and fields["reason"].intent_kind == "retain_current"
    )
    assert not (fields["reason"].action == "set" and fields["reason"].value == "문짝 하단 긁힘")
    _assert_carried(fields["amount"], prior)


@pytest.mark.parametrize("use_model", [False, True])
def test_executed_period08_tentative_remains_held_and_independent_end(monkeypatch, use_model):
    first = "실측 시작 날짜를 12월 7일로 잠정 제시합니다."
    ambiguous = _field(
        "start", "uncertain", evidence="실측 시작 날짜를 12월 7일로 잠정 제시", intent="ambiguous"
    )
    fields, _ = _run(monkeypatch, "period", first, [ambiguous], use_model=use_model)
    # 현재의 안전 정책: 잠정 제안을 유효 후보로 승격하지 않는다. 평가 gold는 수정하지 않는다.
    _assert_not_offered(fields["start"])
    text = "첫 실측일은 아까 후보를 쓰고 접수 마감은 12월 30일로 잡아주세요."
    # 실행된 T2의 규칙은 같은 날짜를 두 경계에 제안했다. 모델도 두 필드 보류.
    rules = [_field(name, "set", "2026-12-30", text) for name in ("start", "end")]
    model = [
        _field("start", "uncertain", evidence="첫 실측일은 아까 후보를 쓰고", intent="ambiguous"),
        _field(
            "end", "uncertain", evidence="접수 마감은 12월 30일로 잡아주세요", intent="ambiguous"
        ),
    ]
    fields, _ = _run(monkeypatch, "period", text, model, use_model=use_model, rules=rules)
    _assert_not_offered(fields["start"])
    assert fields["start"].status != "valid"
    _assert_set(fields["end"], "2026-12-30")
    assert fields["end"].origin_request_id == "followup"
