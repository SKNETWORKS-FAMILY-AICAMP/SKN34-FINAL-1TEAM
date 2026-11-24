"""이전 제안의 국소 근거·대상·값이 맞을 때에만 보존하는 서비스 회귀."""

import asyncio
from datetime import date
from uuid import UUID

import pytest

from app.agent import vendor_assist
from app.vendor.assist_schemas import (
    AssistContext,
    AssistRequest,
    FieldIntent,
    ModelInterpretation,
    PreviousCandidate,
)
from app.vendor.assist_service import interpret


def _field(name, action, value, evidence, **attrs):
    return FieldIntent(field=name, action=action, value=value, evidence=evidence, **attrs)


def _retain(name, evidence):
    return _field(
        name,
        "keep",
        None,
        evidence,
        intent_kind="retain_previous",
        value_source="previous",
    )


def _run(monkeypatch, form, current, old_text, old_fields, text, intents):
    previous = PreviousCandidate(
        request_id="known-prior",
        text=old_text,
        fields=old_fields,
        baselines={part.field: current[part.field] for part in old_fields},
        sources={part.field: "rule" for part in old_fields},
        origins={part.field: "known-prior" for part in old_fields},
    )
    data = AssistRequest(
        request_id="new-turn",
        resource_id=UUID("00000000-0000-4000-8000-000000000179"),
        form=form,
        text=text,
        current=current,
        previous_candidate=previous,
        rule_fields=[],
    )

    async def fake_model(safe_request, _context):
        assert safe_request.previous_candidate is not None
        return ModelInterpretation(fields=intents)

    monkeypatch.setattr(vendor_assist, "interpret", fake_model)
    trace = {}
    result = asyncio.run(
        interpret(
            data,
            AssistContext(today=date(2026, 11, 23), base_month=date(2026, 12, 1)),
            trace=trace,
        )
    )
    return {item.field: item for item in result.fields}, trace


def _quote(monkeypatch, text, intents):
    return _run(
        monkeypatch,
        "quote",
        {"amount": 3_200_000, "vat": "excluded", "days": 2, "valid": None},
        "견적 금액 590만 원에 부가세 포함으로 제안합니다.",
        [
            _field("amount", "set", 5_900_000, "590만 원"),
            _field("vat", "set", "included", "부가세 포함"),
        ],
        text,
        intents,
    )


def _valid(row, value):
    assert (row.status, row.action, row.value, row.origin_request_id) == (
        "valid",
        "set",
        value,
        "known-prior",
    ), row


def _not_restored(row):
    assert not (row.status == "valid" and row.action == "set"), row


def test_retention_with_mismatched_crew_number_is_rejected(monkeypatch):
    text = "인원 5명 제안만 이어서 사용해 주세요."
    result, trace = _run(
        monkeypatch,
        "dates",
        {"days": ["2026-12-08", "2026-12-09", "2026-12-10"], "crew": 1},
        "인원 3명으로 바꿔 주세요.",
        [_field("crew", "set", 3, "3명")],
        text,
        [_retain("crew", "인원 5명 제안만 이어서 사용")],
    )
    assert trace["previous_verified"] is not None
    _not_restored(result["crew"])


def test_negated_group_retention_never_rehydrates_either_field(monkeypatch):
    text = "금액과 부가세는 앞서 정한 안으로 두지 마세요."
    candidate = "금액과 부가세는 앞서 정한 안으로 두지 마세요"
    result, trace = _quote(
        monkeypatch,
        text,
        [_retain("amount", candidate), _retain("vat", candidate)],
    )
    assert {item["field"] for item in trace["previous_verified"]["fields"]} == {
        "amount",
        "vat",
    }
    _not_restored(result["amount"])
    _not_restored(result["vat"])


def test_current_form_and_previous_vat_are_bound_separately(monkeypatch):
    text = "금액은 현재 폼대로, 부가세는 아까 제안대로 진행해 주세요."
    result, _ = _quote(
        monkeypatch,
        text,
        [
            _retain("amount", "금액은 현재 폼대로"),
            _retain("vat", "부가세는 아까 제안대로"),
        ],
    )
    _not_restored(result["amount"])
    _valid(result["vat"], "included")


def test_retention_question_is_ambiguous(monkeypatch):
    text = "금액은 앞서 정한 대로 유지?"
    result, _ = _quote(monkeypatch, text, [_retain("amount", text)])
    _not_restored(result["amount"])


def test_previous_vat_cannot_override_explicit_opposite_tax_condition(monkeypatch):
    text = "부가세 미포함 조건으로 정리했던 제안만 이어서 사용해 주세요."
    result, _ = _quote(monkeypatch, text, [_retain("vat", text)])
    _not_restored(result["vat"])


def test_repeated_ambiguous_evidence_cannot_bind_first_positive_occurrence(monkeypatch):
    evidence = "금액은 앞서 제안대로 유지"
    text = evidence + ". " + evidence + "하지 말아 주세요."
    result, _ = _quote(monkeypatch, text, [_retain("amount", evidence)])
    _not_restored(result["amount"])


def test_unrelated_uncertainty_after_retention_does_not_cancel_vat(monkeypatch):
    text = "부가세는 앞서 제안대로 유지하고 금액은 400만인지 500만인지 미정입니다."
    result, _ = _quote(
        monkeypatch,
        text,
        [
            _retain("vat", "부가세는 앞서 제안대로 유지"),
            _field("amount", "uncertain", None, "400만인지 500만인지 미정입니다"),
        ],
    )
    _valid(result["vat"], "included")
    _not_restored(result["amount"])


@pytest.mark.parametrize(
    "evidence",
    [
        "부가세는 앞서 제안대로 유지",
        "부가세는 앞서 정리한 안으로 두고",
    ],
)
def test_unrelated_field_cannot_claim_explicit_vat_retention(monkeypatch, evidence):
    text = "금액은 610만 원으로 바꾸고 " + evidence + " 진행해 주세요."
    result, _ = _quote(monkeypatch, text, [_retain("amount", text)])
    _not_restored(result["amount"])


def test_field_within_prior_reference_phrase(monkeypatch):
    evidence = "방금 제안한 견적 금액만 그대로 사용해 주세요"
    result, _ = _quote(
        monkeypatch, evidence, [_retain("amount", evidence), _retain("vat", evidence)]
    )
    _valid(result["amount"], 5_900_000)
    _not_restored(result["vat"])
