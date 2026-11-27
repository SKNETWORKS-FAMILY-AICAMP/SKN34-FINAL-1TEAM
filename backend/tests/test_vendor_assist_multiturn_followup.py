"""#179 follow-up: service-level continuity, source provenance, and fail-closed corrections.

Use only the existing HTTP/domain schema and stub the model adapter. These cases assert
observable candidate behavior; they do not depend on a proposed internal API.
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
    CorrectionRelation,
    FieldIntent,
    ModelInterpretation,
    PreviousCandidate,
)

RESOURCE = UUID("00000000-0000-4000-8000-000000000179")
TODAY = date(2026, 11, 23)


def context(*, required_days: int | None = None) -> AssistContext:
    return AssistContext(
        today=TODAY,
        base_month=date(2026, 12, 1),
        required_days=required_days,
    )


def field(name: str, action: str, value, evidence: str, **kwargs) -> FieldIntent:
    return FieldIntent(
        field=name,
        action=action,
        value=value,
        evidence=evidence,
        **kwargs,
    )


def previous(
    text: str,
    items: list[FieldIntent],
    baselines: dict,
    *,
    sources: dict[str, str] | None = None,
    origins: dict[str, str] | None = None,
) -> PreviousCandidate:
    return PreviousCandidate(
        request_id="previous-turn",
        text=text,
        fields=items,
        baselines=baselines,
        sources=sources or {},
        origins=origins or {},
    )


def request(
    form: str,
    text: str,
    *,
    current: dict,
    prior: PreviousCandidate | None = None,
    rules: list[FieldIntent] | None = None,
) -> AssistRequest:
    return AssistRequest(
        request_id="followup-turn",
        form=form,
        resource_id=RESOURCE,
        text=text,
        current=current,
        previous_candidate=prior,
        rule_fields=rules or [],
    )


def quote_prior() -> PreviousCandidate:
    return previous(
        "견적 455만 원, 공사 3일",
        [
            field("amount", "set", 4_550_000, "455만 원"),
            field("days", "set", 3, "3일"),
        ],
        {"amount": 6_000_000, "days": 2},
        sources={"amount": "llm", "days": "rule"},
        origins={"amount": "original-amount", "days": "original-days"},
    )


def extra_prior() -> PreviousCandidate:
    return previous(
        "곰팡이 방지 처리, 추가 금액 12만 원, 부가세 포함",
        [
            field("title", "set", "곰팡이 방지 처리", "곰팡이 방지 처리"),
            field("amount", "set", 120_000, "12만 원"),
            field("vat", "set", "included", "부가세 포함"),
        ],
        {"title": None, "amount": None, "vat": None},
        sources={"title": "rule", "amount": "llm", "vat": "rule"},
        origins={"title": "original-title", "amount": "original-extra", "vat": "original-vat"},
    )


def dates_prior(
    days: tuple[str, ...] = ("2026-12-08", "2026-12-09", "2026-12-10"),
) -> PreviousCandidate:
    return previous(
        "공사 날짜는 " + ", ".join(f"{day[5:7]}월 {int(day[8:])}일" for day in days) + ", 인원 2명",
        [
            field(
                "days",
                "set",
                list(days),
                ", ".join(f"{day[5:7]}월 {int(day[8:])}일" for day in days),
            ),
            field("crew", "set", 2, "인원 2명"),
        ],
        {"days": [], "crew": 1},
        sources={"days": "llm", "crew": "rule"},
        origins={"days": "original-schedule", "crew": "original-crew"},
    )


def period_prior() -> PreviousCandidate:
    return previous(
        "12월 2일부터 12월 7일까지 가능하고, 12월 4일은 제외",
        [
            field("start", "set", "2026-12-02", "12월 2일부터"),
            field("end", "set", "2026-12-07", "12월 7일까지"),
            field("excluded", "set", ["2026-12-04"], "12월 4일은 제외"),
        ],
        {"start": None, "end": None, "excluded": []},
        sources={"start": "llm", "end": "rule", "excluded": "llm"},
        origins={"start": "original-start", "end": "original-end", "excluded": "original-excluded"},
    )


def correct(old_value, old_evidence: str, new_evidence: str) -> CorrectionRelation:
    return CorrectionRelation(
        kind="replace",
        rejected_value=old_value,
        rejected_evidence=old_evidence,
        accepted_evidence=new_evidence,
    )


def interpreted(monkeypatch, data: AssistRequest, model_fields: list[FieldIntent], ctx=None):
    """Exercise the real service; the model is a deterministic existing-schema stub."""
    calls = []

    async def fake_interpret(safe_request, model_context):
        calls.append(safe_request)
        return ModelInterpretation(fields=model_fields)

    monkeypatch.setattr(vendor_assist, "interpret", fake_interpret)
    trace = {}
    result = asyncio.run(assist_service.interpret(data, ctx or context(), trace=trace))
    assert len(calls) == 1
    assert result.request_id == data.request_id
    assert result.form == data.form
    return {item.field: item for item in result.fields}, trace, calls[0]


def assert_held(item):
    assert item.action in ("keep", "uncertain"), item
    assert item.value is None, item
    # A conflict with unselected alternatives is also safely unapplied.


def assert_set(item, value, *, origin: str | None = None, source: str | None = None):
    assert (item.action, item.status, item.value) == ("set", "valid", value)
    if origin is not None:
        assert item.origin_request_id == origin
    if source is not None:
        assert item.source == source


def test_current_retention_does_not_promote_unapplied_previous_quote(monkeypatch):
    data = request(
        "quote",
        "금액은 지금 폼 값 그대로, 공사 기간은 이전 제안 유지해 주세요",
        current={"amount": 6_000_000, "days": 2},
        prior=quote_prior(),
    )
    result, trace, safe = interpreted(
        monkeypatch,
        data,
        [
            field(
                "amount",
                "keep",
                None,
                "금액은 지금 폼 값 그대로",
                intent_kind="retain_current",
                value_source="current",
            ),
            field(
                "days",
                "keep",
                None,
                "공사 기간은 이전 제안 유지",
                intent_kind="retain_previous",
                value_source="previous",
            ),
        ],
    )
    assert trace["previous_verified"]["origins"] == {
        "amount": "original-amount",
        "days": "original-days",
    }
    assert safe.previous_candidate is not None
    assert (result["amount"].action, result["amount"].status, result["amount"].intent_kind) == (
        "keep",
        "valid",
        "retain_current",
    )
    assert result["amount"].value is None  # actual 600만원 in the current form wins
    assert_set(result["days"], 3, origin="original-days", source="rule")


def test_prior_retention_preserves_origin_but_other_field_uses_current(monkeypatch):
    data = request(
        "quote",
        "금액은 이전 제안 그대로, 공사 기간은 지금 폼 값 유지해 주세요",
        current={"amount": 6_000_000, "days": 2},
        prior=quote_prior(),
    )
    result, trace, _ = interpreted(
        monkeypatch,
        data,
        [
            field(
                "amount",
                "keep",
                None,
                "금액은 이전 제안 그대로",
                intent_kind="retain_previous",
                value_source="previous",
            ),
            field(
                "days",
                "keep",
                None,
                "공사 기간은 지금 폼 값 유지",
                intent_kind="retain_current",
                value_source="current",
            ),
        ],
    )
    assert_set(result["amount"], 4_550_000, origin="original-amount", source="llm")
    assert result["days"].action == "keep"
    assert result["days"].value is None
    assert result["days"].intent_kind == "retain_current"
    assert trace["decisions"]["amount"]["origin_request_id"] == "original-amount"


def test_period_is_unchanged_when_user_says_same_as_before(monkeypatch):
    data = request(
        "period",
        "기간은 전과 같아요",
        current={"start": None, "end": None, "excluded": []},
        prior=period_prior(),
    )
    result, trace, _ = interpreted(
        monkeypatch,
        data,
        [
            field(
                "start",
                "keep",
                None,
                "기간은 전과 같아요",
                intent_kind="retain_previous",
                value_source="previous",
            ),
            field(
                "end",
                "keep",
                None,
                "기간은 전과 같아요",
                intent_kind="retain_previous",
                value_source="previous",
            ),
            field("excluded", "keep", None, "", intent_kind="no_mention"),
        ],
    )
    assert trace["previous_verified"]["baselines"] == {
        "start": None,
        "end": None,
        "excluded": [],
    }
    assert_set(result["start"], "2026-12-02", origin="original-start", source="llm")
    assert_set(result["end"], "2026-12-07", origin="original-end", source="rule")
    assert_set(result["excluded"], ["2026-12-04"], origin="original-excluded", source="llm")


def test_crew_is_unchanged_when_user_says_same_as_before(monkeypatch):
    data = request(
        "dates",
        "인원은 같아요",
        current={"days": [], "crew": 1},
        prior=dates_prior(),
    )
    result, trace, _ = interpreted(
        monkeypatch,
        data,
        [
            field(
                "crew",
                "keep",
                None,
                "인원은 같아요",
                intent_kind="retain_previous",
                value_source="previous",
            ),
            field("days", "keep", None, "", intent_kind="no_mention"),
        ],
        context(required_days=3),
    )
    assert trace["previous_verified"]["origins"]["crew"] == "original-crew"
    assert_set(result["crew"], 2, origin="original-crew", source="rule")
    assert_set(
        result["days"],
        ["2026-12-08", "2026-12-09", "2026-12-10"],
        origin="original-schedule",
        source="llm",
    )


def test_scoped_remaining_fields_retained_while_amount_is_new(monkeypatch):
    data = request(
        "quote",
        "금액은 555만원으로 바꿀게요, 나머지는 그대로",
        current={"amount": 6_000_000, "days": 2},
        prior=quote_prior(),
    )
    result, _, _ = interpreted(
        monkeypatch,
        data,
        [
            field(
                "amount",
                "set",
                5_550_000,
                "555만원",
                intent_kind="explicit_change",
                value_source="utterance",
            ),
            field(
                "days",
                "keep",
                None,
                "나머지는 그대로",
                intent_kind="retain_previous",
                value_source="previous",
            ),
        ],
    )
    assert_set(result["amount"], 5_550_000, origin=data.request_id)
    assert_set(result["days"], 3, origin="original-days", source="rule")


def test_period_partial_exclusion_correction_keeps_original_boundaries(monkeypatch):
    data = request(
        "period",
        "빼는 날은 4일 말고 5일로 고쳐 주세요",
        current={"start": None, "end": None, "excluded": []},
        prior=period_prior(),
    )
    result, trace, safe = interpreted(
        monkeypatch,
        data,
        [
            field(
                "excluded",
                "set",
                ["2026-12-05"],
                "5일로 고쳐 주세요",
                correction=correct(["2026-12-04"], "4일", "5일로 고쳐 주세요"),
                intent_kind="explicit_change",
                value_source="utterance",
            ),
            field("start", "keep", None, "", intent_kind="no_mention"),
            field("end", "keep", None, "", intent_kind="no_mention"),
        ],
    )
    assert safe.previous_candidate is not None
    assert trace["previous_verified"]["baselines"] == {
        "start": None,
        "end": None,
        "excluded": [],
    }
    assert_set(result["excluded"], ["2026-12-05"], origin=data.request_id)
    assert_set(result["start"], "2026-12-02", origin="original-start", source="llm")
    assert_set(result["end"], "2026-12-07", origin="original-end", source="rule")


def test_one_excluded_day_replacement_preserves_other_excluded_day(monkeypatch):
    old = previous(
        "12월 2일부터 12월 8일까지 가능, 12월 4일과 12월 6일 제외",
        [
            field("start", "set", "2026-12-02", "12월 2일부터"),
            field("end", "set", "2026-12-08", "12월 8일까지"),
            field(
                "excluded",
                "set",
                ["2026-12-04", "2026-12-06"],
                "12월 4일과 12월 6일 제외",
            ),
        ],
        {"start": None, "end": None, "excluded": []},
        sources={"start": "llm", "end": "rule", "excluded": "llm"},
        origins={"start": "origin-start", "end": "origin-end", "excluded": "origin-excluded"},
    )
    data = request(
        "period",
        "빼는 날짜 4일 대신 5일을 제외해 주세요",
        current={"start": None, "end": None, "excluded": []},
        prior=old,
    )
    result, trace, safe = interpreted(
        monkeypatch,
        data,
        [
            field(
                "excluded",
                "set",
                ["2026-12-05"],
                "5일을 제외해 주세요",
                correction=correct(["2026-12-04"], "4일", "5일을 제외해 주세요"),
            ),
            field("start", "keep", None, "", intent_kind="no_mention"),
            field("end", "keep", None, "", intent_kind="no_mention"),
        ],
    )
    assert safe.previous_candidate is not None
    assert trace["previous_verified"]["origins"]["excluded"] == "origin-excluded"
    excluded = result["excluded"]
    assert excluded.action == "set" and excluded.status == "valid"
    assert set(excluded.value) == {"2026-12-05", "2026-12-06"}
    assert len(excluded.value) == 2
    assert_set(result["start"], "2026-12-02", origin="origin-start", source="llm")
    assert_set(result["end"], "2026-12-08", origin="origin-end", source="rule")


def test_historical_extra_amount_correction_keeps_neighbor_and_origin(monkeypatch):
    text = "아까 12만 원이라던 추가 금액은 15만 원으로 수정할게요"
    data = request(
        "extra",
        text,
        current={"title": None, "amount": None, "vat": None},
        prior=extra_prior(),
    )
    result, trace, safe = interpreted(
        monkeypatch,
        data,
        [
            field(
                "amount",
                "set",
                150_000,
                "15만 원으로 수정할게요",
                correction=correct(120_000, "12만 원이라던", "15만 원으로 수정할게요"),
                intent_kind="explicit_change",
                value_source="utterance",
            ),
            field("title", "keep", None, "", intent_kind="no_mention"),
            field("vat", "keep", None, "", intent_kind="no_mention"),
        ],
    )
    assert safe.previous_candidate is not None
    assert trace["previous_verified"]["origins"]["amount"] == "original-extra"
    assert_set(result["amount"], 150_000, origin=data.request_id)
    assert_set(result["title"], "곰팡이 방지 처리", origin="original-title", source="rule")
    assert_set(result["vat"], "included", origin="original-vat", source="rule")


def test_explicit_historical_quote_scalar_correction_requires_verified_old(monkeypatch):
    text = "아까 455만이라고 했는데 555만 원으로 고칠게요"
    old = quote_prior()
    data = request(
        "quote",
        text,
        current={"amount": 6_000_000, "days": 2},
        prior=old,
    )
    result, trace, safe = interpreted(
        monkeypatch,
        data,
        [
            field(
                "amount",
                "set",
                5_550_000,
                "555만 원으로 고칠게요",
                intent_kind="explicit_change",
                value_source="utterance",
                correction=correct(4_550_000, "455만", "555만 원으로 고칠게요"),
            ),
            field("days", "keep", None, "", intent_kind="no_mention"),
        ],
    )
    assert safe.previous_candidate is not None
    assert trace["previous_verified"]["origins"]["amount"] == "original-amount"
    assert_set(result["amount"], 5_550_000, origin=data.request_id)
    assert_set(result["days"], 3, origin="original-days", source="rule")


def test_single_day_replace_derives_complete_schedule_and_keeps_crew(monkeypatch):
    data = request(
        "dates",
        "앞에서 말한 9일 대신 11일을 넣을게요. 인원은 그대로",
        current={"days": [], "crew": 1},
        prior=dates_prior(),
    )
    result, trace, safe = interpreted(
        monkeypatch,
        data,
        [
            field(
                "days",
                "set",
                ["2026-12-11"],
                "11일을 넣을게요",
                intent_kind="explicit_change",
                value_source="utterance",
                correction=correct(["2026-12-09"], "9일", "11일을 넣을게요"),
            ),
            field(
                "crew",
                "keep",
                None,
                "인원은 그대로",
                intent_kind="retain_previous",
                value_source="previous",
            ),
        ],
        context(required_days=3),
    )
    assert safe.previous_candidate is not None
    assert trace["previous_verified"]["origins"]["days"] == "original-schedule"
    schedule = result["days"]
    assert schedule.action == "set" and schedule.status == "valid"
    assert isinstance(schedule.value, list) and len(schedule.value) == 3
    assert set(schedule.value) == {"2026-12-08", "2026-12-10", "2026-12-11"}
    assert "2026-12-09" not in schedule.value
    assert schedule.origin_request_id == data.request_id
    assert_set(result["crew"], 2, origin="original-crew", source="rule")


@pytest.mark.parametrize("state", ["missing", "stale"], ids=["no-baseline", "edited-baseline"])
def test_missing_or_stale_day_baseline_fails_closed_with_verified_crew(monkeypatch, state):
    old = dates_prior()
    current = {"days": [], "crew": 1}
    if state == "missing":
        old = old.model_copy(update={"baselines": {"crew": 1}})
    else:
        current["days"] = ["2026-12-14", "2026-12-15", "2026-12-16"]
    data = request(
        "dates",
        "9일 대신 11일로 바꿔 주세요",
        current=current,
        prior=old,
    )
    result, trace, safe = interpreted(
        monkeypatch,
        data,
        [
            field(
                "days",
                "set",
                ["2026-12-11"],
                "11일로 바꿔 주세요",
                correction=correct(["2026-12-09"], "9일", "11일로 바꿔 주세요"),
            ),
            field("crew", "keep", None, "", intent_kind="no_mention"),
        ],
        context(required_days=3),
    )
    assert trace["previous_rejected"]["days"] == "stale_context"
    assert safe.previous_candidate is not None
    assert [item.field for item in safe.previous_candidate.fields] == ["crew"]
    assert_held(result["days"])
    assert_set(result["crew"], 2, origin="original-crew", source="rule")


@pytest.mark.parametrize(
    ("old_days", "text", "rejected", "new_evidence"),
    [
        (
            ("2026-12-08", "2026-12-09", "2026-12-10"),
            "7일 대신 11일로 바꿀게요",
            ["2026-12-07"],
            "11일로 바꿀게요",
        ),
        (
            ("2026-12-08", "2026-12-09", "2026-12-11"),
            "9일 대신 11일로 바꿀게요",
            ["2026-12-09"],
            "11일로 바꿀게요",
        ),
    ],
    ids=["old-date-not-in-verified-list", "replacement-creates-duplicate"],
)
def test_unknown_or_duplicate_day_replacement_is_held_with_crew_unaffected(
    monkeypatch,
    old_days,
    text,
    rejected,
    new_evidence,
):
    data = request(
        "dates",
        text,
        current={"days": [], "crew": 1},
        prior=dates_prior(old_days),
    )
    result, trace, safe = interpreted(
        monkeypatch,
        data,
        [
            field(
                "days",
                "set",
                ["2026-12-11"],
                new_evidence,
                correction=correct(rejected, text.split(" 대신")[0], new_evidence),
            ),
            field("crew", "keep", None, "", intent_kind="no_mention"),
        ],
        context(required_days=3),
    )
    assert safe.previous_candidate is not None
    assert trace["previous_verified"]["origins"]["days"] == "original-schedule"
    assert_held(result["days"])
    assert_set(result["crew"], 2, origin="original-crew", source="rule")


@pytest.mark.parametrize(
    ("text", "evidence", "intent_kind", "action"),
    [
        (
            "9일 대신 11일로 바꾸지 말고 그대로 두세요",
            "11일로 바꾸지 말고",
            "explicit_change",
            "set",
        ),
        (
            "9일 대신 11일일까요? 아직 미정이에요",
            "11일일까요? 아직 미정이에요",
            "ambiguous",
            "uncertain",
        ),
        (
            "9일 대신 11일은 아니고 12일로 정정할게요",
            "11일은 아니고",
            "explicit_change",
            "set",
        ),
    ],
    ids=["negated-change", "question-ambiguous", "rejected-intermediate-day"],
)
def test_ambiguous_or_negated_single_day_does_not_overwrite_verified_schedule(
    monkeypatch,
    text,
    evidence,
    intent_kind,
    action,
):
    model = (
        field(
            "days",
            "set",
            ["2026-12-11"],
            evidence,
            intent_kind=intent_kind,
            correction=correct(["2026-12-09"], "9일", evidence),
        )
        if action == "set"
        else field("days", "uncertain", None, evidence, intent_kind=intent_kind)
    )
    data = request(
        "dates",
        text,
        current={"days": [], "crew": 1},
        prior=dates_prior(),
    )
    result, _, _ = interpreted(
        monkeypatch,
        data,
        [model, field("crew", "keep", None, "", intent_kind="no_mention")],
        context(required_days=3),
    )
    assert_held(result["days"])
    assert_set(result["crew"], 2, origin="original-crew", source="rule")


def test_historical_scalar_without_verified_previous_cannot_claim_old_value(monkeypatch):
    text = "아까 455만이라고 했는데 555만 원으로 고칠게요"
    candidate = field(
        "amount",
        "set",
        5_550_000,
        "555만 원으로 고칠게요",
        correction=correct(4_550_000, "455만", "555만 원으로 고칠게요"),
    )
    for old, current in [
        (None, {"amount": 6_000_000, "days": 2}),
        (
            quote_prior().model_copy(update={"baselines": {"days": 2}}),
            {"amount": 6_000_000, "days": 2},
        ),
        (quote_prior(), {"amount": 7_000_000, "days": 2}),
    ]:
        data = request("quote", text, current=current, prior=old)
        result, trace, safe = interpreted(
            monkeypatch,
            data,
            [candidate, field("days", "keep", None, "", intent_kind="no_mention")],
        )
        assert_held(result["amount"])
        if old is not None:
            assert trace["previous_rejected"]["amount"] == "stale_context"
            assert safe.previous_candidate is not None
            assert_set(result["days"], 3, origin="original-days", source="rule")


def test_historical_scalar_rejected_value_must_match_the_same_verified_field(monkeypatch):
    old = previous(
        "견적 465만 원, 공사 3일",
        [
            field("amount", "set", 4_650_000, "465만 원"),
            field("days", "set", 3, "3일"),
        ],
        {"amount": 6_000_000, "days": 2},
        sources={"amount": "llm", "days": "rule"},
        origins={"amount": "verified-amount", "days": "verified-days"},
    )
    text = "아까 455만이라고 했는데 555만 원으로 고칠게요"
    data = request(
        "quote",
        text,
        current={"amount": 6_000_000, "days": 2},
        prior=old,
    )
    result, trace, safe = interpreted(
        monkeypatch,
        data,
        [
            field(
                "amount",
                "set",
                5_550_000,
                "555만 원으로 고칠게요",
                correction=correct(4_550_000, "455만", "555만 원으로 고칠게요"),
            ),
            field("days", "keep", None, "", intent_kind="no_mention"),
        ],
    )
    assert safe.previous_candidate is not None
    assert trace["previous_verified"]["fields"][0]["value"] == 4_650_000
    assert_held(result["amount"])
    assert_set(result["days"], 3, origin="verified-days", source="rule")
