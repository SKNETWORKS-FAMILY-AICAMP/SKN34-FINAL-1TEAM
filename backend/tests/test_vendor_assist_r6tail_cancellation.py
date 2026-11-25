"""실행된 quote-12와 필드별 후보 철회의 독립 서비스 회귀. 실 모델/DB 없음."""

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
        "seed": "세금은 면세로 처리하고 견적 금액은 815만 7천 원으로 초안 내주세요.",
        "drop": ("vat", "exempt", "세금은 면세로 처리", "세금 조건", "부가세 방식"),
        "keep": ("amount", 8157000, "견적 금액은 815만 7천 원", "금액", "견적액"),
        "changed": ("vat", "excluded", "세금은 별도 조건으로 변경합니다"),
    },
    "period": {
        "current": {"start": "2026-12-02", "end": "2026-12-24", "excluded": []},
        "seed": "시작일은 12월 5일로 제안합니다. 제외일은 12월 9일과 21일로 제안합니다.",
        "drop": (
            "excluded",
            ["2026-12-09", "2026-12-21"],
            "제외일은 12월 9일과 21일",
            "제외일",
            "방문 제외 날짜",
        ),
        "keep": ("start", "2026-12-05", "시작일은 12월 5일", "시작일", "첫 실측일"),
        "changed": (
            "excluded",
            ["2026-12-10", "2026-12-20"],
            "제외일은 12월 10일과 20일로 변경합니다",
        ),
    },
    "dates": {
        "current": {"days": ["2026-12-09", "2026-12-16"], "crew": 2},
        "seed": "시공 날짜는 12월 8일과 21일, 인원은 3명으로 제안합니다.",
        "drop": ("crew", 3, "인원은 3명", "인원", "투입 인원"),
        "keep": (
            "days",
            ["2026-12-08", "2026-12-21"],
            "시공 날짜는 12월 8일과 21일",
            "시공 날짜",
            "작업일",
        ),
        "changed": ("crew", 4, "인원은 4명으로 변경합니다"),
    },
    "extra": {
        "current": {"title": "보강", "reason": "현장 추가", "amount": 1000000, "vat": "included"},
        "seed": "추가 사유는 문짝 하단 긁힘, 비용은 28만 6천 원으로 먼저 띄워주세요.",
        "drop": ("reason", "문짝 하단 긁힘", "추가 사유는 문짝 하단 긁힘", "추가 사유", "이유"),
        "keep": ("amount", 286000, "비용은 28만 6천 원", "추가금", "비용"),
        "changed": ("reason", "바닥 파손", "사유는 바닥 파손으로 변경합니다"),
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


def _ambiguous(name, evidence):
    return _field(name, "uncertain", evidence=evidence, intent="ambiguous")


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
    # current은 복사해 실제 해석이 입력 값을 변경하지 않는 것도 대조한다.
    req = AssistRequest(
        request_id=request_id,
        resource_id=UUID(int=179),
        form=form,
        text=text,
        current=current,
        previous_candidate=prior,
        rule_fields=[
            next((f for f in rules if f.field == name), _field(name)) for name in FORM_FIELDS[form]
        ],
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
    before = req.model_dump(mode="json")
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
    assert req.model_dump(mode="json") == before
    assert response.request_id == request_id
    return {f.field: f for f in response.fields}, trace


def _assert_set(field, value):
    assert (field.status, field.action, field.value) == ("valid", "set", value), field
    assert not field.alternatives


def _no_offer(field):
    assert field.action not in ("set", "clear") and not field.alternatives, field


def _not_released(field):
    assert not (
        field.status == "valid" and field.action == "keep" and field.intent_kind == "retain_current"
    ), field


def _assert_release(field, text):
    assert (field.status, field.action, field.intent_kind, field.value_source) == (
        "valid",
        "keep",
        "retain_current",
        "current",
    ), field
    assert field.origin_request_id == "followup"
    assert field.value is None and not field.alternatives
    assert field.evidence.strip() and field.evidence.strip() in text


def _previous(form, text, request_id, fields, current):
    selected = [f for f in fields.values() if f.status == "valid" and f.action == "set"]
    return PreviousCandidate(
        request_id=request_id,
        text=text,
        fields=[_field(f.field, f.action, f.value, f.evidence) for f in selected],
        origins={f.field: f.origin_request_id for f in selected},
        sources={f.field: f.source for f in selected},
        baselines={f.field: current[f.field] for f in selected},
    )


def _seed(monkeypatch, form, use_model, *, case=None):
    c = CASES[form] if case is None else case
    fields = [_set(*c["drop"][:3]), _set(*c["keep"][:3])]
    # 실제 quote-12 T1의 규칙 근거는 전체 발화였다. 독립 날짜 대조는 절별 근거.
    rules = [
        _field(f.field, "set", f.value, c["seed"] if form in ("quote", "extra") else f.evidence)
        for f in fields
    ]
    reviewed, _ = _run(
        monkeypatch,
        form,
        c["seed"],
        fields,
        use_model=use_model,
        current=c["current"],
        rules=rules,
        request_id="seed",
    )
    for field in fields:
        _assert_set(reviewed[field.field], field.value)
    return _previous(form, c["seed"], "seed", reviewed, c["current"])


def _carried(field, prior):
    old = next(f for f in prior.fields if f.field == field.field)
    _assert_set(field, old.value)
    assert field.intent_kind == "retain_previous" and field.value_source == "previous"
    assert field.origin_request_id == prior.origins[field.field]
    assert field.source == prior.sources[field.field] and field.evidence == old.evidence


def _omit(prior, name):
    return prior.model_copy(
        update={
            "fields": [f for f in prior.fields if f.field != name],
            "origins": {k: v for k, v in prior.origins.items() if k != name},
            "sources": {k: v for k, v in prior.sources.items() if k != name},
            "baselines": {k: v for k, v in prior.baselines.items() if k != name},
        }
    )


def _follow(
    monkeypatch,
    form,
    prior,
    cancel,
    keep,
    *,
    use_model,
    case=None,
    current=None,
    reversed_order=False,
    keep_reference="previous",
    cancel_model="ambiguous",
):
    c = CASES[form] if case is None else case
    text = keep + ". " + cancel + "." if reversed_order else cancel + ". " + keep + "."
    model = [
        _ambiguous(c["drop"][0], cancel)
        if cancel_model == "ambiguous"
        else _retain(c["drop"][0], cancel, "current"),
        _retain(c["keep"][0], keep, keep_reference),
    ]
    return _run(
        monkeypatch,
        form,
        text,
        model,
        use_model=use_model,
        prior=prior,
        current=c["current"] if current is None else current,
    )


@pytest.mark.parametrize("use_model", [False, True])
def test_exact_executed_quote12_withdraws_vat_and_keeps_amount_candidate(monkeypatch, use_model):
    prior = _seed(monkeypatch, "quote", use_model)
    fields, trace = _follow(
        monkeypatch,
        "quote",
        prior,
        "면세 선택은 취소할게요",
        "금액 후보는 그대로 사용하겠습니다",
        use_model=use_model,
    )
    assert trace["previous_rejected"] == {}
    _assert_release(fields["vat"], "면세 선택은 취소할게요.")
    _carried(fields["amount"], prior)
    _no_offer(fields["days"])
    _no_offer(fields["valid"])


@pytest.mark.parametrize("form", list(CASES))
@pytest.mark.parametrize("use_model", [False, True])
@pytest.mark.parametrize("noun", ["후보", "선택", "제안", "변경 요청"])
def test_named_field_and_alias_withdrawal_preserves_independent_previous(
    monkeypatch, form, use_model, noun
):
    c = CASES[form]
    prior = _seed(monkeypatch, form, use_model)
    label = c["drop"][4] if noun in ("선택", "제안") else c["drop"][3]
    cancel = label + " " + noun + ("는" if noun == "후보" else "은") + " 취소할게요"
    keep = c["keep"][3] + " 후보는 그대로 사용하겠습니다"
    fields, _ = _follow(
        monkeypatch, form, prior, cancel, keep, use_model=use_model, reversed_order=noun == "제안"
    )
    _assert_release(fields[c["drop"][0]], cancel)
    _carried(fields[c["keep"][0]], prior)
    for name in FORM_FIELDS[form]:
        if name not in (c["drop"][0], c["keep"][0]):
            _no_offer(fields[name])


@pytest.mark.parametrize("form", list(CASES))
@pytest.mark.parametrize("use_model", [False, True])
def test_named_withdrawal_and_independent_explicit_current(monkeypatch, form, use_model):
    c = CASES[form]
    prior = _seed(monkeypatch, form, use_model)
    cancel = c["drop"][3] + " 선택은 취소합니다"
    keep = (
        c["keep"][3]
        + ("는" if c["keep"][3].endswith("날짜") else "은")
        + " 현재 입력값 그대로 두겠습니다"
    )
    fields, _ = _follow(
        monkeypatch, form, prior, cancel, keep, use_model=use_model, keep_reference="current"
    )
    _assert_release(fields[c["drop"][0]], cancel)
    _assert_release(fields[c["keep"][0]], cancel + ". " + keep + ".")


VAT_CASES = [
    ("quote", "exempt", "면세", "included", "면세 선택"),
    ("quote", "excluded", "별도", "included", "부가세 별도 선택"),
    ("quote", "included", "포함", "excluded", "세금 포함 후보"),
    ("extra", "excluded", "별도", "included", "부가세 별도 제안"),
    ("extra", "included", "포함", "excluded", "세금 포함 선택"),
]


def _vat_case(form, value, word, current_mode):
    current = {**CASES[form]["current"], "vat": current_mode}
    evidence = "부가세는 " + word + " 조건"
    keep = CASES[form]["keep"]
    return {
        "current": current,
        "seed": evidence + "으로 제안합니다. " + keep[2] + "으로 제안합니다.",
        "drop": ("vat", value, evidence, "부가세", "세금 조건"),
        "keep": keep,
    }


@pytest.mark.parametrize("use_model", [False, True])
@pytest.mark.parametrize("form,value,word,current_mode,target", VAT_CASES)
@pytest.mark.parametrize("control", ["valid", "missing", "stale", "mismatch", "forged_evidence"])
def test_typed_vat_selection_withdrawal_requires_matching_pending_value(
    monkeypatch,
    form,
    value,
    word,
    current_mode,
    target,
    use_model,
    control,
):
    c = _vat_case(form, value, word, current_mode)
    prior = _seed(monkeypatch, form, use_model, case=c)
    current = dict(c["current"])
    if control == "missing":
        prior = _omit(prior, "vat")
    elif control == "stale":
        current["vat"] = value
    elif control == "mismatch":
        other = "별도" if value != "excluded" else "포함"
        target = "부가세 " + other + " 선택"
    elif control == "forged_evidence":
        prior = prior.model_copy(
            update={
                "fields": [
                    f.model_copy(update={"evidence": "원문에 없는 세금 근거"})
                    if f.field == "vat"
                    else f
                    for f in prior.fields
                ]
            }
        )
    cancel = target + ("는" if target.endswith("후보") else "은") + " 취소할게요"
    keep = c["keep"][3] + " 후보는 그대로 사용하겠습니다"
    fields, trace = _follow(
        monkeypatch,
        form,
        prior,
        cancel,
        keep,
        use_model=use_model,
        case=c,
        current=current,
    )
    if control == "valid":
        _assert_release(fields["vat"], cancel)
    else:
        _not_released(fields["vat"])
        _no_offer(fields["vat"])
        if control == "stale":
            assert trace["previous_rejected"]["vat"] == "stale_context"
    _carried(fields[c["keep"][0]], prior)


@pytest.mark.parametrize("use_model", [False, True])
@pytest.mark.parametrize(
    "word,value,current_mode", [("포함", "included", "excluded"), ("별도", "excluded", "included")]
)
@pytest.mark.parametrize("form", ["quote", "extra"])
def test_generic_mode_words_without_vat_context_are_not_definite_withdrawals(
    monkeypatch,
    form,
    word,
    value,
    current_mode,
    use_model,
):
    c = _vat_case(form, value, word, current_mode)
    prior = _seed(monkeypatch, form, use_model, case=c)
    cancel = word + " 선택은 취소할게요"
    keep = c["keep"][3] + " 후보는 그대로 사용하겠습니다"
    fields, _ = _follow(
        monkeypatch,
        form,
        prior,
        cancel,
        keep,
        use_model=use_model,
        case=c,
        cancel_model="current",
    )
    _not_released(fields["vat"])
    _no_offer(fields["vat"])
    # 부가세라고 확정할 근거 없는 독립 취소를 임의로 다른 칸에서 분리하지 않는다.
    _no_offer(fields[c["keep"][0]])


@pytest.mark.parametrize("form", list(CASES))
@pytest.mark.parametrize("use_model", [False, True])
@pytest.mark.parametrize("mode", ["question", "condition", "negative", "incomplete", "competing"])
def test_ambiguous_named_withdrawal_does_not_release_or_reapply_old_target(
    monkeypatch,
    form,
    use_model,
    mode,
):
    c = CASES[form]
    prior = _seed(monkeypatch, form, use_model)
    label = c["drop"][3] + " 선택은"
    cancel = {
        "question": label + " 취소할까요?",
        "condition": "가능하면 " + label + " 취소할게요",
        "negative": label + " 취소하지 마세요",
        "incomplete": label + " 취소할지 고민 중이에요",
        "competing": label + " 취소할게요. " + c["changed"][2],
    }[mode]
    keep = c["keep"][3] + " 후보는 그대로 사용하겠습니다"
    # 명백히 잘못된 현재 유지 모델 판정도 그대로 승인하면 안 된다.
    text = cancel + " " + keep + "."
    if mode not in ("question",):
        text = cancel + ". " + keep + "."
    fields, _ = _run(
        monkeypatch,
        form,
        text,
        [_retain(c["drop"][0], cancel, "current"), _retain(c["keep"][0], keep)],
        use_model=use_model,
        prior=prior,
    )
    _not_released(fields[c["drop"][0]])
    assert not (fields[c["drop"][0]].action == "set" and fields[c["drop"][0]].value == c["drop"][1])
    assert not fields[c["drop"][0]].alternatives
    # 명시적으로 다른 필드인 문의/부정/미정/경쟁은 독립 유지 후보를 지운 근거가 아니다.
    # 선행 조건의 범위가 불명확한 경우만 전체 보류를 허용한다.
    if mode != "condition":
        _carried(fields[c["keep"][0]], prior)


@pytest.mark.parametrize("form", list(CASES))
@pytest.mark.parametrize("use_model", [False, True])
@pytest.mark.parametrize("mode", ["missing", "stale", "forged", "missing_baseline"])
def test_independent_keep_requires_fresh_provenance_even_when_withdrawal_is_valid(
    monkeypatch,
    form,
    use_model,
    mode,
):
    c = CASES[form]
    prior = _seed(monkeypatch, form, use_model)
    name = c["keep"][0]
    current = dict(c["current"])
    if mode == "missing":
        # FE에서 같은 값으로 직접 재입력한 경우도 해당 후보가 제거된다.
        prior = _omit(prior, name)
    elif mode == "stale":
        current[name] = c["keep"][1]
    elif mode == "forged":
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
            update={
                "baselines": {key: value for key, value in prior.baselines.items() if key != name}
            }
        )
    cancel = c["drop"][3] + " 선택은 취소할게요"
    keep = c["keep"][3] + " 후보는 그대로 사용하겠습니다"
    fields, trace = _follow(
        monkeypatch,
        form,
        prior,
        cancel,
        keep,
        use_model=use_model,
        current=current,
    )
    _assert_release(fields[c["drop"][0]], cancel)
    _no_offer(fields[name])
    if mode in ("stale", "missing_baseline"):
        assert trace["previous_rejected"][name] == "stale_context"


@pytest.mark.parametrize("form", list(CASES))
@pytest.mark.parametrize("use_model", [False, True])
def test_withdrawn_candidate_not_restored_on_later_turn(monkeypatch, form, use_model):
    c = CASES[form]
    prior = _seed(monkeypatch, form, use_model)
    cancel = c["drop"][3] + " 후보는 취소할게요"
    keep = c["keep"][3] + " 후보는 그대로 사용하겠습니다"
    fields, _ = _follow(monkeypatch, form, prior, cancel, keep, use_model=use_model)
    _assert_release(fields[c["drop"][0]], cancel)
    _carried(fields[c["keep"][0]], prior)
    history = prior.text + "\n" + cancel + ". " + keep + "."
    next_prior = _previous(form, history, "followup", fields, c["current"])
    assert [f.field for f in next_prior.fields] == [c["keep"][0]]
    # 실제 전달 이력에서도 철회된 대상은 사라져야 하고, 조작된 이력이 와도
    # 취소 이전 근거를 다시 후보로 승인해서는 안 된다.
    old = next(f for f in prior.fields if f.field == c["drop"][0])
    forged = next_prior.model_copy(
        update={
            "fields": next_prior.fields + [old],
            "origins": {**next_prior.origins, old.field: prior.origins[old.field]},
            "sources": {**next_prior.sources, old.field: prior.sources[old.field]},
            "baselines": {**next_prior.baselines, old.field: prior.baselines[old.field]},
        }
    )
    label = c["drop"][3]
    text = (
        label + ("는" if label.endswith("사유") else "은") + " 앞서 제안한 값 그대로 사용하겠습니다"
    )
    for candidate in (next_prior, forged):
        replayed, _ = _run(
            monkeypatch,
            form,
            text + ".",
            [_retain(old.field, text)],
            use_model=use_model,
            prior=candidate,
            request_id="third",
        )
        _no_offer(replayed[old.field])


@pytest.mark.parametrize("use_model", [False, True])
@pytest.mark.parametrize("form", ["period", "dates"])
def test_member_cancellation_is_not_whole_list_withdrawal(monkeypatch, form, use_model):
    original = CASES[form]
    # dates에서는 전체 작업일 후보, period에서는 제외일 후보를 철회 대상으로 둔다.
    c = (
        original
        if form == "period"
        else {
            **original,
            "drop": original["keep"],
            "keep": original["drop"],
        }
    )
    prior = _seed(monkeypatch, form, use_model, case=c)
    label = c["drop"][3]
    keep = c["keep"][3] + " 후보는 그대로 사용하겠습니다"
    whole = label + " 후보는 취소할게요"
    fields, _ = _follow(monkeypatch, form, prior, whole, keep, use_model=use_model, case=c)
    _assert_release(fields[c["drop"][0]], whole)
    _carried(fields[c["keep"][0]], prior)
    member = label + (" 중 12월 9일" if form == "period" else " 중 12월 8일") + " 선택만 취소할게요"
    fields, _ = _follow(
        monkeypatch,
        form,
        prior,
        member,
        keep,
        use_model=use_model,
        case=c,
        cancel_model="current",
    )
    _not_released(fields[c["drop"][0]])
    if fields[c["drop"][0]].action == "set":
        # 목록 일부 취소는 원래 전체 후보의 재적용이어서는 안 된다.
        assert fields[c["drop"][0]].value != c["drop"][1]
    _carried(fields[c["keep"][0]], prior)


@pytest.mark.parametrize("use_model", [False, True])
def test_executed_quote11_independent_missing_reference_remains_held(monkeypatch, use_model):
    current = CASES["quote"]["current"]
    first = "작업 기간은 열흘로 후보 작성 바랍니다."
    fields, _ = _run(
        monkeypatch,
        "quote",
        first,
        [_set("days", 10, "작업 기간은 열흘로")],
        use_model=use_model,
        current=current,
        rules=[_field("days", "set", 10, first)],
        request_id="seed",
    )
    prior = _previous("quote", first, "seed", fields, current)
    second = "견적액은 아까 제안한 걸로 두세요."
    fields, _ = _run(
        monkeypatch,
        "quote",
        second,
        [_ambiguous("amount", second.rstrip("."))],
        use_model=use_model,
        prior=prior,
        current=current,
    )
    _no_offer(fields["amount"])
    _carried(fields["days"], prior)
    next_prior = _previous("quote", first + "\n" + second, "followup", fields, current)
    third = "총 견적금은 584만 1천 원으로 새로 정하겠습니다. 일수는 아까 정한 대로."
    fields, _ = _run(
        monkeypatch,
        "quote",
        third,
        [
            _set("amount", 5841000, "총 견적금은 584만 1천 원으로 새로 정하겠습니다"),
            _retain("days", "일수는 아까 정한 대로"),
        ],
        use_model=use_model,
        prior=next_prior,
        current=current,
        rules=[_field("amount", "set", 5841000, third)],
        request_id="third",
    )
    _assert_set(fields["amount"], 5841000)
    _carried(fields["days"], next_prior)


@pytest.mark.parametrize("use_model", [False, True])
@pytest.mark.parametrize("form", ["quote", "extra"])
@pytest.mark.parametrize("target", ["VAT 포함 후보", "세금 방식 포함 선택"])
def test_vat_compound_label_and_casefold_withdrawal(monkeypatch, form, target, use_model):
    c = _vat_case(form, "included", "포함", "excluded")
    prior = _seed(monkeypatch, form, use_model, case=c)
    cancel = target + "은 취소합니다"
    fields, _ = _follow(
        monkeypatch,
        form,
        prior,
        cancel,
        c["keep"][3] + " 후보는 그대로 사용하겠습니다",
        use_model=use_model,
        case=c,
    )
    # 공개 검증 계약은 NFKC/casefold 정규화 근거를 허용한다.
    _assert_release(fields["vat"], cancel.casefold())
    _carried(fields["amount"], prior)
