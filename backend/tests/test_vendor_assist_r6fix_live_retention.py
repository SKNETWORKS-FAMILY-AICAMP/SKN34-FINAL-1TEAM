"""R6 호출된 quote-06의 독립 유지 및 현재값 보호 회귀."""

import asyncio
from datetime import date
from uuid import UUID

import pytest

from app.agent import vendor_assist
from app.config import settings
from app.vendor.assist_intent import field_mentions, retention_scopes
from app.vendor.assist_schemas import (
    AssistContext,
    AssistRequest,
    FieldIntent,
    ModelInterpretation,
    PreviousCandidate,
)
from app.vendor.assist_service import interpret

CTX = AssistContext(
    today=date(2026, 11, 24),
    base_month=date(2026, 12, 1),
    days_max=120,
    crew_max=6,
    vat_modes=["included", "excluded", "exempt"],
)
CURRENT = {"amount": 4550000, "vat": "included", "days": 3, "valid": "2026-12-17"}
ORIGIN = "우선 견적서 만료 날짜는 1월 6일, 공사 기간은 11일로 후보 주세요."
PROTECT = "공사 기간 손대지 않을게요"
KEEP_VALID = "견적 효력만 직전에 잡은 날짜로 반영해 주세요"


def _keep(field, evidence, reference):
    return FieldIntent(
        field=field,
        action="keep",
        value=None,
        evidence=evidence,
        intent_kind=f"retain_{reference}",
        value_source=reference,
    )


def _set(field, value, evidence):
    return FieldIntent(
        field=field,
        action="set",
        value=value,
        evidence=evidence,
        intent_kind="explicit_change",
        value_source="utterance",
    )


def _run(
    monkeypatch, text, rules, model, *, use_model, prior=None, current=None, origin="followup"
):
    assert not settings.llm_live
    req = AssistRequest(
        request_id=origin,
        form="quote",
        resource_id=UUID(int=179),
        text=text,
        current=CURRENT if current is None else current,
        previous_candidate=prior,
        rule_fields=rules,
    )
    calls = []

    async def stub(data, context):
        calls.append(data)
        return ModelInterpretation(fields=model)

    monkeypatch.setattr(vendor_assist, "interpret", stub)
    trace = {}
    response = asyncio.run(interpret(req, CTX, use_model=use_model, trace=trace))
    assert len(calls) == int(use_model)
    return {f.field: f for f in response.fields}, trace


def _seed(monkeypatch, use_model):
    days = _set("days", 11, "공사 기간은 11일")
    valid = _set("valid", "2027-01-06", "견적서 만료 날짜는 1월 6일")
    candidates = [days, valid]
    fields, _ = _run(
        monkeypatch, ORIGIN, candidates, candidates, use_model=use_model, origin="seed"
    )
    assert all(
        fields[c.field].status == "valid" and fields[c.field].value == c.value for c in candidates
    )
    return PreviousCandidate(
        request_id="seed",
        text=ORIGIN,
        fields=[
            FieldIntent(field=f.field, action=f.action, value=f.value, evidence=f.evidence)
            for f in (fields["days"], fields["valid"])
        ],
        sources={c.field: fields[c.field].source for c in candidates},
        origins={c.field: fields[c.field].origin_request_id for c in candidates},
        baselines={c.field: CURRENT[c.field] for c in candidates},
    )


@pytest.mark.parametrize("use_model", [False, True])
@pytest.mark.parametrize("control", ["valid", "missing", "stale"])
def test_live_quote06_separates_current_protection_and_previous_validity(
    monkeypatch, use_model, control
):
    prior = _seed(monkeypatch, use_model)
    current = dict(CURRENT)
    if control == "missing":
        prior = None
    elif control == "stale":
        current["valid"] = "2026-12-19"
    text = PROTECT + ". " + KEEP_VALID + "."
    model = [_keep("days", PROTECT, "current"), _keep("valid", KEEP_VALID, "previous")]
    fields, trace = _run(
        monkeypatch, text, [], model, use_model=use_model, prior=prior, current=current
    )
    days, valid = fields["days"], fields["valid"]
    assert days.action != "set" and not days.alternatives
    if prior is not None or use_model:
        assert days.status == "valid" and days.intent_kind == "retain_current"
        assert days.value_source == "current"
        assert days.origin_request_id == "followup"
    if control == "valid":
        assert valid.status == "valid" and valid.action == "set"
        assert valid.value == "2027-01-06" and valid.intent_kind == "retain_previous"
        assert valid.origin_request_id == prior.origins["valid"]
        assert valid.source == prior.sources["valid"]
        assert valid.evidence == prior.fields[1].evidence
        assert trace["previous_rejected"] == {}
    else:
        assert valid.action != "set" and not valid.alternatives
        if control == "stale":
            assert trace["previous_rejected"]["valid"] == "stale_context"


@pytest.mark.parametrize("use_model", [False, True])
@pytest.mark.parametrize(
    "suffix",
    [
        "해도 될까요?",
        "가능하면 반영해 주세요",
        "반영하지 말아 주세요",
        "반영해 주세요. 유효기간 변경은 취소합니다.",
        "반영해 주세요. 유효기간은 1월 7일로 변경합니다.",
    ],
)
def test_previous_reference_does_not_override_uncertainty_or_competing_request(
    monkeypatch, use_model, suffix
):
    prior = _seed(monkeypatch, use_model)
    evidence = "견적 효력만 직전에 잡은 날짜로 " + suffix
    text = PROTECT + ". " + evidence
    fields, _ = _run(
        monkeypatch,
        text,
        [],
        [_keep("valid", evidence, "previous")],
        use_model=use_model,
        prior=prior,
    )
    valid = fields["valid"]
    assert not (valid.status == "valid" and valid.action == "set" and valid.value == "2027-01-06")
    assert not valid.alternatives


@pytest.mark.parametrize(
    "text",
    [
        "공사 기간 손대지 않을게요?",
        "가능하면 공사 기간 손대지 않을게요.",
        "공사 기간 손대지 않을게요. 공사 기간을 7일로 변경합니다.",
    ],
)
def test_protection_does_not_certify_ambiguous_current_keep(monkeypatch, text):
    prior = _seed(monkeypatch, True)
    fields, _ = _run(
        monkeypatch,
        text,
        [],
        [_keep("days", PROTECT, "current")],
        use_model=True,
        prior=prior,
    )
    days = fields["days"]
    assert not (days.status == "valid" and days.intent_kind == "retain_current")
    assert not (days.action == "set" and days.value == 11)


@pytest.mark.parametrize(
    "form,field,label,verb",
    [
        ("quote", "days", "공사 기간", "손대지 않을게요"),
        ("period", "end", "마지막 실측 가능일", "건드리지 않겠습니다"),
        ("dates", "crew", "투입 인원", "손대지 않겠습니다"),
        ("extra", "reason", "추가 사유", "건드리지 않을게요"),
    ],
)
def test_protection_is_current_and_field_specific(form, field, label, verb):
    scopes = retention_scopes(form, f"{label} {verb}.")
    assert len(scopes) == 1
    assert scopes[0].fields == {field}
    assert scopes[0].reference == "current" and scopes[0].compatible
    # 이전 후보를 안 건드리겠다는 말은 현재 폼 유지로 바꾸지 않는다.
    scopes = retention_scopes(form, f"{label}은 직전에 잡은 값 손대지 않을게요.")
    assert not any(s.compatible for s in scopes)


def test_last_available_measurement_day_is_not_a_whole_period():
    label = "마지막 실측 가능일"
    mentions = field_mentions("period", label + "은 12월 29일로 확정합니다.")
    assert len(mentions) == 1 and mentions[0].fields == {"end"}
    assert label[mentions[0].start : mentions[0].end] == label
