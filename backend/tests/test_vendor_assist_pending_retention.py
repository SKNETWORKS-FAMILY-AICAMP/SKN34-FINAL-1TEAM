"""#179: pending fields survive unrelated/retaining follow-ups, never stale or canceled ones.

Synthetic inputs only. Exercise assist_service.interpret with the existing ModelInterpretation
schema and an adapter stub. The first turn is interpreted for real before its validated
pending fields are serialized into the next turn; no model API or database is called.
"""

import asyncio
from datetime import date
from uuid import UUID

import pytest

from app.agent import vendor_assist
from app.vendor import assist_service
from app.vendor.assist_schemas import (
    AssistContext,
    AssistRequest,
    FieldIntent,
    ModelInterpretation,
    PreviousCandidate,
)

RESOURCE = UUID("00000000-0000-4000-8000-000000000179")
TODAY = date(2026, 11, 23)


def context(**kwargs) -> AssistContext:
    return AssistContext(today=TODAY, base_month=date(2026, 12, 1), **kwargs)


def field(name: str, action: str, value, evidence: str = "", **options) -> FieldIntent:
    return FieldIntent(field=name, action=action, value=value, evidence=evidence, **options)


def neutral(name: str) -> FieldIntent:
    return field(name, "keep", None, intent_kind="no_mention")


def keep_previous(name: str, evidence: str) -> FieldIntent:
    return field(
        name,
        "keep",
        None,
        evidence,
        intent_kind="retain_previous",
        value_source="previous",
    )


def keep_current(name: str, evidence: str) -> FieldIntent:
    return field(
        name,
        "keep",
        None,
        evidence,
        intent_kind="retain_current",
        value_source="current",
    )


def uncertain(name: str, evidence: str) -> FieldIntent:
    return field(name, "uncertain", None, evidence, intent_kind="ambiguous")


def request(
    form: str,
    text: str,
    current: dict,
    *,
    name: str,
    previous: PreviousCandidate | None = None,
    rules: list[FieldIntent] | None = None,
) -> AssistRequest:
    return AssistRequest(
        request_id=name,
        form=form,
        resource_id=RESOURCE,
        text=text,
        current=current,
        previous_candidate=previous,
        rule_fields=rules or [],
    )


def interpret(monkeypatch, data: AssistRequest, model_fields: list[FieldIntent], ctx=None):
    """Return the real validated service response, trace and model-visible filtered request."""
    seen = []

    async def fake_model(safe_request, model_context):
        seen.append((safe_request, model_context))
        return ModelInterpretation(fields=model_fields)

    monkeypatch.setattr(vendor_assist, "interpret", fake_model)
    trace = {}
    response = asyncio.run(assist_service.interpret(data, ctx or context(), trace=trace))
    assert len(seen) == 1, "stub failed or service silently used a fallback"
    assert response.source == "llm"
    assert trace["model_raw"] is not None
    assert response.request_id == data.request_id
    assert response.form == data.form
    assert not any("AI 해석을 사용할 수 없어" in warning for warning in response.warnings)
    return response, trace, seen[0][0]


def candidate(response, name: str):
    return next(item for item in response.fields if item.field == name)


def assert_pending(response, name: str, value, *, origin: str, source: str | None = None):
    item = candidate(response, name)
    assert (item.action, item.status, item.value) == ("set", "valid", value), item
    assert item.origin_request_id == origin, item
    if source:
        assert item.source == source, item


def assert_not_pending(response, name: str):
    item = candidate(response, name)
    assert not (item.status == "valid" and item.action in ("set", "clear")), item


def carry(
    response,
    text: str,
    initial_current: dict,
    *,
    earlier: PreviousCandidate | None = None,
) -> PreviousCandidate:
    """Serialize only validated pending fields, preserving original baseline/source/origin."""
    chosen = [
        item
        for item in response.fields
        if item.status == "valid" and item.action in ("set", "clear")
    ]
    assert chosen, "a first-turn producer must really create a pending proposal"
    return PreviousCandidate(
        request_id=response.request_id,
        text=(earlier.text + "\n" + text) if earlier else text,
        fields=[field(item.field, item.action, item.value, item.evidence) for item in chosen],
        sources={item.field: item.source for item in chosen},
        origins={item.field: item.origin_request_id or response.request_id for item in chosen},
        baselines={
            item.field: (
                earlier.baselines[item.field]
                if earlier is not None
                and item.field in earlier.baselines
                and item.origin_request_id == earlier.origins.get(item.field, earlier.request_id)
                else initial_current[item.field]
            )
            for item in chosen
        },
    )


def first_period(monkeypatch, *, end: str = "2026-12-20"):
    original = {
        "start": "2026-12-05",
        "end": "2026-12-22",
        "excluded": [],
    }
    date_word = "20" if end.endswith("20") else "25"
    text = f"실측 시작일은 12월 7일, 종료일은 12월 {date_word}일로 제안해 주세요."
    # A targeted rule can produce an independent valid end while the model leaves it unmentioned.
    first = request(
        "period",
        text,
        original,
        name="period-origin",
        rules=[field("end", "set", end, text)],
    )
    result, _, _ = interpret(
        monkeypatch,
        first,
        [
            field("start", "set", "2026-12-07", "12월 7일"),
            neutral("end"),
        ],
    )
    assert_pending(result, "start", "2026-12-07", origin="period-origin", source="llm")
    assert_pending(result, "end", end, origin="period-origin", source="rule")
    return carry(result, text, original), original


def first_quote(monkeypatch):
    original = {
        "amount": 3_200_000,
        "vat": "excluded",
        "days": 2,
        "valid": None,
    }
    text = "견적은 590만 원이고 부가세는 면세로 제안해 주세요."
    data = request(
        "quote",
        text,
        original,
        name="quote-origin-1",
        rules=[field("vat", "set", "exempt", text)],
    )
    response, _, _ = interpret(
        monkeypatch,
        data,
        [
            field("amount", "set", 5_900_000, "590만 원"),
            neutral("vat"),
        ],
    )
    assert_pending(response, "amount", 5_900_000, origin="quote-origin-1", source="llm")
    assert_pending(response, "vat", "exempt", origin="quote-origin-1", source="rule")
    return carry(response, text, original), original


def quote_second(monkeypatch):
    old, original = first_quote(monkeypatch)
    text = "부가세는 포함으로 고치고, 견적 금액은 이전 제안 그대로 유지해 주세요."
    data = request("quote", text, original, name="quote-origin-2", previous=old)
    response, trace, safe = interpret(
        monkeypatch,
        data,
        [
            field("vat", "set", "included", "부가세는 포함"),
            keep_previous("amount", "견적 금액은 이전 제안 그대로 유지"),
        ],
    )
    assert safe.previous_candidate is not None
    assert trace["previous_verified"]["origins"]["amount"] == "quote-origin-1"
    assert_pending(response, "amount", 5_900_000, origin="quote-origin-1", source="llm")
    assert_pending(response, "vat", "included", origin="quote-origin-2", source="llm")
    return carry(response, text, original, earlier=old), original


def first_dates(monkeypatch):
    original = {"days": [], "crew": 1}
    text = "작업은 12월 8일부터 12월 10일까지, 작업 인원은 2명으로 제안해 주세요."
    data = request(
        "dates",
        text,
        original,
        name="dates-origin",
        rules=[field("crew", "set", 2, text)],
    )
    response, _, _ = interpret(
        monkeypatch,
        data,
        [
            field(
                "days",
                "set",
                ["2026-12-08", "2026-12-09", "2026-12-10"],
                "12월 8일부터 12월 10일까지",
            ),
            neutral("crew"),
        ],
        context(required_days=3),
    )
    assert_pending(
        response,
        "days",
        ["2026-12-08", "2026-12-09", "2026-12-10"],
        origin="dates-origin",
        source="llm",
    )
    assert_pending(response, "crew", 2, origin="dates-origin", source="rule")
    return carry(response, text, original), original


def first_extra(monkeypatch):
    original = {
        "title": "하자 점검",
        "reason": "초기 점검 사유",
        "amount": 120_000,
        "vat": "excluded",
    }
    text = "사유는 고객 요청으로 공정 변경, 추가 금액은 35만 원으로 제안해 주세요."
    data = request("extra", text, original, name="extra-origin")
    response, _, _ = interpret(
        monkeypatch,
        data,
        [
            field("reason", "set", "고객 요청으로 공정 변경", "고객 요청으로 공정 변경"),
            field("amount", "set", 350_000, "35만 원"),
        ],
    )
    assert_pending(
        response, "reason", "고객 요청으로 공정 변경", origin="extra-origin", source="llm"
    )
    assert_pending(response, "amount", 350_000, origin="extra-origin", source="llm")
    return carry(response, text, original), original


def first_extra_vat(monkeypatch):
    original = {
        "title": "하자 점검",
        "reason": "초기 점검 사유",
        "amount": 120_000,
        "vat": "excluded",
    }
    text = "추가 금액은 24만 원이고, 부가세는 포함으로 정해 주세요."
    data = request("extra", text, original, name="extra-vat-origin")
    response, _, _ = interpret(
        monkeypatch,
        data,
        [
            field("amount", "set", 240_000, "24만 원"),
            field("vat", "set", "included", "부가세는 포함"),
        ],
    )
    assert_pending(response, "vat", "included", origin="extra-vat-origin")
    return carry(response, text, original), original


def test_period_end_survives_manual_start_and_start_date_mention(monkeypatch):
    old, current = first_period(monkeypatch)
    current = dict(current, start="2026-12-08")  # manual before Send; not an Apply
    text = "시작 날짜는 제가 12월 8일로 고쳤고 현재 폼 값 그대로 유지하겠습니다."
    req = request("period", text, current, name="period-followup", previous=old)
    response, trace, safe = interpret(
        monkeypatch,
        req,
        [
            keep_current("start", "시작 날짜는 제가 12월 8일로 고쳤고 현재 폼 값 그대로 유지"),
            neutral("end"),
        ],
    )
    assert trace["previous_rejected"]["start"] == "stale_context"
    assert safe.previous_candidate is not None
    assert [item.field for item in safe.previous_candidate.fields] == ["end"]
    assert_not_pending(response, "start")
    assert_pending(response, "end", "2026-12-20", origin="period-origin", source="rule")


def test_period_start_survives_uncertain_end_only(monkeypatch):
    old, current = first_period(monkeypatch, end="2026-12-25")
    text = "종료일은 25일인지 27일인지 아직 결정 못 했습니다."
    req = request("period", text, current, name="period-uncertain", previous=old)
    response, trace, _ = interpret(
        monkeypatch,
        req,
        [
            uncertain("end", "25일인지 27일인지 아직 결정 못 했습니다"),
            neutral("start"),
        ],
    )
    assert trace["previous_verified"]["origins"]["start"] == "period-origin"
    assert_not_pending(response, "end")
    assert_pending(response, "start", "2026-12-07", origin="period-origin", source="llm")


@pytest.mark.parametrize(
    ("text", "model_fields"),
    [
        (
            "공사 기간은 4일로 조정해 주세요.",
            [neutral("amount"), neutral("vat"), field("days", "set", 4, "4일")],
        ),
        (
            "견적 금액과 부가세는 앞서 정리한 안으로 두고 공사 기간을 4일로 바꿔 주세요.",
            [
                keep_previous("amount", "견적 금액과 부가세는 앞서 정리한 안으로 두고"),
                keep_previous("vat", "견적 금액과 부가세는 앞서 정리한 안으로 두고"),
                field("days", "set", 4, "4일"),
            ],
        ),
    ],
    ids=["unrelated-day-change", "explicit-grouped-prior-retention"],
)
def test_quote_amount_and_vat_survive_third_turn(monkeypatch, text, model_fields):
    old, current = quote_second(monkeypatch)
    assert old.origins == {"amount": "quote-origin-1", "vat": "quote-origin-2"}
    assert old.baselines == {"amount": 3_200_000, "vat": "excluded"}
    req = request("quote", text, current, name="quote-origin-3", previous=old)
    response, trace, safe = interpret(monkeypatch, req, model_fields)
    assert safe.previous_candidate is not None
    assert trace["previous_verified"]["origins"] == old.origins
    assert_pending(response, "amount", 5_900_000, origin="quote-origin-1", source="llm")
    assert_pending(response, "vat", "included", origin="quote-origin-2", source="llm")
    assert_pending(response, "days", 4, origin="quote-origin-3")


@pytest.mark.parametrize(
    ("text", "crew_decision"),
    [
        (
            "작업 날짜는 제가 12월 16일부터 18일까지로 직접 바꿨고 현재 입력 그대로 둡니다.",
            neutral("crew"),
        ),
        (
            "작업 날짜는 제가 다시 입력한 대로 두고 인원은 지난 제안대로 진행해 주세요.",
            keep_previous("crew", "인원은 지난 제안대로 진행"),
        ),
    ],
    ids=["no-crew-mention", "prior-crew-in-followup"],
)
def test_dates_crew_survives_manual_date_edit(monkeypatch, text, crew_decision):
    old, current = first_dates(monkeypatch)
    current = dict(current, days=["2026-12-16", "2026-12-17", "2026-12-18"])
    req = request("dates", text, current, name="dates-manual", previous=old)
    response, trace, safe = interpret(
        monkeypatch,
        req,
        [
            keep_current("days", "작업 날짜는 제가 다시 입력한 대로 두고")
            if "다시 입력" in text
            else keep_current(
                "days", "작업 날짜는 제가 12월 16일부터 18일까지로 직접 바꿨고 현재 입력 그대로"
            ),
            crew_decision,
        ],
        context(required_days=3),
    )
    assert trace["previous_rejected"]["days"] == "stale_context"
    assert safe.previous_candidate is not None
    assert [item.field for item in safe.previous_candidate.fields] == ["crew"]
    assert_not_pending(response, "days")
    assert_pending(response, "crew", 2, origin="dates-origin", source="rule")


def test_dates_crew_survives_explicit_new_days(monkeypatch):
    old, current = first_dates(monkeypatch)
    text = "공사 날짜만 12월 11일부터 12월 13일까지로 변경하겠습니다."
    req = request("dates", text, current, name="dates-new", previous=old)
    response, trace, _ = interpret(
        monkeypatch,
        req,
        [
            field(
                "days",
                "set",
                ["2026-12-11", "2026-12-12", "2026-12-13"],
                "12월 11일부터 12월 13일까지",
            ),
            neutral("crew"),
        ],
        context(required_days=3),
    )
    assert trace["previous_verified"]["origins"]["crew"] == "dates-origin"
    assert_pending(response, "days", ["2026-12-11", "2026-12-12", "2026-12-13"], origin="dates-new")
    assert_pending(response, "crew", 2, origin="dates-origin", source="rule")


@pytest.mark.parametrize(
    ("text", "reason_decision"),
    [
        (
            "추가 금액이 35만 원인지 38만 원인지 아직 결정 못 했습니다.",
            neutral("reason"),
        ),
        (
            "추가 금액은 35만 원인지 38만 원인지 미정입니다. 사유는 아까 적은 내용으로 둘게요.",
            keep_previous("reason", "사유는 아까 적은 내용으로 둘게요"),
        ),
    ],
    ids=["unrelated-uncertain-money", "prior-reason-in-followup"],
)
def test_extra_reason_survives_undecided_amount(monkeypatch, text, reason_decision):
    old, current = first_extra(monkeypatch)
    req = request("extra", text, current, name="extra-followup", previous=old)
    response, trace, _ = interpret(
        monkeypatch,
        req,
        [
            uncertain("amount", "35만 원인지 38만 원인지 아직 결정 못 했습니다")
            if "결정" in text
            else uncertain("amount", "35만 원인지 38만 원인지 미정입니다"),
            reason_decision,
        ],
    )
    assert trace["previous_verified"]["origins"]["reason"] == "extra-origin"
    assert_not_pending(response, "amount")
    assert_pending(
        response, "reason", "고객 요청으로 공정 변경", origin="extra-origin", source="llm"
    )


def test_extra_vat_survives_unrelated_amount_correction(monkeypatch):
    old, current = first_extra_vat(monkeypatch)
    text = "추가 금액만 26만 원으로 바로잡겠습니다. 세금 조건은 아까 정한 안으로 두세요."
    req = request("extra", text, current, name="extra-vat-followup", previous=old)
    response, trace, _ = interpret(
        monkeypatch,
        req,
        [
            field("amount", "set", 260_000, "26만 원"),
            keep_previous("vat", "세금 조건은 아까 정한 안으로 두세요"),
        ],
    )
    assert trace["previous_verified"]["origins"]["vat"] == "extra-vat-origin"
    assert_pending(response, "amount", 260_000, origin="extra-vat-followup")
    assert_pending(response, "vat", "included", origin="extra-vat-origin")


@pytest.mark.parametrize("state", ["missing", "manual-stale"])
def test_quote_missing_or_stale_amount_baseline_never_rehydrates(monkeypatch, state):
    old, current = quote_second(monkeypatch)
    if state == "missing":
        old = old.model_copy(update={"baselines": {"vat": "excluded"}})
    else:
        current = dict(current, amount=4_100_000)
    text = "견적 금액은 이전 제안 그대로 두고, 부가세는 그때 정한 방식으로 유지해 주세요."
    req = request("quote", text, current, name="quote-stale", previous=old)
    response, trace, safe = interpret(
        monkeypatch,
        req,
        [
            keep_previous("amount", "견적 금액은 이전 제안 그대로"),
            keep_previous("vat", "부가세는 그때 정한 방식으로 유지"),
        ],
    )
    assert trace["previous_rejected"]["amount"] == "stale_context"
    assert safe.previous_candidate is not None
    assert [item.field for item in safe.previous_candidate.fields] == ["vat"]
    assert_not_pending(response, "amount")
    assert_pending(response, "vat", "included", origin="quote-origin-2")


def test_explicit_current_ignores_unapplied_old_quote(monkeypatch):
    old, current = first_quote(monkeypatch)
    text = "견적 금액은 이전 제안 취소하고 지금 입력한 값 그대로 유지해 주세요."
    req = request("quote", text, current, name="quote-current", previous=old)
    response, trace, _ = interpret(
        monkeypatch,
        req,
        [
            keep_current("amount", "견적 금액은 이전 제안 취소하고 지금 입력한 값 그대로 유지"),
            neutral("vat"),
        ],
    )
    assert trace["previous_verified"]["origins"]["amount"] == "quote-origin-1"
    amount = candidate(response, "amount")
    assert (amount.action, amount.status, amount.value, amount.intent_kind) == (
        "keep",
        "valid",
        None,
        "retain_current",
    )
    assert_pending(response, "vat", "exempt", origin="quote-origin-1", source="rule")


def test_canceled_pending_quote_and_unrelated_pending_vat(monkeypatch):
    old, current = first_quote(monkeypatch)
    text = "지난 금액 변경은 취소할게요. 부가세 조건은 앞서 제안 그대로 유지해 주세요."
    req = request("quote", text, current, name="quote-cancel", previous=old)
    response, _, _ = interpret(
        monkeypatch,
        req,
        [
            neutral("amount"),
            keep_previous("vat", "부가세 조건은 앞서 제안 그대로 유지"),
        ],
    )
    assert_not_pending(response, "amount")
    assert_pending(response, "vat", "exempt", origin="quote-origin-1", source="rule")


def test_negated_previous_retention_does_not_apply_old_quote(monkeypatch):
    old, current = first_quote(monkeypatch)
    text = "견적 금액을 이전 안 그대로 유지하지 말고 검토해 주세요."
    req = request("quote", text, current, name="quote-negative", previous=old)
    response, _, _ = interpret(
        monkeypatch,
        req,
        [
            keep_previous("amount", "견적 금액을 이전 안 그대로 유지하지 말고"),
            neutral("vat"),
        ],
    )
    assert_not_pending(response, "amount")
    assert_pending(response, "vat", "exempt", origin="quote-origin-1", source="rule")


def test_wrong_field_retention_cannot_be_used_as_other_field_proof(monkeypatch):
    old, current = first_dates(monkeypatch)
    text = "인원은 지난 제안 그대로 유지하고 공사 날짜는 아직 검토 중입니다."
    req = request("dates", text, current, name="dates-wrong-field", previous=old)
    response, trace, _ = interpret(
        monkeypatch,
        req,
        [
            keep_previous("days", "인원은 지난 제안 그대로 유지"),
            keep_previous("crew", "인원은 지난 제안 그대로 유지"),
        ],
        context(required_days=3),
    )
    assert trace["previous_verified"] is not None
    assert_not_pending(response, "days")  # '인원' is not evidence for a schedule change.
    assert_pending(response, "crew", 2, origin="dates-origin", source="rule")


def test_previous_clear_stays_pending_on_unrelated_amount_followup(monkeypatch):
    current = {"amount": 3_200_000, "vat": "excluded", "days": 2, "valid": "2026-12-27"}
    first_text = "견적 유효기간만 비워 주세요."
    first_req = request("quote", first_text, current, name="valid-clear-origin")
    first_result, _, _ = interpret(
        monkeypatch,
        first_req,
        [
            field("valid", "clear", None, "유효기간만 비워 주세요"),
        ],
    )
    cleared = candidate(first_result, "valid")
    assert (cleared.action, cleared.status, cleared.value) == ("clear", "valid", None)
    old = carry(first_result, first_text, current)
    next_text = "견적 금액을 380만 원으로 변경하겠습니다."
    data = request("quote", next_text, current, name="valid-clear-next", previous=old)
    response, _, _ = interpret(
        monkeypatch,
        data,
        [
            field("amount", "set", 3_800_000, "380만 원"),
            neutral("valid"),
        ],
    )
    assert_pending(response, "amount", 3_800_000, origin="valid-clear-next")
    value = candidate(response, "valid")
    assert (value.action, value.status, value.value) == ("clear", "valid", None)
    assert value.origin_request_id == "valid-clear-origin"
