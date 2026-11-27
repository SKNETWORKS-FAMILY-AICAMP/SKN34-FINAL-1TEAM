"""R3 실제 서비스의 필드 상태 전이와 업무 조합 검증 시험.

G100 D22/E22 T4는 당시 요청과 저장 모델 응답 그대로 재생한다.
Q21은 매 턴 실제 응답에서 다음 미적용 후보를 만들어 재생한다.
동결 결과 SHA256: 21c12177c65d430d7c65838c56761436572473421ced9f697ebbb8e0d430fd3d
동결 입력 SHA256: 303cee7d272e6958a791e23478d699735ee205a8ae5978efcc5c5999999f70bc
"""

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
from app.vendor.assist_service import FALLBACK_WARNING, interpret

_CTX = AssistContext(
    today=date(2026, 11, 24), base_month=date(2026, 12, 1), crew_max=6, days_max=120
)
_CURRENT = {
    "dates": {"days": ["2026-12-09", "2026-12-16"], "crew": 2},
    "extra": {"title": "보강", "reason": "현장 추가", "amount": 1_000_000, "vat": "included"},
    "period": {"start": "2026-12-02", "end": "2026-12-24", "excluded": []},
    "quote": {"amount": 4_550_000, "vat": "included", "days": 3, "valid": "2026-12-17"},
}
_RESOURCE = UUID("00000000-0000-4000-8000-000000000179")


def _set(field, value, evidence, *, model=False, value_source="utterance"):
    return FieldIntent(
        field=field,
        action="set",
        value=value,
        evidence=evidence,
        intent_kind="explicit_change" if model else None,
        value_source=value_source if model else None,
    )


def _keep(field, evidence="", kind=None):
    return FieldIntent(
        field=field,
        action="keep",
        value=None,
        evidence=evidence,
        intent_kind=kind,
        value_source={"retain_current": "current", "retain_previous": "previous"}.get(kind),
    )


def _uncertain(field, evidence):
    return FieldIntent(
        field=field, action="uncertain", value=None, evidence=evidence, intent_kind="ambiguous"
    )


def _request(form, text, *, prior=None, rules=(), current=None, request_id="r3-new"):
    return AssistRequest(
        request_id=request_id,
        resource_id=_RESOURCE,
        form=form,
        text=text,
        current=_CURRENT[form] if current is None else current,
        previous_candidate=prior,
        rule_fields=list(rules),
    )


def _full_fields(form, items, *, model):
    by_name = {f.field: f for f in items}
    return [
        by_name.get(field) or _keep(field, kind="no_mention" if model else None)
        for field in FORM_FIELDS[form]
    ]


def _run(monkeypatch, request, model, *, context=None, mode="model"):
    assert not settings.llm_live
    if context is None:
        context = _CTX.model_copy(update={"required_days": 2}) if request.form == "dates" else _CTX
    before = request.model_dump(mode="json")
    calls = []

    async def stub(data, model_context):
        calls.append(data.model_copy(deep=True))
        assert model_context == context
        if mode == "fallback":
            raise RuntimeError("offline fixture")
        return ModelInterpretation(fields=list(model))

    monkeypatch.setattr(vendor_assist, "interpret", stub)
    trace = {}
    response = asyncio.run(interpret(request, context, use_model=mode != "rules", trace=trace))
    assert len(calls) == int(mode != "rules")
    assert request.model_dump(mode="json") == before
    assert response.form == request.form and response.request_id == request.request_id
    assert response.source == ("llm" if mode == "model" else "rule")
    if mode == "fallback":
        assert FALLBACK_WARNING in response.warnings
    if calls:
        # 미적용 후보는 실제 폼 입력값을 대체하지 않는다.
        assert calls[0].current == before["current"]
        verified = calls[0].previous_candidate
        assert (verified.model_dump(mode="json") if verified else None) == trace[
            "previous_verified"
        ]
        if mode == "model":
            assert trace["model_raw"] == [item.model_dump(mode="json") for item in model]
    fields = {field.field: field for field in response.fields}
    assert set(fields) == set(FORM_FIELDS[request.form])
    assert not any(field.alternatives for field in fields.values())
    return fields, trace


def _offers(fields):
    return {
        name: field.value
        for name, field in fields.items()
        if field.status == "valid" and field.action in ("set", "clear")
    }


def _held(field):
    assert field.status in ("invalid", "uncertain"), field
    assert field.action == "uncertain" and field.value is None and not field.alternatives


def _current_kept(field, request_id):
    assert (field.status, field.action, field.intent_kind, field.value_source) == (
        "valid",
        "keep",
        "retain_current",
        "current",
    )
    assert field.origin_request_id == request_id


def _assert_carried(field, prior):
    original = next(f for f in prior.fields if f.field == field.field)
    assert field.status == "valid" and field.action == original.action
    assert field.value == original.value and field.evidence == original.evidence
    assert field.source == prior.sources.get(field.field, "rule")
    assert field.origin_request_id == prior.origins.get(field.field, prior.request_id)
    if original.value_source == "derived" and original.correction is not None:
        assert field.value_source == "derived" and field.correction == original.correction


def _prior(form, history, items, *, request_id="r3-prior", origins=None, sources=None):
    return PreviousCandidate(
        request_id=request_id,
        text=history,
        fields=list(items),
        baselines={item.field: _CURRENT[form][item.field] for item in items},
        sources=sources or {item.field: "llm" for item in items},
        origins=origins or {item.field: request_id for item in items},
    )


def _archived_scalar(form):
    if form == "dates":
        history = (
            "작업일 12월 7일하고 28일, 작업 인원 4명으로 제안\n"
            "작업자는 두 명으로 수정하고 날짜는 아까 정한 값으로 유지\n"
            "작업 날짜는 현재 달력에 있는 값 그대로 두겠습니다. 인원은 직전 제안대로 유지해 주세요"
        )
        prior = _prior(
            "dates",
            history,
            [_set("crew", 2, "작업자는 두 명으로 수정")],
            request_id="4b8a8cde-71da-4f75-b86d-4f2968c70427",
            origins={"crew": "454a2054-a14c-4303-a921-c88ff7f93928"},
        )
        text = "작업자는 앞서 제안한 인원 그대로 유지, 공사 날짜는 12월 12일하고 29일로 확정"
        days = ["2026-12-12", "2026-12-29"]
        req = _request(
            form,
            text,
            prior=prior,
            request_id="3b62c62d-882d-4ba7-beff-27cf4d790b18",
            rules=_full_fields(form, [_set("days", days, text)], model=False),
        )
        model = [
            _set("days", days, "공사 날짜는 12월 12일하고 29일로 확정", model=True),
            _uncertain("crew", "작업자는 앞서 제안한 인원 그대로 유지"),
        ]
        return req, model, "crew", {"days": days, "crew": 2}
    history = (
        "사유는 바닥 모서리 파손, 추가금 34만원으로 제안\n"
        "사유는 바닥 하부 균열로 정정하고 금액은 앞서 제안한 그대로\n"
        "사유는 지금 적은 값대로 두겠습니다. 추가 금액은 아까 제안한 값대로 유지해주세요"
    )
    prior = _prior(
        "extra",
        history,
        [_set("amount", 340_000, "추가금 34만원")],
        request_id="48736e68-14e9-4dc4-9c75-d0466c4d3876",
        origins={"amount": "dfafb001-5e6a-4ca3-9a43-f2c96b62f06c"},
    )
    text = "금액은 앞서 제안한 대로 유지하고 작업명은 마루 부분 보수로 확정"
    req = _request(
        form,
        text,
        prior=prior,
        request_id="8329f186-5469-47dd-84bb-db5e457c3763",
        rules=_full_fields(form, [], model=False),
    )
    model = _full_fields(
        form,
        [
            _set("title", "마루 부분 보수", "작업명은 마루 부분 보수로 확정", model=True),
            _uncertain("amount", "금액은 앞서 제안한 대로 유지"),
        ],
        model=True,
    )
    return req, model, "amount", {"title": "마루 부분 보수", "amount": 340_000}


@pytest.mark.parametrize("form", ["dates", "extra"], ids=["D22-T4", "E22-T4"])
def test_archived_scalar_history_retention(monkeypatch, form):
    req, model, scalar, expected = _archived_scalar(form)
    fields, trace = _run(monkeypatch, req, model)
    assert _offers(fields) == expected, trace["decisions"]
    _assert_carried(fields[scalar], req.previous_candidate)
    assert scalar not in trace["previous_rejected"]
    assert trace["previous_verified"]["baselines"][scalar] == req.current[scalar]


def _next_prior(request, fields):
    # 일반 스칼라의 정정 메타데이터는 브라우저의 다음 요청에서 생략된다.
    # 목록 교체의 파생 근거는 기존 계약 그대로 전달한다.
    prior = request.previous_candidate
    items = [f for f in fields.values() if f.status == "valid" and f.action in ("set", "clear")]
    baselines = {}
    for field in items:
        inherited = (
            prior
            and field.origin_request_id != request.request_id
            and (field.field in prior.baselines)
        )
        baselines[field.field] = (
            prior.baselines[field.field] if inherited else request.current[field.field]
        )
    return PreviousCandidate(
        request_id=request.request_id,
        text=(prior.text + "\n" if prior else "") + request.text,
        fields=[
            FieldIntent(
                field=f.field,
                action=f.action,
                value=f.value,
                evidence=f.evidence,
                intent_kind="explicit_change" if f.value_source == "derived" else None,
                value_source="derived" if f.value_source == "derived" else None,
                correction=f.correction if f.value_source == "derived" else None,
            )
            for f in items
        ],
        sources={f.field: f.source for f in items},
        origins={f.field: f.origin_request_id for f in items},
        baselines=baselines,
    )


def test_q21_dynamic_four_turn_carry_from_actual_service_outputs(monkeypatch):
    # G100 Q21의 모든 입력과 저장 모델 응답. 이전 후보는 과거 실패 결과가 아닌
    # 이번 실행에서 검증된 응답만 이어서 사용한다.
    ids = [
        "593f6102-f905-4386-b67d-119b62ca6040",
        "85bd0fdc-780f-46e3-9ceb-3697a01a4fb8",
        "d647b501-bd70-4aee-95e2-aba4b5585d22",
        "33790a99-9e0a-4607-ae16-93c6d77aef66",
    ]
    first = "견적 764만원, 공사 6일로 우선 제안"
    second = "금액은 783만원으로 올려주세요. 일수는 앞서 정한 대로"
    third = "부가세 별도로 정하고 나머지는 그대로 유지해 주세요"
    fourth = "유효기간만 12월 29일로 넣어주세요"
    turns = [
        (
            first,
            [_set("amount", 7_640_000, first), _set("days", 6, first)],
            [
                _set("amount", 7_640_000, "견적 764만원", model=True),
                _set("days", 6, "공사 6일", model=True),
            ],
            {"amount": 7_640_000, "days": 6},
        ),
        (
            second,
            [_set("amount", 7_830_000, second)],
            [
                _set("amount", 7_830_000, "금액은 783만원으로 올려주세요", model=True),
                _keep("days", "일수는 앞서 정한 대로", "retain_previous"),
            ],
            {"amount": 7_830_000, "days": 6},
        ),
        (
            third,
            [_set("vat", "excluded", third)],
            [
                _set("vat", "excluded", "부가세 별도로 정하고", model=True),
                *[
                    _keep(f, "나머지는 그대로 유지해 주세요", "retain_current")
                    for f in ("amount", "days", "valid")
                ],
            ],
            {"amount": 7_830_000, "days": 6, "vat": "excluded"},
        ),
        (
            fourth,
            [],
            [_set("valid", "2026-12-29", fourth, model=True)],
            {"amount": 7_830_000, "days": 6, "vat": "excluded", "valid": "2026-12-29"},
        ),
    ]
    prior = None
    for index, (utterance, rules, raw_model, expected) in enumerate(turns):
        request = _request(
            "quote",
            utterance,
            prior=prior,
            request_id=ids[index],
            rules=_full_fields("quote", rules, model=False),
        )
        fields, trace = _run(monkeypatch, request, _full_fields("quote", raw_model, model=True))
        assert _offers(fields) == expected, (index + 1, trace["decisions"])
        assert not trace["previous_rejected"]
        prior = _next_prior(request, fields)
        assert prior.baselines == {name: request.current[name] for name in expected}
    assert prior.origins == {
        "amount": ids[1],
        "days": ids[0],
        "vat": ids[2],
        "valid": ids[3],
    }
    assert prior.sources == {field: "llm" for field in prior.origins}
    assert {f.field: f.evidence for f in prior.fields} == {
        "amount": "금액은 783만원으로 올려주세요",
        "days": "공사 6일",
        "vat": "부가세 별도로 정하고",
        "valid": fourth,
    }


@pytest.mark.parametrize("form", ["dates", "extra"])
@pytest.mark.parametrize(
    "failure", ["current", "cancel", "ambiguous", "changed", "stale", "missing"]
)
def test_scalar_history_does_not_revive_unverified_or_retired_candidate(monkeypatch, form, failure):
    request, model, scalar, expected = _archived_scalar(form)
    old = request.previous_candidate
    label = "인원" if form == "dates" else "추가 금액"
    history = old.text
    current = dict(request.current)
    if failure == "current":
        history += "\n" + label + "은 현재 폼 값 그대로 유지하겠습니다"
    elif failure == "cancel":
        history += "\n" + label + " 변경 요청은 취소하겠습니다"
    elif failure == "ambiguous":
        history += "\n" + label + "은 아직 미정이에요"
    elif failure == "changed":
        history += "\n" + (
            "인원은 5명으로 확정해 주세요"
            if form == "dates"
            else "추가 금액 48만원으로 확정해 주세요"
        )
    elif failure == "stale":
        current[scalar] = 3 if form == "dates" else 900_000
    elif failure == "missing":
        history = (
            "작업 날짜는 현재 달력에 있는 값 그대로 유지"
            if form == "dates"
            else "사유는 현재 입력한 값 그대로 유지"
        )
    prior = old.model_copy(update={"text": history})
    request = request.model_copy(update={"previous_candidate": prior, "current": current})
    # 오래된 모델이 이전 값 유지라고 응답해도 복원 근거가 없으면 적용할 수 없다.
    model = [
        _keep(scalar, f.evidence, "retain_previous") if f.field == scalar else f for f in model
    ]
    fields, trace = _run(monkeypatch, request, model)
    _held(fields[scalar])
    assert _offers(fields) == {name: value for name, value in expected.items() if name != scalar}
    assert scalar in trace["previous_rejected"]
    if failure == "stale":
        assert trace["previous_rejected"][scalar] == "stale_context"


@pytest.mark.parametrize("control", ["current", "cancel", "ambiguous", "previous"])
def test_independent_scalar_transitions_do_not_change_other_prior(monkeypatch, control):
    prior = _prior(
        "extra",
        "추가 항목명은 선반 보강, 추가 금액은 34만원으로 제안해 주세요",
        [
            _set("title", "선반 보강", "추가 항목명은 선반 보강"),
            _set("amount", 340_000, "추가 금액은 34만원"),
        ],
        sources={"title": "rule", "amount": "llm"},
        origins={"title": "original-title", "amount": "original-amount"},
    )
    phrase = {
        "current": "추가 금액은 현재 폼 값 그대로 유지하겠습니다",
        "cancel": "추가 금액 변경 요청은 취소하겠습니다",
        "ambiguous": "추가 금액은 아직 미정이에요",
        "previous": "추가 금액은 앞서 제안한 대로 유지해주세요",
    }[control]
    title_ref = "항목명은 앞서 제안한 대로 유지해 주세요"
    request = _request("extra", phrase + ". " + title_ref, prior=prior)
    amount = (
        _uncertain("amount", phrase)
        if control == "ambiguous"
        else _keep(
            "amount", phrase, "retain_previous" if control == "previous" else "retain_current"
        )
    )
    fields, trace = _run(
        monkeypatch, request, [amount, _keep("title", title_ref, "retain_previous")]
    )
    _assert_carried(fields["title"], prior)
    assert not trace["previous_rejected"]
    if control == "ambiguous":
        _held(fields["amount"])
    elif control == "previous":
        _assert_carried(fields["amount"], prior)
    else:
        _current_kept(fields["amount"], request.request_id)
    expected = {"title": "선반 보강"}
    if control == "previous":
        expected["amount"] = 340_000
    assert _offers(fields) == expected


@pytest.mark.parametrize("mode", ["model", "rules", "fallback"])
def test_period_current_retention_uses_actual_not_pending_start(monkeypatch, mode):
    prior = _prior(
        "period",
        "시작일은 12월 10일로 제안해 주세요",
        [_set("start", "2026-12-10", "시작일은 12월 10일")],
    )
    keep = "시작일은 현재 폼 값 그대로 유지"
    change = "제외일은 12월 5일로 확정해 주세요"
    model = [
        _keep("start", keep, "retain_current"),
        _set("excluded", ["2026-12-05"], change, model=True),
    ]
    request = _request("period", keep + "하고 " + change, prior=prior, rules=model)
    fields, trace = _run(monkeypatch, request, model, mode=mode)
    assert trace["previous_verified"]["fields"][0]["value"] == "2026-12-10"
    assert _offers(fields) == {"excluded": ["2026-12-05"]}, trace["decisions"]
    _current_kept(fields["start"], request.request_id)
    assert fields["excluded"].origin_request_id == request.request_id


@pytest.mark.parametrize("reference", ["implicit", "explicit"])
def test_period_pending_boundary_can_support_independent_valid_exclusion(monkeypatch, reference):
    current = {"start": "2026-12-10", "end": "2026-12-24", "excluded": []}
    prior = _prior(
        "period",
        "시작일은 12월 2일로 제안해 주세요",
        [_set("start", "2026-12-02", "시작일은 12월 2일")],
    ).model_copy(update={"baselines": {"start": current["start"]}})
    change = "제외일은 12월 5일로 확정해 주세요"
    model = [_set("excluded", ["2026-12-05"], change, model=True)]
    text = change
    if reference == "explicit":
        keep = "시작일은 앞서 제안한 대로 유지"
        text = keep + "하고 " + change
        model.insert(0, _keep("start", keep, "retain_previous"))
    req = _request("period", text, prior=prior, current=current)
    fields, trace = _run(monkeypatch, req, model)
    assert _offers(fields) == {"start": "2026-12-02", "excluded": ["2026-12-05"]}, trace[
        "decisions"
    ]
    _assert_carried(fields["start"], prior)


def test_rejected_exclusion_rechecks_combination_against_real_current(monkeypatch):
    current = {"start": "2026-12-02", "end": "2026-12-24", "excluded": ["2026-12-15"]}
    text = "종료일은 12월 10일로 확정하고 제외일은 12월 10일로 변경해 주세요"
    model = [
        _set("end", "2026-12-10", "종료일은 12월 10일로 확정", model=True),
        _set("excluded", ["2026-12-10"], "제외일은 12월 10일로 변경해 주세요", model=True),
    ]
    req = _request("period", text, current=current, rules=model)
    fields, trace = _run(monkeypatch, req, model)
    assert not _offers(fields), trace["decisions"]
    _held(fields["end"])
    _held(fields["excluded"])


def test_combination_rejects_cross_producer_span_and_keeps_valid_prior(monkeypatch):
    # 각 공급자의 경계 하나는 현재 폼과 맞지만 두 경계의 최종 조합은 62일 초과.
    current = {"start": "2026-12-10", "end": "2027-01-10", "excluded": []}
    prior = _prior(
        "period",
        "제외일은 12월 20일로 제안해 주세요",
        [_set("excluded", ["2026-12-20"], "제외일은 12월 20일")],
    )
    # 시작 12/01 + 기존 종료 1/10 = 40일. 기존 시작 12/10 + 종료 2/10 = 62일.
    # 두 후보를 함께 적용하면 71일이고, 제외일은 실제 기간에서도 안전하다.
    text = "시작일 12월 1일, 종료일 2027년 2월 10일로 확정"
    rule = _set("start", "2026-12-01", "시작일 12월 1일")
    model = _set("end", "2027-02-10", "종료일 2027년 2월 10일로 확정", model=True)
    req = _request("period", text, prior=prior, current=current, rules=[rule])
    fields, trace = _run(monkeypatch, req, [model])
    assert _offers(fields) == {"excluded": ["2026-12-20"]}, trace["decisions"]
    _held(fields["start"])
    _held(fields["end"])
    _assert_carried(fields["excluded"], prior)


@pytest.mark.parametrize("mode", ["rules", "fallback"])
@pytest.mark.parametrize("stale", [False, True])
def test_server_fallback_preserves_only_verified_pending_fields(monkeypatch, mode, stale):
    request, _, scalar, expected = _archived_scalar("extra")
    current = dict(request.current)
    if stale:
        current[scalar] = 900_000
    change = "부가세는 별도로 확정해 주세요"
    rule = _set("vat", "excluded", change)
    request = _request(
        "extra", change, prior=request.previous_candidate, rules=[rule], current=current
    )
    fields, trace = _run(monkeypatch, request, [], mode=mode)
    assert fields["vat"].source == "rule"
    assert fields["vat"].origin_request_id == request.request_id
    if stale:
        assert _offers(fields) == {"vat": "excluded"}, trace["decisions"]
        _held(fields[scalar])
        assert trace["previous_rejected"][scalar] == "stale_context"
    else:
        assert _offers(fields) == {scalar: expected[scalar], "vat": "excluded"}, trace["decisions"]
        _assert_carried(fields[scalar], request.previous_candidate)
