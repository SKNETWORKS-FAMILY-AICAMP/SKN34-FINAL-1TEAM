"""#179 추가 회귀: 명시적 유지 · 과거 발화 정정 · 날짜 목록 1일 교체."""

import asyncio
import uuid
from datetime import date

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
from app.vendor.assist_validation import review_previous


def ctx(**options) -> AssistContext:
    return AssistContext(today=date(2026, 11, 23), base_month=date(2026, 12, 1), **options)


def field(name: str, action: str, value, evidence: str, **extra) -> FieldIntent:
    return FieldIntent(field=name, action=action, value=value, evidence=evidence, **extra)


def request(form: str, text: str, *, current=None, previous=None, rule=None) -> AssistRequest:
    return AssistRequest(
        request_id="new-request",
        form=form,
        resource_id=uuid.uuid4(),
        text=text,
        current=current or {},
        previous_candidate=previous,
        rule_fields=rule or [],
    )


def stub(monkeypatch, fields: list[FieldIntent]) -> None:
    async def fake(*_args, **_kwargs):
        return ModelInterpretation(fields=fields)

    monkeypatch.setattr(vendor_assist, "interpret", fake)


def interpret(data: AssistRequest, context: AssistContext):
    trace: dict = {}
    result = asyncio.run(assist_service.interpret(data, context, trace=trace))
    return {item.field: item for item in result.fields}, trace


def quote_previous(amount=4_550_000, days=3) -> PreviousCandidate:
    amount_label = f"{amount // 10_000}만원"
    return PreviousCandidate(
        request_id="old-quote",
        text=f"견적 {amount_label}, 공사 {days}일",
        fields=[
            field("amount", "set", amount, amount_label),
            field("days", "set", days, f"{days}일"),
        ],
        sources={"amount": "llm", "days": "rule"},
        origins={"amount": "origin-amount", "days": "origin-days"},
        baselines={"amount": None, "days": None},
    )


def period_previous() -> PreviousCandidate:
    return PreviousCandidate(
        request_id="old-period",
        text="12월 7일부터 12월 10일까지 가능",
        fields=[
            field("start", "set", "2026-12-07", "12월 7일"),
            field("end", "set", "2026-12-10", "12월 10일"),
        ],
        sources={"start": "llm", "end": "rule"},
        origins={"start": "origin-start", "end": "origin-end"},
        baselines={"start": None, "end": None},
    )


def dates_previous() -> PreviousCandidate:
    return PreviousCandidate(
        request_id="old-dates",
        text="12/8부터 12/10까지 공사, 2명",
        fields=[
            field(
                "days",
                "set",
                ["2026-12-08", "2026-12-09", "2026-12-10"],
                "12/8부터 12/10까지",
            ),
            field("crew", "set", 2, "2명"),
        ],
        sources={"days": "llm", "crew": "rule"},
        origins={"days": "origin-days", "crew": "origin-crew"},
        baselines={"days": [], "crew": None},
    )


def exclusions_previous() -> PreviousCandidate:
    return PreviousCandidate(
        request_id="old-exclusions",
        text="12/9, 12/10은 제외해 주세요",
        fields=[
            field(
                "excluded",
                "set",
                ["2026-12-09", "2026-12-10"],
                "12/9, 12/10",
            )
        ],
        sources={"excluded": "rule"},
        origins={"excluded": "origin-excluded"},
        baselines={"excluded": []},
    )


@pytest.mark.parametrize(
    ("form", "text", "name", "evidence", "expected", "origin", "source"),
    [
        ("quote", "기간은 전과 같아요", "days", "기간은 전과 같아요", 3, "origin-days", "rule"),
        ("quote", "기간은 전과 같아요", "days", "전과 같아요", 3, "origin-days", "rule"),
        (
            "period",
            "기간은 전과 같아요",
            "start",
            "기간은 전과 같아요",
            "2026-12-07",
            "origin-start",
            "llm",
        ),
        ("period", "기간은 전과 같아요", "end", "전과 같아요", "2026-12-10", "origin-end", "rule"),
        ("dates", "인원은 같아요", "crew", "인원은 같아요", 2, "origin-crew", "rule"),
        ("dates", "인원은 같아요", "crew", "같아요", 2, "origin-crew", "rule"),
    ],
)
def test_affirmative_same_field_previous_retention(
    monkeypatch, form, text, name, evidence, expected, origin, source
):
    previous = (
        quote_previous()
        if form == "quote"
        else period_previous()
        if form == "period"
        else dates_previous()
    )
    current = (
        {"amount": None, "days": None}
        if form == "quote"
        else {"start": None, "end": None, "excluded": []}
        if form == "period"
        else {"days": [], "crew": None}
    )
    stub(
        monkeypatch,
        [
            field(
                name,
                "keep",
                None,
                evidence,
                intent_kind="retain_previous",
                value_source="previous",
            )
        ],
    )
    response, _ = interpret(request(form, text, current=current, previous=previous), ctx())
    assert (response[name].status, response[name].value) == ("valid", expected)
    assert (response[name].origin_request_id, response[name].source) == (origin, source)
    assert response[name].value_source == "previous"


def test_rest_scope_retains_only_unmodified_fields(monkeypatch):
    data = request(
        "quote",
        "공사 기간은 4일로 바꾸고, 나머지는 그대로",
        current={"amount": None, "days": None},
        previous=quote_previous(),
    )
    stub(
        monkeypatch,
        [
            field(
                "amount",
                "keep",
                None,
                "나머지는 그대로",
                intent_kind="retain_previous",
                value_source="previous",
            ),
            field("days", "set", 4, "4일"),
        ],
    )
    result, _ = interpret(data, ctx())
    assert (result["amount"].value, result["amount"].origin_request_id) == (
        4_550_000,
        "origin-amount",
    )
    assert result["days"].value == 4


def test_rest_scope_one_period_boundary_retains_untouched_other(monkeypatch):
    data = request(
        "period",
        "시작일만 12월 8일로 바꾸고, 나머지는 그대로",
        current={"start": None, "end": None, "excluded": []},
        previous=period_previous(),
    )
    stub(
        monkeypatch,
        [
            field("start", "set", "2026-12-08", "12월 8일"),
            field(
                "end",
                "keep",
                None,
                "나머지는 그대로",
                intent_kind="retain_previous",
                value_source="previous",
            ),
        ],
    )
    result, _ = interpret(data, ctx())
    assert (result["start"].status, result["start"].value) == ("valid", "2026-12-08")
    assert (result["end"].status, result["end"].value) == ("valid", "2026-12-10")
    assert result["end"].origin_request_id == "origin-end"


@pytest.mark.parametrize(
    "text",
    [
        "인원은 같아요?",
        "인원은 같지 않아요",
        "인원은 같은 게 아니에요",
        "인원은 같은지 모르겠어요",
    ],
)
def test_uncertain_or_negated_same_field_does_not_retain(monkeypatch, text):
    stub(
        monkeypatch,
        [field("crew", "keep", None, text, intent_kind="retain_previous", value_source="previous")],
    )
    outcome, _ = interpret(
        request("dates", text, current={"days": [], "crew": None}, previous=dates_previous()),
        ctx(),
    )
    assert not (outcome["crew"].status == "valid" and outcome["crew"].action == "set")


def test_retention_distinguishes_current_from_stale_previous(monkeypatch):
    old = dates_previous()
    data = request(
        "dates",
        "현재 인원은 같아요",
        current={"days": [], "crew": 4},  # old.baselines['crew'] was None
        previous=old,
    )
    stub(
        monkeypatch,
        [
            field(
                "crew",
                "keep",
                None,
                "인원은 같아요",
                intent_kind="retain_current",
                value_source="current",
            )
        ],
    )
    result, trace = interpret(data, ctx())
    assert (result["crew"].status, result["crew"].action) == ("valid", "keep")
    assert result["crew"].value_source == "current"
    assert trace["previous_rejected"]["crew"] == "stale_context"


@pytest.mark.parametrize(
    ("text", "old_amount", "new_amount", "old_evidence", "new_evidence"),
    [
        (
            "아까 455만이라고 했는데 555만 원으로 고칠게요",
            4_550_000,
            5_550_000,
            "455만",
            "555만 원",
        ),
        (
            "지난번에 320만원이라고 적었는데 420만원으로 수정할게요",
            3_200_000,
            4_200_000,
            "320만원이라고 적었는데",
            "420만원",
        ),
        (
            "455만 원이라고 했는데 555만 원으로 고칠게요",
            4_550_000,
            5_550_000,
            "455만 원이라고 했는데",
            "555만 원",
        ),
        (
            "12만 원이라던 추가금을 15만 원으로 수정할게요",
            120_000,
            150_000,
            "12만 원이라던 추가금을",
            "15만 원",
        ),
    ],
)
def test_historical_amount_explicitly_corrects_verified_same_field(
    monkeypatch, text, old_amount, new_amount, old_evidence, new_evidence
):
    form = "extra" if "추가금" in text else "quote"
    prev = (
        quote_previous(old_amount)
        if form == "quote"
        else PreviousCandidate(
            request_id="prior-extra",
            text=f"추가 금액 {old_amount // 10_000}만원",
            fields=[field("amount", "set", old_amount, f"{old_amount // 10_000}만원")],
            sources={"amount": "llm"},
            origins={"amount": "origin-extra-amount"},
            baselines={"amount": None},
        )
    )
    current = {"amount": None, "days": None} if form == "quote" else {"amount": None}
    relation = CorrectionRelation(
        kind="replace",
        rejected_value=old_amount,
        rejected_evidence=old_evidence,
        accepted_evidence=new_evidence,
    )
    stub(
        monkeypatch,
        [field("amount", "set", new_amount, new_evidence, correction=relation)],
    )
    result, trace = interpret(request(form, text, current=current, previous=prev), ctx())
    assert (result["amount"].status, result["amount"].action, result["amount"].value) == (
        "valid",
        "set",
        new_amount,
    )
    assert result["amount"].origin_request_id == "new-request"
    assert result["amount"].correction is not None
    assert trace["previous_verified"] is not None


def test_reported_past_crew_correction(monkeypatch):
    prev = dates_previous().model_copy(
        update={
            "text": "작업자 세 명",
            "fields": [
                field("crew", "set", 3, "세 명"),
            ],
            "sources": {"crew": "llm"},
            "origins": {"crew": "origin-crew"},
            "baselines": {"crew": None},
        }
    )
    text = "세 명이라 했는데 네 명으로 정정해요"
    stub(
        monkeypatch,
        [
            field(
                "crew",
                "set",
                4,
                "네 명",
                correction=CorrectionRelation(
                    kind="replace",
                    rejected_value=3,
                    rejected_evidence="세 명이라 했는데",
                    accepted_evidence="네 명",
                ),
            )
        ],
    )
    result, _ = interpret(request("dates", text, current={"crew": None}, previous=prev), ctx())
    assert (result["crew"].status, result["crew"].value) == ("valid", 4)


@pytest.mark.parametrize(
    ("text", "reference"),
    [
        ("아까 455만이라고 했는데 555만 원으로 고칠게요", 5_000_000),
        ("아까 455만이라고 했는데 555만 원으로 고칠까요?", 4_550_000),
        ("아까 455만이라고 했는데 555만 원 또는 655만 원으로", 4_550_000),
        ("아까 455만이라고 했는데 555만 원으로 고치지 마세요", 4_550_000),
    ],
)
def test_historical_relation_with_wrong_old_or_uncertain_new_is_held(monkeypatch, text, reference):
    stub(
        monkeypatch,
        [
            field(
                "amount",
                "set",
                5_550_000,
                "555만 원",
                correction=CorrectionRelation(
                    kind="replace",
                    rejected_value=4_550_000,
                    rejected_evidence="455만",
                    accepted_evidence="555만 원",
                ),
            )
        ],
    )
    result, _ = interpret(
        request(
            "quote",
            text,
            current={"amount": None, "days": None},
            previous=quote_previous(reference),
        ),
        ctx(),
    )
    assert not (result["amount"].status == "valid" and result["amount"].action == "set")


def test_stale_previous_baseline_never_proves_historical_relation(monkeypatch):
    text = "아까 455만이라고 했는데 555만 원으로 고칠게요"
    stub(
        monkeypatch,
        [
            field(
                "amount",
                "set",
                5_550_000,
                "555만 원",
                correction=CorrectionRelation(
                    kind="replace",
                    rejected_value=4_550_000,
                    rejected_evidence="455만",
                    accepted_evidence="555만 원",
                ),
            )
        ],
    )
    result, trace = interpret(
        request(
            "quote",
            text,
            current={"amount": 5_000_000, "days": None},
            previous=quote_previous(),
        ),
        ctx(),
    )
    assert trace["previous_rejected"]["amount"] == "stale_context"
    assert not (result["amount"].status == "valid" and result["amount"].action == "set")


@pytest.mark.parametrize(
    "new_value",
    [
        ["2026-12-11"],
        ["2026-12-08", "2026-12-11", "2026-12-10"],
    ],
)
def test_verified_dates_replace_one_day_without_losing_other_elements(monkeypatch, new_value):
    text = "9일 대신 11일"
    stub(
        monkeypatch,
        [
            field(
                "days",
                "set",
                new_value,
                "11일",
                value_source="derived",
                correction=CorrectionRelation(
                    kind="replace",
                    rejected_value="2026-12-09",
                    rejected_evidence="9일",
                    accepted_evidence="11일",
                ),
            )
        ],
    )
    result, _ = interpret(
        request(
            "dates",
            text,
            current={"days": [], "crew": None},
            previous=dates_previous(),
        ),
        ctx(required_days=3),
    )
    assert (result["days"].status, result["days"].action) == ("valid", "set")
    assert set(result["days"].value) == {"2026-12-08", "2026-12-10", "2026-12-11"}
    assert result["days"].value_source == "derived"
    assert result["days"].origin_request_id == "new-request"


def test_verified_excluded_replaces_single_day_inside_existing_period(monkeypatch):
    text = "제외할 날 9일 대신 11일"
    stub(
        monkeypatch,
        [
            field(
                "excluded",
                "set",
                ["2026-12-11"],
                "11일",
                value_source="derived",
                correction=CorrectionRelation(
                    kind="replace",
                    rejected_value="2026-12-09",
                    rejected_evidence="9일",
                    accepted_evidence="11일",
                ),
            )
        ],
    )
    result, _ = interpret(
        request(
            "period",
            text,
            current={"start": "2026-12-07", "end": "2026-12-15", "excluded": []},
            previous=exclusions_previous(),
        ),
        ctx(),
    )
    assert (result["excluded"].status, set(result["excluded"].value)) == (
        "valid",
        {"2026-12-10", "2026-12-11"},
    )


def test_derived_excluded_list_is_reverified_on_next_message(monkeypatch):
    original = exclusions_previous()
    edit = "제외할 날 9일 대신 11일"
    stub(
        monkeypatch,
        [
            field(
                "excluded",
                "set",
                ["2026-12-11"],
                "11일",
                value_source="derived",
                correction=CorrectionRelation(
                    kind="replace",
                    rejected_value="2026-12-09",
                    rejected_evidence="9일",
                    accepted_evidence="11일",
                ),
            )
        ],
    )
    current = {"start": "2026-12-07", "end": "2026-12-15", "excluded": []}
    second, _ = interpret(request("period", edit, current=current, previous=original), ctx())
    assert second["excluded"].status == "valid"
    chosen = second["excluded"]
    pending = PreviousCandidate(
        request_id="pending-excluded",
        text=f"{original.text}\n{edit}",
        fields=[
            field(
                "excluded",
                "set",
                chosen.value,
                chosen.evidence,
                value_source=chosen.value_source,
                correction=chosen.correction,
            )
        ],
        sources={"excluded": chosen.source},
        origins={"excluded": chosen.origin_request_id},
        baselines={"excluded": []},
    )
    third = request("period", "빼는 날은 그대로", current=current, previous=pending)
    verified, rejected = review_previous(third, ctx())
    assert verified is not None, rejected
    stub(
        monkeypatch,
        [
            field(
                "excluded",
                "keep",
                None,
                "빼는 날은 그대로",
                intent_kind="retain_previous",
                value_source="previous",
            )
        ],
    )
    final, _ = interpret(third, ctx())
    assert (final["excluded"].status, final["excluded"].value) == ("valid", chosen.value)
    assert final["excluded"].origin_request_id == chosen.origin_request_id


@pytest.mark.parametrize(
    ("text", "new_day", "expected_allowed"),
    [
        ("9일 대신 10일", "2026-12-10", False),  # duplicate
        ("8일 대신 11일", "2026-12-11", False),  # rejected_value는 9일로 잘못 연결됨
        ("9일 대신 15일", "2026-12-15", True),
        ("9일 또는 11일", "2026-12-11", False),
        ("9일 대신 11일일까요?", "2026-12-11", False),
    ],
)
def test_list_replacement_requires_unambiguous_unique_day(
    monkeypatch, text, new_day, expected_allowed
):
    stub(
        monkeypatch,
        [
            field(
                "days",
                "set",
                [new_day],
                new_day[8:].lstrip("0") + "일",
                value_source="derived",
                correction=CorrectionRelation(
                    kind="replace",
                    rejected_value="2026-12-09",
                    rejected_evidence="9일",
                    accepted_evidence=new_day[8:].lstrip("0") + "일",
                ),
            )
        ],
    )
    result, _ = interpret(
        request("dates", text, current={"days": []}, previous=dates_previous()),
        ctx(required_days=3),
    )
    assert (result["days"].status == "valid") is expected_allowed


def test_single_day_edit_honors_required_days_and_holiday(monkeypatch):
    text = "9일 대신 11일"
    stub(
        monkeypatch,
        [
            field(
                "days",
                "set",
                ["2026-12-11"],
                "11일",
                value_source="derived",
                correction=CorrectionRelation(
                    kind="replace",
                    rejected_value="2026-12-09",
                    rejected_evidence="9일",
                    accepted_evidence="11일",
                ),
            )
        ],
    )
    for kwargs in ({"required_days": 2}, {"required_days": 3, "holidays": [date(2026, 12, 11)]}):
        result, _ = interpret(
            request("dates", text, current={"days": []}, previous=dates_previous()), ctx(**kwargs)
        )
        assert result["days"].status != "valid"


def test_list_replacement_stale_baseline_holds_and_does_not_touch_current(monkeypatch):
    text = "9일 대신 11일"
    stub(
        monkeypatch,
        [
            field(
                "days",
                "set",
                ["2026-12-11"],
                "11일",
                value_source="derived",
                correction=CorrectionRelation(
                    kind="replace",
                    rejected_value="2026-12-09",
                    rejected_evidence="9일",
                    accepted_evidence="11일",
                ),
            )
        ],
    )
    result, trace = interpret(
        request("dates", text, current={"days": ["2026-12-08"]}, previous=dates_previous()),
        ctx(required_days=3),
    )
    assert trace["previous_rejected"]["days"] == "stale_context"
    assert result["days"].status != "valid"


def test_date_edit_derived_proof_survives_a_third_message(monkeypatch):
    previous = dates_previous()
    second_text = "9일 대신 11일"
    stub(
        monkeypatch,
        [
            field(
                "days",
                "set",
                ["2026-12-11"],
                "11일",
                value_source="derived",
                correction=CorrectionRelation(
                    kind="replace",
                    rejected_value="2026-12-09",
                    rejected_evidence="9일",
                    accepted_evidence="11일",
                ),
            )
        ],
    )
    second, _ = interpret(
        request("dates", second_text, current={"days": []}, previous=previous),
        ctx(required_days=3),
    )
    assert second["days"].status == "valid"
    carried = second["days"]
    pending = PreviousCandidate(
        request_id="new-request",
        text=previous.text + "\n" + second_text,
        fields=[
            field(
                "days",
                carried.action,
                carried.value,
                carried.evidence,
                value_source=carried.value_source,
                correction=carried.correction,
            )
        ],
        sources={"days": carried.source},
        origins={"days": carried.origin_request_id},
        baselines={"days": []},
    )
    third_data = request("dates", "공사 날짜는 그대로", current={"days": []}, previous=pending)
    verified, rejected = review_previous(third_data, ctx(required_days=3))
    assert verified is not None, rejected
    stub(
        monkeypatch,
        [
            field(
                "days",
                "keep",
                None,
                "날짜는 그대로",
                intent_kind="retain_previous",
                value_source="previous",
            )
        ],
    )
    third, _ = interpret(third_data, ctx(required_days=3))
    assert (third["days"].status, third["days"].value) == ("valid", carried.value)
    assert third["days"].origin_request_id == carried.origin_request_id


@pytest.mark.parametrize(
    "later",
    ["공사 날짜 전부 지워 주세요", "공사 날짜는 12월 15일로 바꿀까요?", "일정을 다시 수정할게요"],
)
def test_old_derived_list_is_not_revived_after_later_change_or_clear(monkeypatch, later):
    original = dates_previous()
    second_text = "9일 대신 11일"
    stub(
        monkeypatch,
        [
            field(
                "days",
                "set",
                ["2026-12-11"],
                "11일",
                value_source="derived",
                correction=CorrectionRelation(
                    kind="replace",
                    rejected_value="2026-12-09",
                    rejected_evidence="9일",
                    accepted_evidence="11일",
                ),
            )
        ],
    )
    result, _ = interpret(
        request("dates", second_text, current={"days": []}, previous=original),
        ctx(required_days=3),
    )
    pending = PreviousCandidate(
        request_id="old-candidate",
        text=f"{original.text}\n{second_text}\n{later}",
        fields=[
            field(
                "days",
                "set",
                result["days"].value,
                result["days"].evidence,
                value_source=result["days"].value_source,
                correction=result["days"].correction,
            )
        ],
        baselines={"days": []},
        sources={"days": "llm"},
        origins={"days": "old-candidate"},
    )
    checked, reasons = review_previous(
        request("dates", "날짜는 그대로", current={"days": []}, previous=pending),
        ctx(required_days=3),
    )
    assert checked is None and reasons["days"] == "reference_missing"


def test_verified_date_edits_chain_through_four_messages(monkeypatch):
    original = dates_previous()
    second_text = "9일 대신 11일"
    stub(
        monkeypatch,
        [
            field(
                "days",
                "set",
                ["2026-12-11"],
                "11일",
                value_source="derived",
                correction=CorrectionRelation(
                    kind="replace",
                    rejected_value="2026-12-09",
                    rejected_evidence="9일",
                    accepted_evidence="11일",
                ),
            )
        ],
    )
    second, _ = interpret(
        request("dates", second_text, current={"days": []}, previous=original),
        ctx(required_days=3),
    )
    pending2 = PreviousCandidate(
        request_id="second",
        text=f"{original.text}\n{second_text}",
        fields=[
            field(
                "days",
                "set",
                second["days"].value,
                second["days"].evidence,
                value_source="derived",
                correction=second["days"].correction,
            )
        ],
        baselines={"days": []},
        sources={"days": "llm"},
        origins={"days": "second"},
    )
    third_text = "10일 대신 12일"
    stub(
        monkeypatch,
        [
            field(
                "days",
                "set",
                ["2026-12-12"],
                "12일",
                value_source="derived",
                correction=CorrectionRelation(
                    kind="replace",
                    rejected_value="2026-12-10",
                    rejected_evidence="10일",
                    accepted_evidence="12일",
                ),
            )
        ],
    )
    third, _ = interpret(
        request("dates", third_text, current={"days": []}, previous=pending2),
        ctx(required_days=3),
    )
    assert third["days"].status == "valid"
    assert set(third["days"].value) == {"2026-12-08", "2026-12-11", "2026-12-12"}
    pending3 = PreviousCandidate(
        request_id="third",
        text=f"{original.text}\n{second_text}\n{third_text}",
        fields=[
            field(
                "days",
                "set",
                third["days"].value,
                third["days"].evidence,
                value_source="derived",
                correction=third["days"].correction,
            )
        ],
        baselines={"days": []},
        sources={"days": third["days"].source},
        origins={"days": "third"},
    )
    final = request("dates", "공사 날짜는 그대로", current={"days": []}, previous=pending3)
    checked, reasons = review_previous(final, ctx(required_days=3))
    assert checked is not None, reasons
    stub(
        monkeypatch,
        [
            field(
                "days",
                "keep",
                None,
                "날짜는 그대로",
                intent_kind="retain_previous",
                value_source="previous",
            )
        ],
    )
    last, _ = interpret(final, ctx(required_days=3))
    assert (last["days"].status, last["days"].value) == ("valid", third["days"].value)
    assert last["days"].origin_request_id == "third"
    assert last["days"].value_source == "derived"
    assert last["days"].correction == third["days"].correction
