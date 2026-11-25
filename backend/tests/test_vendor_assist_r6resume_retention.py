"""Independent regression for executed R6 period-06 and typed reference families."""

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

CASES = {
    "period": {
        "current": {"start": "2026-12-02", "end": "2026-12-24", "excluded": []},
        "seed": "실측은 12월 5일부터 22일까지 접수하는 걸로 우선 제시합니다.",
        "drop": ("start", "2026-12-05", "12월 5일부터", "시작일"),
        "keep": ("end", "2026-12-22", "12월 5일부터 22일까지", "종료일", "날"),
        "sibling": ("excluded", ["2026-12-13"], "제외일은 12월 13일로 확정합니다."),
    },
    "quote": {
        "current": {"amount": 4550000, "vat": "included", "days": 3, "valid": "2026-12-17"},
        "seed": "공사 기간은 6일로 제안합니다. 견적 금액은 526만원으로 제안합니다.",
        "drop": ("days", 6, "공사 기간은 6일", "공사 기간"),
        "keep": ("amount", 5260000, "견적 금액은 526만원", "견적 금액", "금액"),
        "sibling": ("vat", "excluded", "부가세는 별도로 확정합니다."),
    },
    "dates": {
        "current": {"days": ["2026-12-09", "2026-12-16"], "crew": 2},
        "seed": "작업일은 12월 8일과 21일, 인원은 3명으로 제안합니다.",
        "drop": ("crew", 3, "인원은 3명", "인원"),
        "keep": (
            "days",
            ["2026-12-08", "2026-12-21"],
            "작업일은 12월 8일과 21일",
            "작업일",
            "날짜",
        ),
        "sibling": ("crew", 4, "인원은 4명으로 확정합니다."),
    },
    "extra": {
        "current": {"title": "보강", "reason": "현장 추가", "amount": 1000000, "vat": "included"},
        "seed": "추가 작업명은 문틀 보수로, 사유는 고정 불량으로 제안합니다.",
        "drop": ("title", "문틀 보수", "추가 작업명은 문틀 보수", "추가 작업명"),
        "keep": ("reason", "고정 불량", "사유는 고정 불량", "사유", "이유"),
        "sibling": ("vat", "excluded", "부가세는 별도로 확정합니다."),
    },
}


def _field(name, action="keep", value=None, evidence="", *, intent=None, source=None):
    return FieldIntent(
        field=name,
        action=action,
        value=value,
        evidence=evidence,
        intent_kind=intent,
        value_source=source,
    )


def _set(name, value, evidence, *, source="utterance"):
    return _field(name, "set", value, evidence, intent="explicit_change", source=source)


def _retain(name, evidence, reference="previous"):
    return _field(name, evidence=evidence, intent="retain_" + reference, source=reference)


def _run(
    monkeypatch,
    form,
    text,
    rules,
    model,
    *,
    use_model,
    prior=None,
    current=None,
    request_id="followup",
):
    assert not settings.llm_live
    base = CASES[form]["current"] if current is None else current
    req = AssistRequest(
        request_id=request_id,
        resource_id=UUID(int=179),
        form=form,
        text=text,
        current=base,
        previous_candidate=prior,
        rule_fields=rules,
    )
    context = AssistContext(
        today=date(2026, 11, 24),
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
        return ModelInterpretation(fields=model)

    monkeypatch.setattr(vendor_assist, "interpret", stub)
    trace = {}
    response = asyncio.run(interpret(req, context, use_model=use_model, trace=trace))
    assert len(calls) == int(use_model)
    assert req.current == base
    assert response.request_id == request_id
    return req, {f.field: f for f in response.fields}, trace


def _assert_set(field, value):
    assert (field.status, field.action, field.value) == ("valid", "set", value), field
    assert not field.alternatives


def _assert_not_offered(field):
    assert field.action not in ("set", "clear") and not field.alternatives, field


def _assert_release(field, request_id):
    assert (field.status, field.action, field.intent_kind, field.value_source) == (
        "valid",
        "keep",
        "retain_current",
        "current",
    ), field
    assert field.value is None and not field.alternatives
    assert field.origin_request_id == request_id


def _prior(req, fields, earlier=None):
    selected = [f for f in fields.values() if f.status == "valid" and f.action == "set"]
    return PreviousCandidate(
        request_id=req.request_id,
        text=(earlier.text + "\n" if earlier else "") + req.text,
        fields=[
            FieldIntent(field=f.field, action=f.action, value=f.value, evidence=f.evidence)
            for f in selected
        ],
        origins={f.field: f.origin_request_id for f in selected},
        sources={f.field: f.source for f in selected},
        baselines={f.field: req.current[f.field] for f in selected},
    )


def _assert_carried(candidate, prior):
    old = next(f for f in prior.fields if f.field == candidate.field)
    _assert_set(candidate, old.value)
    assert candidate.intent_kind == "retain_previous"
    assert candidate.value_source == "previous"
    assert candidate.evidence == old.evidence
    assert candidate.source == prior.sources[old.field]
    assert candidate.origin_request_id == prior.origins[old.field]


def _seed(monkeypatch, form, use_model):
    c = CASES[form]
    candidates = [_set(*c["drop"][:3]), _set(*c["keep"][:3])]
    req, fields, _ = _run(
        monkeypatch,
        form,
        c["seed"],
        candidates,
        candidates,
        use_model=use_model,
        request_id="seed",
    )
    for f in candidates:
        _assert_set(fields[f.field], f.value)
    return _prior(req, fields)


@pytest.mark.parametrize("use_model", [False, True])
def test_exact_period06_executed_response_keeps_end_and_releases_start(monkeypatch, use_model):
    # Only the executed T1/T2 request/model replies; not the uncalled corpus.
    first_text = CASES["period"]["seed"]
    first_id = "c53afc8b-e480-4eec-ae96-20cb0b63dfde"
    second_id = "b330fcb4-d662-4016-8654-810c9a2063a8"
    rules = [
        _field("start", "set", "2026-12-05", first_text),
        _field("end", "set", "2026-12-22", first_text),
        _field("excluded"),
    ]
    model = [
        _set("start", "2026-12-05", "12월 5일부터"),
        _set("end", "2026-12-22", "12월 5일부터 22일까지", source="derived"),
        _field("excluded", intent="no_mention"),
    ]
    req, fields, _ = _run(
        monkeypatch,
        "period",
        first_text,
        rules,
        model,
        use_model=use_model,
        request_id=first_id,
    )
    _assert_set(fields["start"], "2026-12-05")
    _assert_set(fields["end"], "2026-12-22")
    prior = _prior(req, fields)
    if use_model:
        assert {f.field: f.evidence for f in prior.fields} == {
            "start": "12월 5일부터",
            "end": "12월 5일부터 22일까지",
        }
        assert prior.sources == {"start": "llm", "end": "llm"}
    cancel = "시작일 수정은 없던 일로 해주세요"
    retain = "종료일은 방금 제시한 날을 쓰겠습니다"
    text = cancel + ". " + retain + "."
    req, fields, trace = _run(
        monkeypatch,
        "period",
        text,
        [_field(f) for f in ("start", "end", "excluded")],
        [
            _retain("start", cancel, "current"),
            _retain("end", retain),
            _field("excluded", intent="no_mention"),
        ],
        use_model=use_model,
        prior=prior,
        request_id=second_id,
    )
    _assert_release(fields["start"], second_id)
    _assert_carried(fields["end"], prior)
    assert trace["previous_rejected"] == {}
    assert trace["previous_verified"]["origins"]["end"] == first_id
    next_prior = _prior(req, fields, prior)
    assert [f.field for f in next_prior.fields] == ["end"]
    _, fields, trace = _run(
        monkeypatch,
        "period",
        "제외일은 12월 13일로 확정합니다.",
        [_set("excluded", ["2026-12-13"], "제외일은 12월 13일로 확정합니다.")],
        [_set("excluded", ["2026-12-13"], "제외일은 12월 13일로 확정합니다.")],
        use_model=use_model,
        prior=next_prior,
        request_id="third",
    )
    _assert_not_offered(fields["start"])
    _assert_carried(fields["end"], next_prior)
    _assert_set(fields["excluded"], ["2026-12-13"])


def _object_particle(noun):
    return "을" if (ord(noun[-1]) - 0xAC00) % 28 else "를"


@pytest.mark.parametrize("form", list(CASES))
@pytest.mark.parametrize("use_model", [False, True])
@pytest.mark.parametrize(
    "withdrawal", ["수정은 없던 일로 해주세요", "변경 요청은 없던 걸로 하겠습니다"]
)
def test_typed_object_reference_and_independent_withdrawal(
    monkeypatch, form, use_model, withdrawal
):
    c = CASES[form]
    prior = _seed(monkeypatch, form, use_model)
    drop, _, _, label = c["drop"]
    keep, _, _, other_label, noun = c["keep"]
    cancel = f"{label} {withdrawal}"
    retain = f"{other_label}은 앞서 제시한 {noun}{_object_particle(noun)} 쓰겠습니다"
    req, fields, trace = _run(
        monkeypatch,
        form,
        cancel + ". " + retain + ".",
        [],
        [_retain(drop, cancel, "current"), _retain(keep, retain)],
        use_model=use_model,
        prior=prior,
    )
    _assert_release(fields[drop], req.request_id)
    _assert_carried(fields[keep], prior)
    assert trace["previous_rejected"] == {}


@pytest.mark.parametrize("form", list(CASES))
@pytest.mark.parametrize("use_model", [False, True])
@pytest.mark.parametrize("control", ["missing", "stale", "wrong_type"])
def test_typed_reference_requires_eligible_same_field_prior(monkeypatch, form, use_model, control):
    c = CASES[form]
    prior = _seed(monkeypatch, form, use_model)
    keep, value, _, label, noun = c["keep"]
    current = dict(c["current"])
    if control == "missing":
        prior = None
    elif control == "stale":
        current[keep] = value
    else:
        noun = {"period": "금액", "quote": "날짜", "dates": "금액", "extra": "인원"}[form]
    retain = f"{label}은 방금 제시한 {noun}{_object_particle(noun)} 쓰겠습니다"
    _, fields, trace = _run(
        monkeypatch,
        form,
        retain + ".",
        [],
        [_retain(keep, retain)],
        use_model=use_model,
        prior=prior,
        current=current,
    )
    _assert_not_offered(fields[keep])
    if control == "stale":
        assert trace["previous_rejected"][keep] == "stale_context"


@pytest.mark.parametrize("use_model", [False, True])
@pytest.mark.parametrize(
    "retain",
    [
        "종료일은 방금 제시한 날을 써도 될까요?",
        "가능하면 종료일은 방금 제시한 날을 쓰겠습니다.",
        "종료일은 방금 제시한 날을 쓰지 않겠습니다.",
        "종료일은 방금 제시한 날을 쓰겠습니다. 종료일 변경은 없던 일로 해주세요.",
        "종료일은 방금 제시한 날을 쓰겠습니다. 종료일을 12월 23일로 다시 바꿉니다.",
    ],
)
def test_same_field_reference_does_not_override_uncertainty_or_competing_date(
    monkeypatch, use_model, retain
):
    prior = _seed(monkeypatch, "period", use_model)
    cancel = "시작일 수정은 없던 일로 해주세요."
    _, fields, _ = _run(
        monkeypatch,
        "period",
        cancel + " " + retain,
        [],
        [_retain("start", cancel, "current"), _retain("end", retain)],
        use_model=use_model,
        prior=prior,
    )
    assert not (fields["end"].action == "set" and fields["end"].value == "2026-12-22")
    assert not fields["end"].alternatives


@pytest.mark.parametrize("use_model", [False, True])
@pytest.mark.parametrize(
    "cancel",
    [
        "시작일 수정은 없던 일로 해도 될까요?",
        "가능하면 시작일 수정은 없던 일로 해주세요.",
        "시작일 수정은 없던 일로 하지 마세요.",
        "시작일 수정은 없던 일로 해주세요. 시작일은 12월 9일로 바꾸겠습니다.",
    ],
)
def test_uncertain_withdrawal_neither_releases_nor_reapplies_old_start(
    monkeypatch, use_model, cancel
):
    prior = _seed(monkeypatch, "period", use_model)
    retain = "종료일은 방금 제시한 날을 쓰겠습니다."
    _, fields, _ = _run(
        monkeypatch,
        "period",
        cancel + " " + retain,
        [],
        [_retain("start", cancel, "current"), _retain("end", retain)],
        use_model=use_model,
        prior=prior,
    )
    start = fields["start"]
    assert not (start.status == "valid" and start.intent_kind == "retain_current")
    assert not (start.action == "set" and start.value == "2026-12-05")
    _assert_carried(fields["end"], prior)
