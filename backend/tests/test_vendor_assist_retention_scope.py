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
from app.vendor.assist_validation import review_fields


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


def _run(monkeypatch, form, current, old_text, old_fields, text, intents, *, baselines=None):
    previous = PreviousCandidate(
        request_id="known-prior",
        text=old_text,
        fields=old_fields,
        baselines=(
            {part.field: current[part.field] for part in old_fields}
            if baselines is None
            else baselines
        ),
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


def _period_request(text, current=None):
    return AssistRequest(
        request_id="period-turn",
        resource_id=UUID("00000000-0000-4000-8000-000000000179"),
        form="period",
        text=text,
        current=current
        or {
            "start": "2026-12-05",
            "end": "2026-12-18",
            "excluded": ["2026-12-10", "2026-12-12"],
        },
        previous_candidate=None,
        rule_fields=[],
    )


def _checked_period_clear(text, evidence, field="excluded"):
    data = _period_request(text)
    verified, _ = review_fields(
        [_field(field, "clear", None, evidence, intent_kind="explicit_change")],
        "llm",
        data,
        AssistContext(today=date(2026, 11, 23), base_month=date(2026, 12, 1)),
    )
    return verified[field]


@pytest.mark.parametrize(
    ("text", "evidence"),
    [
        ("전에 빼둔 날짜 다 풀어줘요 이제 기간 내내 가능해요", "전에 빼둔 날짜 다 풀어줘요"),
        ("기존 제외했던 날 모두 풀어 주세요", "기존 제외했던 날 모두 풀어 주세요"),
        ("쉬는 날은 전부 풀어줘요", "쉬는 날은 전부 풀어줘요"),
    ],
)
def test_explicit_all_exclusion_release_clears_only_excluded_days(text, evidence):
    cleared = _checked_period_clear(text, evidence)
    assert (cleared.action, cleared.status, cleared.value) == ("clear", "valid", None)
    assert cleared.source == "llm"
    assert _checked_period_clear(text, evidence, field="start").status != "valid"
    assert _checked_period_clear(text, evidence, field="end").status != "valid"


@pytest.mark.parametrize(
    "text",
    [
        "전에 빼둔 날짜 중 10일만 풀어줘요",
        "전에 빼둔 날짜 다 풀지 마세요",
        "전에 빼둔 날짜 다 풀어줘요. 아, 그 요청은 취소합니다.",
        "전에 빼둔 날짜 다 풀어줘요라고 말한 적은 없어요",
        "전에 빼둔 날짜 다 풀어줄까요?",
        "전에 빼둔 날짜 다 풀어주는 건 아니에요",
        "전에 빼둔 날짜 다 풀어줄지 아직 고민 중이에요",
        "현재 기간에 제외된 날짜 다 기억해 줘요",
        "전에 빼둔 날짜는 유지하고 자재 묶음 다 풀어줘요",
        "전에 빼둔 날짜는 그대로 두고 자재 묶음은 모두 풀어 주세요",
        "전에 빼둔 날짜 다 풀면 연락 주세요",
        "전에 빼둔 날짜 다 풀어줘야 하면 연락 주세요",
        "전에 빼둔 날짜 다 풀어주면 검토할게요",
        "전에 빼둔 날짜 다 풀어 주세요 말고 그대로 유지해 주세요",
        "전에 빼둔 날짜 다 풀어줘요 취소할게요",
    ],
)
def test_partial_negated_or_uncertain_exclusion_release_never_clears_everything(text):
    cleared = _checked_period_clear(text, text)
    assert not (cleared.status == "valid" and cleared.action == "clear"), cleared


def test_exclusion_release_checks_question_outside_clipped_evidence():
    text = "전에 빼둔 날짜 다 풀어줘요? 확실할까요?"
    cleared = _checked_period_clear(text, "전에 빼둔 날짜 다 풀어줘요")
    assert not (cleared.status == "valid" and cleared.action == "clear"), cleared


def test_exclusion_release_allows_uncertain_other_field_in_separate_clause():
    text = "전에 빼둔 날짜 다 풀어 주세요, 종료일은 아직 미정입니다."
    cleared = _checked_period_clear(text, "전에 빼둔 날짜 다 풀어 주세요")
    assert (cleared.action, cleared.status) == ("clear", "valid")


def test_period_clear_service_does_not_change_other_fields(monkeypatch):
    text = "전에 빼둔 날짜 다 풀어줘요 이제 기간 내내 가능해요"
    data = _period_request(text)

    async def fake_model(_safe_request, _context):
        return ModelInterpretation(
            fields=[
                _field("excluded", "clear", None, "전에 빼둔 날짜 다 풀어줘요"),
            ]
        )

    monkeypatch.setattr(vendor_assist, "interpret", fake_model)
    response = asyncio.run(
        interpret(data, AssistContext(today=date(2026, 11, 23), base_month=date(2026, 12, 1)))
    )
    fields = {part.field: part for part in response.fields}
    assert (fields["excluded"].action, fields["excluded"].status) == ("clear", "valid")
    assert all(fields[name].action == "keep" for name in ("start", "end"))


def test_period_prior_exclusion_retention_preserves_provenance_with_end_change(monkeypatch):
    text = "아까 쉰다고 한 날은 그대로 두고 21일까지만 할게요"
    result, trace = _run(
        monkeypatch,
        "period",
        {"start": "2026-12-05", "end": "2026-12-18", "excluded": []},
        "12월 24일까지 가능하고 중간에 12일 하루만 쉽니다.",
        [
            _field("end", "set", "2026-12-24", "12월 24일까지 가능"),
            _field("excluded", "set", ["2026-12-12"], "12일 하루만 쉽니다"),
        ],
        text,
        [
            _field("end", "set", "2026-12-21", "21일까지만 할게요"),
            _retain("excluded", "아까 쉰다고 한 날은 그대로 두고"),
        ],
    )
    assert trace["previous_verified"] is not None
    assert {part["field"] for part in trace["previous_verified"]["fields"]} == {
        "end",
        "excluded",
    }
    _valid(result["excluded"], ["2026-12-12"])
    assert result["excluded"].value_source == "previous"
    assert (result["end"].status, result["end"].value) == ("valid", "2026-12-21")


def test_period_retention_of_excluded_days_ignores_unrelated_uncertainty(monkeypatch):
    text = "아까 쉰다고 한 날은 그대로 두고, 종료일은 20일인지 21일인지 미정이에요."
    result, _ = _run(
        monkeypatch,
        "period",
        {"start": "2026-12-05", "end": "2026-12-18", "excluded": []},
        "12일은 쉬어요.",
        [_field("excluded", "set", ["2026-12-12"], "12일은 쉬어요")],
        text,
        [
            _retain("excluded", "아까 쉰다고 한 날은 그대로 두고"),
            _field("end", "uncertain", None, "종료일은 20일인지 21일인지 미정"),
        ],
    )
    _valid(result["excluded"], ["2026-12-12"])
    assert result["end"].status != "valid"


@pytest.mark.parametrize(
    "text",
    [
        "아까 쉰다고 한 날은 그대로 두지 마세요",
        "아까 쉰다고 한 날은 그대로 둘까요?",
        "아까 쉰다고 한 날은 아직 미정이에요",
        "인원은 앞서 제안대로 유지해 주세요",
    ],
)
def test_period_prior_exclusion_requires_affirmative_same_field_retention(monkeypatch, text):
    result, _ = _run(
        monkeypatch,
        "period",
        {"start": "2026-12-05", "end": "2026-12-18", "excluded": []},
        "12일은 쉬어요.",
        [_field("excluded", "set", ["2026-12-12"], "12일은 쉬어요")],
        text,
        [_retain("excluded", text)],
    )
    _not_restored(result["excluded"])


def test_period_prior_exclusion_manual_change_fails_closed(monkeypatch):
    text = "아까 쉰다고 한 날은 그대로 두고 싶어요"
    result, trace = _run(
        monkeypatch,
        "period",
        {"start": "2026-12-05", "end": "2026-12-18", "excluded": ["2026-12-13"]},
        "12일은 쉬어요.",
        [_field("excluded", "set", ["2026-12-12"], "12일은 쉬어요")],
        text,
        [_retain("excluded", text)],
        baselines={"excluded": []},
    )
    assert trace["previous_rejected"]["excluded"] == "stale_context"
    _not_restored(result["excluded"])


def test_stale_end_does_not_erase_independently_verified_excluded_days(monkeypatch):
    text = "전에 쉰다고 한 날은 그대로 두겠습니다."
    result, trace = _run(
        monkeypatch,
        "period",
        {"start": "2026-12-05", "end": "2026-12-20", "excluded": []},
        "12월 24일까지 가능하고 12일은 쉽니다.",
        [
            _field("end", "set", "2026-12-24", "12월 24일까지 가능"),
            _field("excluded", "set", ["2026-12-12"], "12일은 쉽니다"),
        ],
        text,
        [_retain("excluded", text)],
        baselines={"end": "2026-12-18", "excluded": []},
    )
    assert trace["previous_rejected"]["end"] == "stale_context"
    _valid(result["excluded"], ["2026-12-12"])
    assert result["end"].status != "valid"
