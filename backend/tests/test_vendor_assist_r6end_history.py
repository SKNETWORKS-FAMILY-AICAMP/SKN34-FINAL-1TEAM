"""실행된 dates-11 및 독립 과거 선택/정정 참조 회귀. 실제 서비스, 오프라인."""

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
    CorrectionRelation,
    FieldIntent,
    ModelInterpretation,
    PreviousCandidate,
)
from app.vendor.assist_service import interpret

CASES = {
    "quote": {
        "current": {"amount": 4550000, "vat": "included", "days": 3, "valid": "2026-12-17"},
        "seed": "공사 기간은 8일로 제안합니다. 견적 금액은 482만원으로 제안합니다.",
        "first": ("days", 8, "공사 기간은 8일", "공사 기간은", "일수"),
        "initial": ("amount", 4820000, "견적 금액은 482만원"),
        "recent": ("amount", 5170000, "견적 금액은 517만원", "견적 금액은", "금액"),
        "update": "견적 금액은 517만원으로 변경합니다.",
        "changed": ("days", 10, "공사 기간은 10일로 변경합니다"),
        "wrong": ("금액", "인원"),
        "cancel": "공사 기간 변경은 취소합니다",
    },
    "period": {
        "current": {"start": "2026-12-02", "end": "2026-12-24", "excluded": []},
        "seed": "시작일은 12월 6일로 제안합니다. 종료일은 12월 22일로 제안합니다.",
        "first": ("start", "2026-12-06", "시작일은 12월 6일", "시작일은", "날짜"),
        "initial": ("end", "2026-12-22", "종료일은 12월 22일"),
        "recent": ("end", "2026-12-23", "종료일은 12월 23일", "종료일은", "날짜"),
        "update": "종료일은 12월 23일로 변경합니다.",
        "changed": ("start", "2026-12-07", "시작일은 12월 7일로 변경합니다"),
        "wrong": ("금액", "인원"),
        "cancel": "시작일 변경은 취소합니다",
    },
    "dates": {
        "current": {"days": ["2026-12-09", "2026-12-16"], "crew": 2},
        "seed": "시공 날짜는 12월 10일과 22일, 인원은 3명으로 제안합니다.",
        "first": (
            "days",
            ["2026-12-10", "2026-12-22"],
            "시공 날짜는 12월 10일과 22일",
            "공사일은",
            "날짜",
        ),
        "initial": ("crew", 3, "인원은 3명"),
        "recent": ("crew", 4, "인원은 4명", "작업자 수는", "인원"),
        "update": "인원은 4명으로 변경합니다.",
        "changed": (
            "days",
            ["2026-12-11", "2026-12-23"],
            "시공 날짜는 12월 11일과 23일로 변경합니다",
        ),
        "wrong": ("금액", "금액"),
        "cancel": "시공 날짜 변경은 취소합니다",
    },
    "extra": {
        "current": {"title": "보강", "reason": "현장 추가", "amount": 1000000, "vat": "included"},
        "seed": "추가 사유는 문짝 하단 긁힘, 비용은 28만 6천 원으로 먼저 띄워주세요.",
        "first": ("reason", "문짝 하단 긁힘", "추가 사유는 문짝 하단 긁힘", "사유는", "사유"),
        "initial": ("amount", 286000, "비용은 28만 6천 원"),
        "recent": ("amount", 360000, "추가금은 36만원", "추가금은", "금액"),
        "update": "추가금은 36만원으로 변경합니다.",
        "changed": ("reason", "바닥 파손", "사유는 바닥 파손으로 변경합니다"),
        "wrong": ("인원", "인원"),
        "cancel": "사유 변경은 취소합니다",
    },
}

RESOURCE = UUID("00000000-0000-4000-8000-000000000179")


def _field(name, action="keep", value=None, evidence="", intent=None, source=None, correction=None):
    return FieldIntent(
        field=name,
        action=action,
        value=value,
        evidence=evidence,
        intent_kind=intent,
        value_source=source,
        correction=correction,
    )


def _set(name, value, evidence, correction=None):
    return _field(name, "set", value, evidence, "explicit_change", "utterance", correction)


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
    req = AssistRequest(
        request_id=request_id,
        resource_id=RESOURCE,
        form=form,
        text=text,
        current=current,
        previous_candidate=prior,
        rule_fields=[
            next((f for f in rules if f.field == name), _field(name)) for name in FORM_FIELDS[form]
        ],
    )
    ctx = AssistContext(
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
    response = asyncio.run(interpret(req, ctx, use_model=use_model, trace=trace))
    assert len(calls) == int(use_model)
    assert req.model_dump(mode="json") == before and response.request_id == request_id
    return {f.field: f for f in response.fields}, trace


def _assert_set(field, value):
    assert (field.status, field.action, field.value) == ("valid", "set", value), field
    assert not field.alternatives


def _no_offer(field):
    assert field.action not in ("set", "clear") and not field.alternatives, field
    assert not (field.status == "valid" and field.intent_kind == "retain_current"), field


def _prior(form, text, request_id, fields):
    selected = [f for f in fields.values() if f.status == "valid" and f.action == "set"]
    return PreviousCandidate(
        request_id=request_id,
        text=text,
        fields=[
            # 실제 FE protocol.previousCandidate는 파생 날짜 목록만 정정
            # 메타데이터를 전달한다. 스칼라 정정은 새 턴에서 원문으로 재검증.
            _field(
                f.field,
                f.action,
                f.value,
                f.evidence,
                source="derived" if f.value_source == "derived" else None,
                correction=f.correction if f.value_source == "derived" else None,
            )
            for f in selected
        ],
        sources={f.field: f.source for f in selected},
        origins={f.field: f.origin_request_id for f in selected},
        baselines={f.field: CASES[form]["current"][f.field] for f in selected},
    )


def _carried(field, prior):
    old = next(f for f in prior.fields if f.field == field.field)
    _assert_set(field, old.value)
    assert field.intent_kind == "retain_previous" and field.value_source == (
        "derived" if old.value_source == "derived" else "previous"
    ), field
    assert field.origin_request_id == prior.origins[field.field]
    assert field.source == prior.sources[field.field] and field.evidence == old.evidence
    assert field.correction == old.correction


def _omit(prior, name):
    return prior.model_copy(
        update={
            "fields": [f for f in prior.fields if f.field != name],
            "origins": {k: v for k, v in prior.origins.items() if k != name},
            "sources": {k: v for k, v in prior.sources.items() if k != name},
            "baselines": {k: v for k, v in prior.baselines.items() if k != name},
        }
    )


def _history(monkeypatch, form, use_model, *, update_first=False):
    c = CASES[form]
    first, initial, recent = c["first"], c["initial"], c["recent"]
    fields, _ = _run(
        monkeypatch,
        form,
        c["seed"],
        [_set(*first[:3]), _set(*initial)],
        use_model=use_model,
        request_id="seed",
        rules=[_field(x[0], "set", x[1], x[2]) for x in (first, initial)],
    )
    _assert_set(fields[first[0]], first[1])
    _assert_set(fields[initial[0]], initial[1])
    prior = _prior(form, c["seed"], "seed", fields)
    text = c["update"]
    model = [_set(*recent[:3])]
    if update_first:
        text += " " + c["changed"][2] + "."
        model.append(_set(*c["changed"]))
    fields, _ = _run(
        monkeypatch,
        form,
        text,
        model,
        use_model=use_model,
        prior=prior,
        request_id="recent",
        rules=[_field(f.field, "set", f.value, f.evidence) for f in model],
    )
    _assert_set(fields[first[0]], c["changed"][1] if update_first else first[1])
    _assert_set(fields[recent[0]], recent[1])
    assert fields[first[0]].origin_request_id == ("recent" if update_first else "seed")
    assert fields[recent[0]].origin_request_id == "recent"
    return _prior(form, c["seed"] + "\n" + text, "recent", fields)


def _references(c, selection="앞서 고른", correction="방금 고친"):
    return (
        f"{c['first'][3]} {selection} {c['first'][4]}",
        f"{c['recent'][3]} {correction} {c['recent'][4]}",
    )


def _follow(
    monkeypatch,
    form,
    prior,
    first,
    recent,
    *,
    use_model,
    current=None,
    reverse=False,
    clip=True,
    directive=" 적용해주세요",
    model_first_ambiguous=False,
    ending=".",
):
    c = CASES[form]
    # 받침 유무에 따라 목적격 방향 조사 사용. 앞 절은 완결 술어를 공유한다.
    left, right = (first, recent) if reverse else (recent, first)
    particle = "으로" if (ord(right[-1]) - ord("가")) % 28 else "로"
    terminal = right + particle + directive
    text = left + ", " + terminal + ending
    final_evidence = (
        terminal
        if not clip
        else terminal.split(" 적용", 1)[0] + (" 적용" if " 적용" in terminal else "")
    )
    earlier_evidence = left
    first_evidence, recent_evidence = (
        (earlier_evidence, final_evidence) if reverse else (final_evidence, earlier_evidence)
    )
    model = [
        _ambiguous(c["first"][0], first_evidence)
        if model_first_ambiguous
        else _retain(c["first"][0], first_evidence),
        _retain(c["recent"][0], recent_evidence),
    ]
    return _run(monkeypatch, form, text, model, use_model=use_model, prior=prior, current=current)


def _exact_history(monkeypatch):
    # 실행된 T1/T2의 모델 및 규칙 근거를 그대로 사용한다. T3는 두 전달 상태를 비교한다.
    t1 = "투입 인원은 3명에 공사 날짜는 12월 8일과 29일로 먼저 후보 주세요."
    t2 = "조금 전에 인원을 3명이라 했는데 5명으로 정정하겠습니다."
    rid1 = "b619b5a1-ea4d-4c6f-aa1e-5d19e7b48cfe"
    rid2 = "295b2c58-ee14-476d-804b-2eaf6ed374ff"
    model = [
        _set("days", ["2026-12-08", "2026-12-29"], "공사 날짜는 12월 8일과 29일"),
        _set("crew", 3, "투입 인원은 3명"),
    ]
    fields, _ = _run(
        monkeypatch,
        "dates",
        t1,
        model,
        use_model=True,
        request_id=rid1,
        rules=[_field(f.field, "set", f.value, t1) for f in model],
    )
    _assert_set(fields["crew"], 3)
    _assert_set(fields["days"], ["2026-12-08", "2026-12-29"])
    prior = _prior("dates", t1, rid1, fields)
    correction = CorrectionRelation(
        kind="replace",
        rejected_value=3,
        rejected_evidence="3명이라 했는데",
        accepted_evidence="5명으로 정정하겠습니다",
    )
    fields, _ = _run(
        monkeypatch,
        "dates",
        t2,
        [
            _set("crew", 5, "인원을 3명이라 했는데 5명으로 정정하겠습니다", correction),
        ],
        use_model=True,
        prior=prior,
        request_id=rid2,
    )
    _assert_set(fields["crew"], 5)
    assert fields["crew"].correction == correction
    _carried(fields["days"], prior)
    return _prior("dates", t1 + "\n" + t2, rid2, fields)


@pytest.mark.parametrize("use_model", [False, True])
@pytest.mark.parametrize("missing_crew", [True, False])
def test_exact_executed_dates11_and_corrected_history(monkeypatch, use_model, missing_crew):
    prior = _exact_history(monkeypatch)
    if missing_crew:
        prior = _omit(prior, "crew")
        # 실제 전송된 T3의 날짜 후보·원문·출처·기준값을 바꾸지 않는다.
        assert [f.field for f in prior.fields] == ["days"]
        assert prior.fields[0].evidence == "공사 날짜는 12월 8일과 29일"
        assert prior.baselines == {"days": ["2026-12-09", "2026-12-16"]}
    t3 = "작업자 수는 방금 고친 후보, 공사일은 앞서 고른 날짜로 적용해주세요."
    fields, trace = _run(
        monkeypatch,
        "dates",
        t3,
        [
            _retain("days", "공사일은 앞서 고른 날짜로 적용"),
            _ambiguous("crew", "작업자 수는 방금 고친 후보"),
        ],
        use_model=use_model,
        prior=prior,
        request_id="a8e172be-05ba-453c-b634-5424cf6660ce",
    )
    assert trace["previous_rejected"] == {}
    _carried(fields["days"], prior)
    if missing_crew:
        _no_offer(fields["crew"])
    else:
        _carried(fields["crew"], prior)


@pytest.mark.parametrize("form", list(CASES))
@pytest.mark.parametrize("use_model", [False, True])
@pytest.mark.parametrize(
    "selection,correction,reverse,clip",
    [
        ("앞서 고른", "방금 고친", False, True),
        ("앞서 골랐던", "방금 고쳤던", True, False),
        ("처음 고른", "방금 수정한", False, False),
        ("처음에 골랐던", "방금 변경했던", True, True),
        ("맨 처음 고른", "방금 결정했던", False, True),
        ("최초 선택했던", "직전에 고친", True, False),
        ("조금 전에 고른", "직전에 고친", False, True),
    ],
)
def test_historical_choice_and_correction_share_complete_predicate(
    monkeypatch,
    form,
    use_model,
    selection,
    correction,
    reverse,
    clip,
):
    c = CASES[form]
    prior = _history(monkeypatch, form, use_model)
    first, recent = _references(c, selection, correction)
    fields, trace = _follow(
        monkeypatch,
        form,
        prior,
        first,
        recent,
        use_model=use_model,
        reverse=reverse,
        clip=clip,
        model_first_ambiguous=True,
    )
    _carried(fields[c["first"][0]], prior)
    _carried(fields[c["recent"][0]], prior)
    assert trace["previous_rejected"] == {}


@pytest.mark.parametrize("form", list(CASES))
@pytest.mark.parametrize("use_model", [False, True])
@pytest.mark.parametrize("side", ["first", "recent"])
@pytest.mark.parametrize(
    "control", ["missing", "stale", "forged", "missing_baseline", "wrong_type"]
)
def test_independent_history_sibling_is_not_required_for_valid_reference(
    monkeypatch,
    form,
    use_model,
    side,
    control,
):
    c = CASES[form]
    prior = _history(monkeypatch, form, use_model)
    name = c[side][0]
    other = c["recent" if side == "first" else "first"][0]
    current = dict(c["current"])
    first, recent = _references(c)
    if control == "missing":
        # FE 同值 수동 편집도 해당 필드의 이전 후보를 제거한 요청으로 표현한다.
        prior = _omit(prior, name)
    elif control == "stale":
        current[name] = c[side][1]
    elif control == "forged":
        prior = prior.model_copy(
            update={
                "fields": [
                    f.model_copy(update={"evidence": "원문에 없는 근거"}) if f.field == name else f
                    for f in prior.fields
                ]
            }
        )
    elif control == "missing_baseline":
        prior = prior.model_copy(
            update={"baselines": {k: v for k, v in prior.baselines.items() if k != name}}
        )
    elif side == "first":
        first = c["first"][3] + " 앞서 고른 " + c["wrong"][0]
    else:
        recent = c["recent"][3] + " 방금 고친 " + c["wrong"][1]
    fields, trace = _follow(
        monkeypatch,
        form,
        prior,
        first,
        recent,
        use_model=use_model,
        current=current,
        model_first_ambiguous=True,
    )
    _no_offer(fields[name])
    _carried(fields[other], prior)
    if control in ("stale", "missing_baseline"):
        assert trace["previous_rejected"][name] == "stale_context"


@pytest.mark.parametrize("form", list(CASES))
@pytest.mark.parametrize("use_model", [False, True])
@pytest.mark.parametrize("control", ["updated_first", "wrong_first_origin"])
def test_first_choice_never_means_revised_latest_value(monkeypatch, form, use_model, control):
    c = CASES[form]
    prior = _history(monkeypatch, form, use_model, update_first=control == "updated_first")
    if control == "wrong_first_origin":
        prior = prior.model_copy(update={"origins": {**prior.origins, c["first"][0]: "recent"}})
    first, recent = _references(c, "처음 골랐던")
    fields, _ = _follow(monkeypatch, form, prior, first, recent, use_model=use_model)
    _no_offer(fields[c["first"][0]])
    _carried(fields[c["recent"][0]], prior)


@pytest.mark.parametrize("form", list(CASES))
@pytest.mark.parametrize("use_model", [False, True])
@pytest.mark.parametrize(
    "suffix", ["로 적용할까요?", "로 적용하지 마세요.", "로 적용할지 고민 중입니다.", "로."]
)
def test_incomplete_or_nonaffirmative_shared_directive_cannot_restore_candidates(
    monkeypatch,
    form,
    use_model,
    suffix,
):
    c = CASES[form]
    prior = _history(monkeypatch, form, use_model)
    first, recent = _references(c)
    text = recent + ", " + first + suffix
    fields, _ = _run(
        monkeypatch,
        form,
        text,
        [_retain(c["first"][0], first), _retain(c["recent"][0], recent)],
        use_model=use_model,
        prior=prior,
    )
    _no_offer(fields[c["first"][0]])
    _no_offer(fields[c["recent"][0]])


@pytest.mark.parametrize("form", list(CASES))
@pytest.mark.parametrize("use_model", [False, True])
@pytest.mark.parametrize("control", ["condition", "competition", "cancellation", "standalone"])
def test_scoped_history_controls(monkeypatch, form, use_model, control):
    c = CASES[form]
    prior = _history(monkeypatch, form, use_model)
    first, recent = _references(c)
    text = (
        recent
        + ", "
        + first
        + ("으로" if (ord(first[-1]) - ord("가")) % 28 else "로")
        + " 적용해주세요."
    )
    if control == "condition":
        text = "가능하면 " + text
    elif control == "competition":
        text += " " + c["changed"][2] + "."
    elif control == "cancellation":
        text += " " + c["cancel"] + "."
    else:
        text = first + "."
    fields, _ = _run(
        monkeypatch,
        form,
        text,
        [_retain(c["first"][0], first), _retain(c["recent"][0], recent)],
        use_model=use_model,
        prior=prior,
    )
    f = fields[c["first"][0]]
    assert not (f.action == "set" and f.value == c["first"][1]), f
    assert not f.alternatives
    if control in ("competition", "cancellation"):
        _carried(fields[c["recent"][0]], prior)
    elif control == "condition":
        _no_offer(fields[c["recent"][0]])


@pytest.mark.parametrize("form", list(CASES))
@pytest.mark.parametrize("use_model", [False, True])
def test_canceled_old_candidate_cannot_be_reintroduced_from_history(monkeypatch, form, use_model):
    c = CASES[form]
    prior = _history(monkeypatch, form, use_model)
    name = c["first"][0]
    cancel = c["cancel"] + "."
    fields, _ = _run(
        monkeypatch,
        form,
        cancel,
        [_retain(name, c["cancel"], "current")],
        use_model=use_model,
        prior=prior,
        request_id="canceled",
    )
    f = fields[name]
    assert (f.status, f.action, f.intent_kind) == ("valid", "keep", "retain_current"), f
    _carried(fields[c["recent"][0]], prior)
    history = _prior(form, prior.text + "\n" + cancel, "canceled", fields)
    assert name not in history.origins
    old = next(f for f in prior.fields if f.field == name)
    forged = history.model_copy(
        update={
            "fields": history.fields + [old],
            "origins": {**history.origins, name: prior.origins[name]},
            "sources": {**history.sources, name: prior.sources[name]},
            "baselines": {**history.baselines, name: prior.baselines[name]},
        }
    )
    first, recent = _references(c)
    for candidate in (history, forged):
        fields, _ = _follow(monkeypatch, form, candidate, first, recent, use_model=use_model)
        _no_offer(fields[name])
        _carried(fields[c["recent"][0]], history)


@pytest.mark.parametrize("form", list(CASES))
@pytest.mark.parametrize("use_model", [False, True])
@pytest.mark.parametrize("complete", [False, True])
def test_standalone_selection_needs_its_own_complete_predicate(
    monkeypatch, form, use_model, complete
):
    c = CASES[form]
    prior = _history(monkeypatch, form, use_model)
    first, _ = _references(c, "앞서 골랐던")
    if complete:
        text = first + "로 적용해주세요."
    else:
        text = first + "."
    fields, _ = _run(
        monkeypatch,
        form,
        text,
        [_retain(c["first"][0], first)],
        use_model=use_model,
        prior=prior,
    )
    if complete:
        _carried(fields[c["first"][0]], prior)
    else:
        _no_offer(fields[c["first"][0]])


@pytest.mark.parametrize("form,referent", [("quote", "날짜"), ("dates", "일수")])
@pytest.mark.parametrize("use_model", [False, True])
def test_days_reference_type_distinguishes_duration_and_date_list(
    monkeypatch, form, referent, use_model
):
    c = CASES[form]
    prior = _history(monkeypatch, form, use_model)
    _, recent = _references(c)
    first = c["first"][3] + " 앞서 고른 " + referent
    fields, _ = _follow(monkeypatch, form, prior, first, recent, use_model=use_model)
    _no_offer(fields["days"])
    _carried(fields[c["recent"][0]], prior)


@pytest.mark.parametrize("form", ["quote", "extra"])
@pytest.mark.parametrize("use_model", [False, True])
@pytest.mark.parametrize("variant", ["verified", "missing", "wrong_type", "question"])
def test_historical_mode_setting_reference_after_independent_manual_amount(
    monkeypatch, form, use_model, variant
):
    seed = "부가세는 별도로 처리해 주세요."
    fields, _ = _run(
        monkeypatch,
        form,
        seed,
        [_set("vat", "excluded", seed.rstrip("."))],
        use_model=use_model,
        rules=[_set("vat", "excluded", seed.rstrip("."))],
        request_id="seed",
    )
    _assert_set(fields["vat"], "excluded")
    prior = _prior(form, seed, "seed", fields)
    current = {**CASES[form]["current"], "amount": 337000}
    if variant == "missing":
        prior = None
    clause = "부가세 구분은 아까 요청했던 " + ("금액" if variant == "wrong_type" else "설정")
    clause += " 그대로 가주세요" if variant != "question" else " 그대로 갈까요?"
    text = "제가 금액 입력란에서 33만 7천 원으로 이미 수정해 뒀습니다. 그 금액은 놔두고 " + clause
    fields, _ = _run(
        monkeypatch,
        form,
        text,
        [_retain("vat", clause), _retain("amount", "그 금액은 놔두고", "current")],
        use_model=use_model,
        prior=prior,
        current=current,
    )
    if variant == "verified":
        _carried(fields["vat"], prior)
    else:
        _no_offer(fields["vat"])
    assert fields["amount"].action not in ("set", "clear")
