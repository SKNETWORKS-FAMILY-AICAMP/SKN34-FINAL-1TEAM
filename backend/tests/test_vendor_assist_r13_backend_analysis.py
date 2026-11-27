"""Shared request facts are immutable, provenance-bound, and actually consumed."""

import asyncio
from dataclasses import FrozenInstanceError
from datetime import date
from uuid import UUID

import pytest

from app.agent import llm, vendor_assist
from app.config import settings
from app.vendor import assist_service
from app.vendor import assist_validation as validation
from app.vendor.assist_intent_safety import date_clause_facts, date_set_is_inquiry
from app.vendor.assist_schemas import (
    AssistContext,
    AssistRequest,
    CandidateField,
    FieldIntent,
    ModelInterpretation,
    PreviousCandidate,
)

CTX = AssistContext(today=date(2026, 11, 25), base_month=date(2026, 12, 1))
CURRENT = {"amount": 4500000, "vat": "included", "days": 5, "valid": "2026-12-31"}


def request(text, previous=None):
    return AssistRequest(
        request_id="r13-analysis",
        form="quote",
        resource_id=UUID(int=179),
        text=text,
        current=CURRENT,
        previous_candidate=previous,
        rule_fields=[],
    )


def bind(req):
    analysis = validation.analyze_request(req, CTX)
    prior, rejected = validation.review_previous(req, CTX, analysis=analysis)
    return validation.bind_request_analysis(req, CTX, analysis, prior, rejected), prior, rejected


def amount():
    return FieldIntent(
        field="amount",
        action="set",
        value=6200000,
        evidence="견적 금액은 620만원으로 수정해 주세요",
        intent_kind="explicit_change",
        value_source="utterance",
    )


def test_request_analysis_cannot_be_reused_across_requests_or_contexts():
    req = request(amount().evidence)
    analysis, prior, _ = bind(req)
    with pytest.raises(FrozenInstanceError):
        analysis.current.fields[0].mentioned = False
    with pytest.raises(TypeError):
        analysis.withdrawals["amount"] = None
    with pytest.raises(ValueError, match="different request/context"):
        validation.review_fields(
            [amount()],
            "llm",
            req.model_copy(update={"text": "새 요청"}),
            CTX,
            previous=prior,
            analysis=analysis,
        )
    with pytest.raises(ValueError, match="different request/context"):
        validation.review_previous(req, CTX.model_copy(update={"crew_max": 3}), analysis=analysis)


def test_review_projection_reuses_exact_grounding_once_per_producer(monkeypatch):
    proposal = amount()
    req = request(proposal.evidence)
    analysis, prior, rejected = bind(req)
    count = 0
    original = validation._grounded

    def measured(*args, **kwargs):
        nonlocal count
        count += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(validation, "_grounded", measured)
    rp, mp = {}, {}
    rule, _ = validation.review_fields(
        [proposal], "rule", req, CTX, previous=prior, analysis=analysis, operation_proofs=rp
    )
    model, _ = validation.review_fields(
        [proposal], "llm", req, CTX, previous=prior, analysis=analysis, operation_proofs=mp
    )
    merged = validation.merge_candidates(
        req,
        CTX,
        rule,
        model,
        prior,
        previous_rejected=rejected,
        analysis=analysis,
        rule_proofs=rp,
        model_proofs=mp,
    )
    assert count == 2
    assert rp["amount"].candidate_key == rule["amount"].model_dump_json()
    assert mp["amount"].candidate_key == model["amount"].model_dump_json()
    row = next(item for item in merged if item.field == "amount")
    assert (row.action, row.status, row.value, row.origin_request_id) == (
        "set",
        "valid",
        6200000,
        req.request_id,
    )


def test_pending_transition_consumes_verified_facts_without_reparsing(monkeypatch):
    proposal = amount()
    previous = PreviousCandidate(
        request_id="previous",
        text=proposal.evidence,
        fields=[proposal],
        sources={"amount": "llm"},
        origins={"amount": "original"},
        baselines={"amount": CURRENT["amount"]},
    )
    req = request("부가세는 면세로 확정해 주세요.", previous)
    analysis, prior, rejected = bind(req)
    assert prior is not None and rejected == {}
    assert analysis.current.field("amount").scope == "other"

    def forbidden(*args, **kwargs):
        raise AssertionError("pending transition reparsed verified facts")

    for name in (
        "_targeted_retention",
        "_scoped_retention",
        "_verified_group_retention",
        "_verified_implicit_retention",
        "_field_scope",
        "_mentions_field",
        "_unscoped_retention_control",
        "resolve_withdrawals",
    ):
        monkeypatch.setattr(validation, name, forbidden)
    chosen = CandidateField(
        field="amount",
        action="keep",
        value=None,
        evidence="",
        source="llm",
        status="valid",
        alternatives=[],
        intent_kind="no_mention",
        origin_request_id=req.request_id,
    )
    result = validation._pending_transition(
        req,
        CTX,
        "amount",
        chosen,
        prior,
        rejected,
        "llm",
        withdrawals=analysis.withdrawals,
        analysis=analysis,
    )
    assert (result.value, result.intent_kind, result.source, result.origin_request_id) == (
        6200000,
        "retain_previous",
        "llm",
        "original",
    )


def test_date_value_ownership_and_inherited_risk_are_not_the_same():
    text = "종료일을 12월 20일로 정해 주세요. 아직 미정입니다."
    facts = date_clause_facts("period", text)
    assert facts[0].value_fields == {"end"}
    assert facts[-1].value_fields == set()
    assert facts[-1].risk_fields == {"end"}
    assert date_set_is_inquiry("period", "end", text)
    assert not date_set_is_inquiry("period", "start", text)


def test_service_constructs_and_binds_analysis_once(monkeypatch):
    assert not settings.llm_live
    proposal = amount()
    req = request(proposal.evidence).model_copy(update={"rule_fields": [proposal]})
    counts = {"analyze": 0, "bind": 0, "provider_stub": 0}
    analyze = assist_service.analyze_request
    binding = assist_service.bind_request_analysis

    def measured_analyze(*args, **kwargs):
        counts["analyze"] += 1
        return analyze(*args, **kwargs)

    def measured_bind(*args, **kwargs):
        counts["bind"] += 1
        return binding(*args, **kwargs)

    async def forbidden(*args, **kwargs):
        raise AssertionError("external provider forbidden")

    async def stub(*args, **kwargs):
        counts["provider_stub"] += 1
        return ModelInterpretation(fields=[proposal])

    monkeypatch.setattr(assist_service, "analyze_request", measured_analyze)
    monkeypatch.setattr(assist_service, "bind_request_analysis", measured_bind)
    monkeypatch.setattr(llm, "structured", forbidden)
    monkeypatch.setattr(vendor_assist, "interpret", stub)
    result = asyncio.run(assist_service.interpret(req, CTX))
    assert result.source == "llm"
    assert counts == {"analyze": 1, "bind": 1, "provider_stub": 1}
