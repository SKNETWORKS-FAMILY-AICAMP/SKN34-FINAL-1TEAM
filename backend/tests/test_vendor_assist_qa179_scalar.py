"""QA #179: scalar intent must be grounded independently of model/rule source."""

import asyncio
from datetime import date
from uuid import UUID

import pytest

from app.agent import vendor_assist
from app.vendor.assist_schemas import (
    AssistContext,
    AssistRequest,
    CorrectionRelation,
    FieldIntent,
    ModelInterpretation,
    PreviousCandidate,
)
from app.vendor.assist_service import interpret
from app.vendor.assist_validation import review_fields, verified_previous

CTX = AssistContext(today=date(2026, 11, 24), crew_max=6)
RESOURCE = UUID(int=179)


def _request(form, text, *, prior=None, current=None, rules=None):
    return AssistRequest(
        request_id="qa-scalar",
        resource_id=RESOURCE,
        form=form,
        text=text,
        current=current or {},
        previous_candidate=prior,
        rule_fields=rules or [],
    )


def _field(name, value, evidence, *, relation=None):
    return FieldIntent(
        field=name, action="set", value=value, evidence=evidence, correction=relation
    )


def _assert_held(candidate):
    assert candidate.status in ("invalid", "uncertain")
    assert candidate.action == "uncertain"
    assert candidate.value is None


@pytest.mark.parametrize("source", ["rule", "llm"])
@pytest.mark.parametrize(
    ("form", "text", "field", "value", "evidence"),
    [
        ("quote", "견적은 500만원으로 넣지 마세요", "amount", 5_000_000, "500만원"),
        ("quote", "견적 500만원으로 확정할게요. 잠깐, 취소할게요.", "amount", 5_000_000, "500만원"),
        ("quote", "견적 300만~500만 원", "amount", 5_000_000, "500만 원"),
        ("quote", "견적 300만원 이상", "amount", 3_000_000, "300만원"),
        (
            "quote",
            "견적 300만 원 또는 400만 원 중 아직 고민 중입니다",
            "amount",
            4_000_000,
            "400만 원",
        ),
        ("quote", "견적 600만원은 미정", "amount", 6_000_000, "600만원"),
        ("extra", "금액 21만 원 이하로 생각 중", "amount", 210_000, "21만 원"),
        ("extra", "추가금 18만 원으로 해도 될까요?", "amount", 180_000, "18만 원"),
        ("quote", "부가세가 포함된다면, 견적 600만원", "amount", 6_000_000, "600만원"),
        ("quote", "견적 - 500만원", "amount", 5_000_000, "500만원"),
        ("dates", "인원은 3명으로 할까요?", "crew", 3, "3명"),
        ("dates", "작업 인원은 3명으로 바꾸지 마세요", "crew", 3, "3명"),
        ("dates", "작업 인원은 3명 정도로 생각하고 있어요", "crew", 3, "3명"),
        ("dates", "작업 인원 2명 아니면 3명", "crew", 3, "3명"),
        ("dates", "작업자 3명 이상", "crew", 3, "3명"),
        ("dates", "작업 인원은 -3명", "crew", 3, "3명"),
    ],
)
def test_scalar_set_is_held_on_both_paths(source, form, text, field, value, evidence):
    item = _field(field, value, evidence)
    req = _request(form, text, rules=[item] if source == "rule" else None)
    reviewed, _ = review_fields([item], source, req, CTX)
    _assert_held(reviewed[field])
    if source == "rule":
        result = asyncio.run(interpret(req, CTX, use_model=False))
        _assert_held(next(candidate for candidate in result.fields if candidate.field == field))


@pytest.mark.parametrize("source", ["rule", "llm"])
@pytest.mark.parametrize(
    ("form", "text", "field", "value", "evidence"),
    [
        ("quote", "견적 삼백만원, 기간은 아직 미정", "amount", 3_000_000, "삼백만원"),
        ("quote", "견적 삼백만원이고 기간은 아직 미정", "amount", 3_000_000, "삼백만원"),
        ("quote", "견적 삼백만원. 기간은 다시 고민 중이에요.", "amount", 3_000_000, "삼백만원"),
        ("quote", "견적 455만 5백 원", "amount", 4_550_500, "455만 5백 원"),
        ("quote", "견적 1천500만원, 공사 기간은 미정", "amount", 15_000_000, "1천500만원"),
        ("dates", "인원 3명, 작업일은 아직 미정", "crew", 3, "3명"),
        ("dates", "12/8부터 12/10까지 공사, 2명", "crew", 2, "2명"),
        ("quote", "견적 10억원", "amount", 1_000_000_000, "10억원"),
        ("dates", "인원 6명", "crew", 6, "6명"),
    ],
)
def test_independent_field_uncertainty_and_exact_units(source, form, text, field, value, evidence):
    req = _request(form, text)
    reviewed, _ = review_fields([_field(field, value, evidence)], source, req, CTX)
    candidate = reviewed[field]
    assert (candidate.action, candidate.status, candidate.value, candidate.source) == (
        "set",
        "valid",
        value,
        source,
    )


@pytest.mark.parametrize("source", ["rule", "llm"])
def test_mixed_amount_never_accepts_truncated_value(source):
    req = _request("quote", "견적 455만 5백 원")
    reviewed, _ = review_fields([_field("amount", 4_550_000, "455만")], source, req, CTX)
    _assert_held(reviewed["amount"])


@pytest.mark.parametrize("source", ["rule", "llm"])
@pytest.mark.parametrize("metadata", [False, True])
@pytest.mark.parametrize(
    ("form", "field", "old", "new", "old_text", "new_text", "text"),
    [
        (
            "quote",
            "amount",
            5_000_000,
            5_500_000,
            "500만원",
            "550만원",
            "500만원 말고 550만원으로 수정해 주세요. 금액 정정은 취소할게요.",
        ),
        (
            "dates",
            "crew",
            3,
            4,
            "3명",
            "4명",
            "작업 인원은 3명 말고 4명으로 바꿔 주세요. 다시 보류할게요.",
        ),
    ],
)
def test_later_cancellation_defeats_verified_or_inferred_correction(
    source, metadata, form, field, old, new, old_text, new_text, text
):
    relation = (
        CorrectionRelation(
            kind="replace",
            rejected_value=old,
            rejected_evidence=old_text,
            accepted_evidence=new_text,
        )
        if metadata
        else None
    )
    item = _field(field, new, new_text, relation=relation)
    reviewed, _ = review_fields([item], source, _request(form, text), CTX)
    _assert_held(reviewed[field])


def _prior(amount=4_550_000, *, baseline=6_000_000, text="견적 455만 원", evidence="455만 원"):
    return PreviousCandidate(
        request_id="old",
        text=text,
        fields=[_field("amount", amount, evidence)],
        baselines={"amount": baseline},
        sources={"amount": "llm"},
        origins={"amount": "old"},
    )


@pytest.mark.parametrize("source", ["rule", "llm"])
@pytest.mark.parametrize("prior_state", ["valid", "missing", "stale", "wrong"])
def test_historical_amount_without_optional_relation_is_verified(source, prior_state):
    prior = _prior() if prior_state != "missing" else None
    if prior_state == "wrong":
        prior = _prior(4_560_000, text="견적 456만 원", evidence="456만 원")
    current = {"amount": 6_100_000 if prior_state == "stale" else 6_000_000}
    text = "아까 455만 원이라고 했는데 487만 원으로 수정할게요."
    req = _request("quote", text, prior=prior, current=current)
    item = _field("amount", 4_870_000, "487만 원")
    checked, _ = review_fields([item], source, req, CTX, previous=verified_previous(req, CTX))
    if prior_state == "valid":
        assert checked["amount"].status == "valid"
        assert checked["amount"].value == 4_870_000
        assert checked["amount"].correction is not None
    else:
        _assert_held(checked["amount"])


def test_corrected_amount_history_can_be_verified_on_next_turn():
    history = "견적 455만 원\n아까 455만 원이라고 했는데 487만 원으로 수정할게요."
    previous = _prior(4_870_000, text=history, evidence="487만 원")
    req = _request("quote", "금액 그대로", current={"amount": 6_000_000}, prior=previous)
    safe = verified_previous(req, CTX)
    assert safe is not None
    assert safe.fields[0].value == 4_870_000
    assert safe.origins["amount"] == "old"
    cancelled = previous.model_copy(update={"text": history + "\n그 요청은 취소할게요."})
    req = _request("quote", "금액 그대로", current={"amount": 6_000_000}, prior=cancelled)
    assert verified_previous(req, CTX) is None


@pytest.mark.parametrize("source", ["rule", "llm"])
@pytest.mark.parametrize("cancelled", [False, True])
def test_verified_crew_change_without_metadata_obeys_later_cancellation(source, cancelled):
    previous = PreviousCandidate(
        request_id="old",
        text="인원 5명",
        fields=[_field("crew", 5, "5명")],
        baselines={"crew": 2},
    )
    text = "인원 5명에서 4명으로 정정할게요."
    if cancelled:
        text += " 인원 변경은 안 할게요."
    req = _request("dates", text, current={"crew": 2}, prior=previous)
    checked, _ = review_fields(
        [_field("crew", 4, "4명")], source, req, CTX, previous=verified_previous(req, CTX)
    )
    if cancelled:
        _assert_held(checked["crew"])
    else:
        assert checked["crew"].status == "valid"
        assert checked["crew"].value == 4
        assert checked["crew"].correction is not None


def test_model_stub_and_rule_only_never_return_negated_amount(monkeypatch):
    text = "견적은 500만원으로 넣지 마세요. 부가세 별도"
    amount = _field("amount", 5_000_000, "500만원")
    vat = _field("vat", "excluded", "부가세 별도")
    req = _request("quote", text, current={"amount": 4_550_000}, rules=[amount, vat])

    async def fake_model(*_args):
        return ModelInterpretation(fields=[amount, vat])

    monkeypatch.setattr(vendor_assist, "interpret", fake_model)
    for enabled in (False, True):
        got = asyncio.run(interpret(req, CTX, use_model=enabled))
        fields = {field.field: field for field in got.fields}
        _assert_held(fields["amount"])
        assert fields["vat"].status == "valid" and fields["vat"].value == "excluded"
