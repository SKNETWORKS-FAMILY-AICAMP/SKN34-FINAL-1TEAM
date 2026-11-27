"""R5 독립 목록 재확정 이력 검증: 실제 서비스의 다음 후보만 계승한다."""

import asyncio
from datetime import date
from uuid import UUID

import pytest

from app.agent import vendor_assist
from app.config import settings
from app.vendor.assist_schemas import (
    AssistContext,
    AssistRequest,
    CorrectionRelation,
    FieldIntent,
    ModelInterpretation,
    PreviousCandidate,
)
from app.vendor.assist_service import interpret

_CASES = {
    "dates": {
        "field": "days",
        "current": {"days": ["2026-12-09", "2026-12-16"], "crew": 2},
        "seed": "작업일은 12월 4일과 18일로 확정합니다",
        "seed_value": ["2026-12-04", "2026-12-18"],
        "edit": "앞서 제안한 작업일 중 18일을 20일로 바꿔주세요",
        "edit_value": ["2026-12-04", "2026-12-20"],
        "reset": "공사일은 12월 8일 및 26일로 목록 전체를 새로 지정합니다",
        "reset_value": ["2026-12-08", "2026-12-26"],
        "carry": "작업일은 이전 제안 그대로 유지해주세요",
        "sibling": ("crew", 4, "작업 인원은 4명으로 확정합니다"),
        "change": "공사일은 12월 10일과 27일로 다시 확정합니다",
        "cancel": "공사 날짜 변경 요청은 취소합니다",
        "inquiry": "공사일은 12월 10일과 27일로 할까요?",
        "stale": ["2026-12-11", "2026-12-16"],
        "forged": ["2026-12-10", "2026-12-26"],
    },
    "period": {
        "field": "excluded",
        "current": {"start": "2026-12-02", "end": "2026-12-30", "excluded": []},
        "seed": "방문 제외일은 12월 4일, 18일로 확정합니다",
        "seed_value": ["2026-12-04", "2026-12-18"],
        "edit": "앞서 정한 제외일 중 18일을 20일로 바꿔주세요",
        "edit_value": ["2026-12-04", "2026-12-20"],
        "reset": "제외일은 12월 8일, 26일로 새로 확정합니다",
        "reset_value": ["2026-12-08", "2026-12-26"],
        "carry": "제외일은 이전 제안 그대로 유지해주세요",
        "sibling": ("start", "2026-12-03", "시작일을 12월 3일로 확정합니다"),
        "change": "제외일은 12월 10일, 27일로 다시 확정합니다",
        "cancel": "제외일 변경 요청은 취소합니다",
        "inquiry": "제외일은 12월 10일, 27일로 할까요?",
        "stale": ["2026-12-06"],
        "forged": ["2026-12-10", "2026-12-26"],
    },
}


def _set(field, value, text):
    return FieldIntent(
        field=field,
        action="set",
        value=value,
        evidence=text,
        intent_kind="explicit_change",
        value_source="utterance",
    )


def _ambiguous(field, text):
    return FieldIntent(
        field=field, action="uncertain", value=None, evidence=text, intent_kind="ambiguous"
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


def _run(monkeypatch, form, text, model, request_id, *, prior=None, current=None):
    assert not settings.llm_live
    ctx = AssistContext(
        today=date(2026, 11, 24),
        base_month=date(2026, 12, 1),
        days_max=120,
        crew_max=6,
        required_days=2 if form == "dates" else None,
        vat_modes=["included", "excluded"],
    )
    request = AssistRequest(
        request_id=request_id,
        form=form,
        resource_id=UUID(int=179),
        text=text,
        current=_CASES[form]["current"] if current is None else current,
        previous_candidate=prior,
        rule_fields=[],
    )
    calls = []

    async def stub(data, context):
        calls.append(data)
        return ModelInterpretation(fields=list(model))

    monkeypatch.setattr(vendor_assist, "interpret", stub)
    trace = {}
    response = asyncio.run(interpret(request, ctx, trace=trace))
    assert len(calls) == 1 and response.source == "llm"
    return request, {f.field: f for f in response.fields}, trace


def _next_prior(request, fields):
    previous = request.previous_candidate
    items = [f for f in fields.values() if f.status == "valid" and f.action in ("set", "clear")]
    return PreviousCandidate(
        request_id=request.request_id,
        text=(previous.text + "\n" if previous else "") + request.text,
        fields=[
            FieldIntent(
                field=f.field,
                action=f.action,
                value=f.value,
                evidence=f.evidence,
                value_source="derived" if f.value_source == "derived" else None,
                correction=f.correction if f.value_source == "derived" else None,
            )
            for f in items
        ],
        sources={f.field: f.source for f in items},
        origins={f.field: f.origin_request_id for f in items},
        baselines={
            f.field: previous.baselines[f.field]
            if previous
            and f.origin_request_id != request.request_id
            and f.field in previous.baselines
            else request.current[f.field]
            for f in items
        },
    )


def _assert_value(candidate, value):
    assert candidate.status == "valid" and candidate.action == "set"
    assert candidate.value == value and not candidate.alternatives


def _assert_blocked(candidate):
    assert candidate.status in ("invalid", "uncertain") and candidate.action == "uncertain"
    assert candidate.value is None and not candidate.alternatives


def _three_turn_reset(monkeypatch, form):
    case = _CASES[form]
    field = case["field"]
    req, fields, _ = _run(
        monkeypatch,
        form,
        case["seed"],
        [_set(field, case["seed_value"], case["seed"])],
        "r5-history-seed",
    )
    _assert_value(fields[field], case["seed_value"])
    prior = _next_prior(req, fields)
    req, fields, _ = _run(
        monkeypatch,
        form,
        case["edit"],
        [_ambiguous(field, case["edit"])],
        "r5-history-edit",
        prior=prior,
    )
    _assert_value(fields[field], case["edit_value"])
    assert fields[field].value_source == "derived"
    assert fields[field].correction.rejected_value == case["seed_value"]
    prior = _next_prior(req, fields)
    req, fields, _ = _run(
        monkeypatch,
        form,
        case["reset"],
        [_set(field, case["reset_value"], case["reset"])],
        "r5-history-reset",
        prior=prior,
    )
    _assert_value(fields[field], case["reset_value"])
    assert fields[field].value_source == "utterance" and fields[field].correction is None
    prior = _next_prior(req, fields)
    assert len(prior.text.splitlines()) == 3
    return prior


@pytest.mark.parametrize("form", list(_CASES))
@pytest.mark.parametrize("mode", ["explicit", "sibling"])
def test_independent_latest_full_list_reverified_and_retained(monkeypatch, form, mode):
    case = _CASES[form]
    field = case["field"]
    prior = _three_turn_reset(monkeypatch, form)
    if mode == "explicit":
        text = case["carry"]
        model = [_keep(field, text, retain=True)]
    else:
        sibling, value, text = case["sibling"]
        model = [_keep(field), _set(sibling, value, text)]
    _, fields, trace = _run(monkeypatch, form, text, model, "r5-history-next", prior=prior)
    candidate = fields[field]
    _assert_value(candidate, case["reset_value"])
    assert trace["previous_rejected"] == {}
    assert candidate.evidence == case["reset"]
    assert candidate.intent_kind == "retain_previous" and candidate.value_source == "previous"
    assert candidate.source == "llm" and candidate.origin_request_id == "r5-history-reset"
    assert candidate.correction is None
    if mode == "sibling":
        _assert_value(fields[sibling], value)


@pytest.mark.parametrize("form", list(_CASES))
@pytest.mark.parametrize("control", ["change", "cancel", "inquiry", "stale", "forged_derived"])
def test_full_list_history_reverify_cannot_resurrect_invalid_candidate(monkeypatch, form, control):
    case = _CASES[form]
    field = case["field"]
    prior = _three_turn_reset(monkeypatch, form)
    current = dict(case["current"])
    if control in ("change", "cancel", "inquiry"):
        # 실제 이력 뒤에 경쟁 지시가 생겼는데 클라이언트가 예전 후보를 전달한 경우.
        prior = prior.model_copy(update={"text": prior.text + "\n" + case[control]})
    elif control == "stale":
        current[field] = case["stale"]
    else:
        # 새 전체 목록 뒤에 존재하지 않는 파생 교체를 주장한다.
        forged = prior.fields[0].model_copy(
            update={
                "value": case["forged"],
                "value_source": "derived",
                "correction": CorrectionRelation(
                    kind="replace",
                    rejected_value=case["reset_value"],
                    rejected_evidence="8일",
                    accepted_evidence="10일",
                ),
            },
        )
        prior = prior.model_copy(update={"fields": [forged]})
    text = case["carry"]
    _, fields, trace = _run(
        monkeypatch,
        form,
        text,
        [_keep(field, text, retain=True)],
        "r5-history-negative",
        prior=prior,
        current=current,
    )
    _assert_blocked(fields[field])
    assert field in trace["previous_rejected"]
    if control == "stale":
        assert trace["previous_rejected"][field] == "stale_context"


@pytest.mark.parametrize("verb", ["바꾼", "고친", "옮긴"])
def test_colloquial_completed_edit_reference_preserves_derived_proof(monkeypatch, verb):
    case = _CASES["period"]
    req, fields, _ = _run(
        monkeypatch,
        "period",
        case["seed"],
        [_set("excluded", case["seed_value"], case["seed"])],
        "colloquial-seed",
    )
    req, fields, _ = _run(
        monkeypatch,
        "period",
        case["edit"],
        [_ambiguous("excluded", case["edit"])],
        "colloquial-edit",
        prior=_next_prior(req, fields),
    )
    _assert_value(fields["excluded"], case["edit_value"])
    old = fields["excluded"]
    text = f"제외 날짜는 아까 {verb} 목록 유지"
    _, fields, trace = _run(
        monkeypatch,
        "period",
        text,
        [_keep("excluded", text, retain=True)],
        "colloquial-carry",
        prior=_next_prior(req, fields),
    )
    new = fields["excluded"]
    _assert_value(new, case["edit_value"])
    assert trace["previous_rejected"] == {}
    assert new.value_source == "derived" and new.correction == old.correction
    assert new.evidence == old.evidence and new.origin_request_id == old.origin_request_id


@pytest.mark.parametrize("suffix", ["유지하지 마세요", "유지할까요?", "가능하면 유지해주세요"])
def test_colloquial_completed_edit_reference_does_not_override_uncertainty(monkeypatch, suffix):
    prior = _three_turn_reset(monkeypatch, "period")
    text = f"제외 날짜는 아까 바꾼 목록 {suffix}"
    _, fields, _ = _run(
        monkeypatch,
        "period",
        text,
        [_keep("excluded", text, retain=True)],
        "colloquial-control",
        prior=prior,
    )
    assert not (fields["excluded"].status == "valid" and fields["excluded"].action == "set")
    assert not fields["excluded"].alternatives
