"""업체 도우미 연속 입력: 출처·정정·보류·최종 조합의 회귀 시험."""

import asyncio
import uuid
from datetime import date

import pytest
from pydantic import ValidationError

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
from app.vendor.assist_validation import (
    merge_candidates,
    review_fields,
    verified_previous,
)


def context() -> AssistContext:
    return AssistContext(today=date(2026, 11, 23), base_month=date(2026, 12, 1))


def intent(field: str, action: str, value, evidence: str, **other) -> FieldIntent:
    return FieldIntent(field=field, action=action, value=value, evidence=evidence, **other)


def request(
    form: str,
    text: str,
    *,
    current: dict | None = None,
    previous: PreviousCandidate | None = None,
    rule: list[FieldIntent] | None = None,
) -> AssistRequest:
    return AssistRequest(
        request_id="new-request",
        form=form,
        resource_id=uuid.uuid4(),
        text=text,
        current=current or {},
        previous_candidate=previous,
        rule_fields=rule or [],
    )


def previous_quote() -> PreviousCandidate:
    return PreviousCandidate(
        request_id="second-request",
        text="견적 500만원, 공사 3일",
        fields=[
            intent("amount", "set", 5_000_000, "500만원"),
            intent("days", "set", 3, "3일"),
        ],
        sources={"amount": "llm", "days": "rule"},
        origins={"amount": "first-request", "days": "second-request"},
        baselines={"amount": None, "days": None},
    )


def fields(result):
    return {field.field: field for field in result.fields}


def model_stub(monkeypatch, values: list[FieldIntent]):
    calls: list[int] = []

    async def fake(*args, **kwargs):
        calls.append(1)
        return ModelInterpretation(fields=values)

    monkeypatch.setattr(vendor_assist, "interpret", fake)
    return calls


def test_retains_previous_by_reference_with_original_source_three_turns(monkeypatch):
    previous = previous_quote()
    req = request(
        "quote",
        "금액 그대로, 공사 기간만 4일",
        current={"amount": None, "days": None},
        previous=previous,
    )
    calls = model_stub(
        monkeypatch,
        [
            intent(
                "amount",
                "keep",
                None,
                "금액 그대로",
                intent_kind="retain_previous",
                value_source="previous",
            ),
            intent(
                "days",
                "set",
                4,
                "4일",
                intent_kind="explicit_change",
                value_source="utterance",
            ),
        ],
    )
    trace = {}
    got = asyncio.run(assist_service.interpret(req, context(), trace=trace))
    result = fields(got)
    assert len(calls) == 1
    assert result["amount"].status == "valid"
    assert result["amount"].value == 5_000_000
    assert result["amount"].source == "llm"
    assert result["amount"].origin_request_id == "first-request"
    assert result["days"].value == 4
    assert result["days"].origin_request_id == req.request_id
    assert trace["previous_verified"]["origins"]["amount"] == "first-request"
    assert trace["decisions"]["amount"]["intent"] == "retain_previous"


@pytest.mark.parametrize("use_model", [False, True])
def test_ambiguous_new_amount_never_revives_prior(monkeypatch, use_model):
    req = request(
        "quote",
        "금액은 600만 정도였나?",
        current={"amount": None, "days": None},
        previous=previous_quote(),
        rule=[intent("amount", "keep", None, "")],
    )
    calls = model_stub(
        monkeypatch,
        [
            intent(
                "amount",
                "uncertain",
                None,
                "600만 정도였나?",
                intent_kind="ambiguous",
            )
        ],
    )
    got = asyncio.run(assist_service.interpret(req, context(), use_model=use_model))
    assert len(calls) == int(use_model)
    assert fields(got)["amount"].action == "uncertain"
    assert fields(got)["amount"].value is None
    assert fields(got)["amount"].reason_code == "ambiguous_intent"


def test_failed_new_candidate_does_not_restore_old_value(monkeypatch):
    req = request(
        "quote",
        "견적 600만원쯤?",
        current={"amount": None, "days": None},
        previous=previous_quote(),
    )
    model_stub(monkeypatch, [intent("amount", "set", 999_000_000, "600만원")])
    got = asyncio.run(assist_service.interpret(req, context()))
    assert fields(got)["amount"].status == "invalid"
    assert fields(got)["amount"].reason_code == "unsupported_normalization"


def test_missing_and_stale_baselines_block_previous_field_independently(monkeypatch):
    prior = previous_quote()
    stale = request(
        "quote",
        "공사 기간만 4일",
        current={"amount": 6_000_000, "days": None},
        previous=prior,
    )
    model_stub(
        monkeypatch,
        [
            intent(
                "amount",
                "keep",
                None,
                "기간만 4일",
                intent_kind="retain_previous",
                value_source="previous",
            ),
            intent("days", "set", 4, "4일"),
        ],
    )
    got = asyncio.run(assist_service.interpret(stale, context()))
    assert fields(got)["amount"].status == "invalid"
    assert fields(got)["amount"].value is None
    assert fields(got)["days"].value == 4
    missing = prior.model_copy(update={"baselines": {}})
    bad = request(
        "quote",
        "기간만 4일",
        current={"amount": None, "days": None},
        previous=missing,
    )
    assert verified_previous(bad, context()) is None
    got = asyncio.run(assist_service.interpret(bad, context(), use_model=False))
    assert fields(got)["amount"].status == "uncertain"
    assert fields(got)["amount"].reason_code == "stale_context"


def test_verified_correction_supersedes_rejected_rule_only_with_relation():
    text = "455만원 말고 555만원으로 수정"
    req = request("quote", text)
    rule, _ = review_fields([intent("amount", "set", 4_550_000, "455만원")], "rule", req, context())
    corrected = intent(
        "amount",
        "set",
        5_550_000,
        "555만원",
        correction=CorrectionRelation(
            kind="replace",
            rejected_value=4_550_000,
            rejected_evidence="455만원",
            accepted_evidence="555만원",
        ),
    )
    model, _ = review_fields([corrected], "llm", req, context())
    assert model["amount"].correction is not None
    chosen = merge_candidates(req, context(), rule, model, None)[0]
    assert chosen.status == "valid"
    assert chosen.value == 5_550_000
    assert chosen.alternatives == []


def test_title_literal_replacement_and_without_relation_stays_conflict():
    req = request("extra", "실리콘 보수 말고 누수 보수")
    rule, _ = review_fields(
        [intent("title", "set", "실리콘 보수", "실리콘 보수")], "rule", req, context()
    )
    updated = intent(
        "title",
        "set",
        "누수 보수",
        "누수 보수",
        correction=CorrectionRelation(
            kind="replace",
            rejected_value="실리콘 보수",
            rejected_evidence="실리콘 보수",
            accepted_evidence="누수 보수",
        ),
    )
    model, _ = review_fields([updated], "llm", req, context())
    assert merge_candidates(req, context(), rule, model, None)[0].value == "누수 보수"
    plain, _ = review_fields(
        [intent("title", "set", "누수 보수", "누수 보수")], "llm", req, context()
    )
    assert merge_candidates(req, context(), rule, plain, None)[0].status == "conflict"


def test_correction_is_scoped_to_its_field_when_other_field_is_uncertain():
    req = request("quote", "455만원 말고 555만원, 기간은 미정")
    rule, _ = review_fields([intent("amount", "set", 4_550_000, "455만원")], "rule", req, context())
    model, _ = review_fields(
        [
            intent(
                "amount",
                "set",
                5_550_000,
                "555만원",
                correction=CorrectionRelation(
                    kind="replace",
                    rejected_value=4_550_000,
                    rejected_evidence="455만원",
                    accepted_evidence="555만원",
                ),
            ),
            intent("days", "uncertain", None, "기간은 미정", intent_kind="ambiguous"),
        ],
        "llm",
        req,
        context(),
    )
    final = {item.field: item for item in merge_candidates(req, context(), rule, model, None)}
    assert final["amount"].status == "valid"
    assert final["amount"].value == 5_550_000
    assert final["days"].status == "uncertain"


@pytest.mark.parametrize(
    "text,old,new",
    [
        ("455만원에서 555만원 사이로 생각 중", "455만원", "555만원"),
        ("555만원 말고 455만원", "455만원", "555만원"),
        ("455만원 말고 555만원, 다시 455만원 말고", "455만원", "555만원"),
        ("455만원 말고 555만원 아니고 655만원", "455만원", "555만원"),
        ("455만원 또는 555만원, 아직 미정", "455만원", "555만원"),
    ],
)
def test_unproved_correction_cannot_become_valid(text, old, new):
    req = request("quote", text)
    asserted = intent(
        "amount",
        "set",
        5_550_000,
        new,
        correction=CorrectionRelation(
            kind="replace",
            rejected_value=4_550_000,
            rejected_evidence=old,
            accepted_evidence=new,
        ),
    )
    reviewed, _ = review_fields([asserted], "llm", req, context())
    assert reviewed["amount"].action == "uncertain"
    assert reviewed["amount"].reason_code == "correction_unverified"


def test_model_wrong_field_retention_and_negated_keep_are_rejected():
    previous = previous_quote()
    req = request(
        "quote",
        "기간 그대로",
        current={"amount": None, "days": None},
        previous=previous,
    )
    safe = verified_previous(req, context())
    assert safe is not None
    wrong, _ = review_fields(
        [
            intent(
                "amount",
                "keep",
                None,
                "기간 그대로",
                intent_kind="retain_previous",
                value_source="previous",
            )
        ],
        "llm",
        req,
        context(),
        previous=safe,
    )
    assert wrong["amount"].status == "invalid"
    req = request(
        "quote",
        "금액 그대로 두지 말고 600만원으로",
        current={"amount": None, "days": None},
        previous=previous,
    )
    safe = verified_previous(req, context())
    shortened, _ = review_fields(
        [
            intent(
                "amount",
                "keep",
                None,
                "금액 그대로",
                intent_kind="retain_previous",
                value_source="previous",
            )
        ],
        "llm",
        req,
        context(),
        previous=safe,
    )
    assert shortened["amount"].status == "invalid"
    assert wrong["amount"].reason_code == "ambiguous_intent"
    req = request(
        "quote",
        "금액 그대로 두지 말고 600만원으로",
        current={"amount": None, "days": None},
        previous=previous,
    )
    safe = verified_previous(req, context())
    wrong, _ = review_fields(
        [
            intent(
                "amount",
                "keep",
                None,
                "금액 그대로 두지 말고",
                intent_kind="retain_previous",
                value_source="previous",
            )
        ],
        "llm",
        req,
        context(),
        previous=safe,
    )
    assert wrong["amount"].status == "invalid"


def test_similar_substrings_do_not_prove_other_field_retention_or_clear():
    prior = previous_quote()
    req = request(
        "quote",
        "인원 그대로",
        current={"amount": None, "days": None},
        previous=prior,
    )
    safe = verified_previous(req, context())
    amount, _ = review_fields(
        [
            intent(
                "amount",
                "keep",
                None,
                "인원 그대로",
                intent_kind="retain_previous",
                value_source="previous",
            )
        ],
        "llm",
        req,
        context(),
        previous=safe,
    )
    assert amount["amount"].status == "invalid"

    req = request("extra", "인원 삭제")
    cleared, _ = review_fields(
        [intent("amount", "clear", None, "인원 삭제")], "llm", req, context()
    )
    assert cleared["amount"].status == "invalid"

    req = request(
        "quote",
        "유효기간 그대로",
        current={"amount": None, "days": None},
        previous=prior,
    )
    safe = verified_previous(req, context())
    days, _ = review_fields(
        [
            intent(
                "days",
                "keep",
                None,
                "유효기간 그대로",
                intent_kind="retain_previous",
                value_source="previous",
            )
        ],
        "llm",
        req,
        context(),
        previous=safe,
    )
    assert days["days"].status == "invalid"
    req = request("quote", "유효기간 삭제")
    cleared, _ = review_fields(
        [intent("days", "clear", None, "유효기간 삭제")], "llm", req, context()
    )
    assert cleared["days"].status == "invalid"


def test_explicit_current_retention_blocks_pending_previous(monkeypatch):
    req = request(
        "quote",
        "현재 금액은 그대로, 기간만 4일",
        current={"amount": 6_000_000, "days": None},
        previous=previous_quote(),
    )
    model_stub(
        monkeypatch,
        [
            intent(
                "amount",
                "keep",
                None,
                "금액은 그대로",
                intent_kind="retain_current",
                value_source="current",
            ),
            intent("days", "set", 4, "4일"),
        ],
    )
    got = asyncio.run(assist_service.interpret(req, context()))
    assert fields(got)["amount"].action == "keep"
    assert fields(got)["amount"].status == "valid"
    assert fields(got)["days"].value == 4


def test_stale_clear_never_replaces_manual_title(monkeypatch):
    previous = PreviousCandidate(
        request_id="old",
        text="항목 삭제해 주세요",
        fields=[intent("title", "clear", None, "삭제해 주세요")],
        baselines={"title": "실리콘 보수"},
    )
    req = request(
        "extra",
        "이유만 수정해 주세요",
        current={"title": "누수 보수"},
        previous=previous,
    )
    model_stub(monkeypatch, [])
    got = asyncio.run(assist_service.interpret(req, context()))
    assert fields(got)["title"].status == "uncertain"
    assert fields(got)["title"].reason_code == "stale_context"


def test_cross_producer_individually_valid_period_fails_combined_check():
    req = request(
        "period",
        "12/20부터 12/10까지",
        current={"start": "2026-12-02", "end": "2026-12-30", "excluded": []},
    )
    rule, _ = review_fields([intent("start", "set", "2026-12-20", "12/20")], "rule", req, context())
    model, _ = review_fields([intent("end", "set", "2026-12-10", "12/10")], "llm", req, context())
    assert rule["start"].status == "valid"
    assert model["end"].status == "valid"
    merged = {field.field: field for field in merge_candidates(req, context(), rule, model, None)}
    assert merged["start"].reason_code == "combined_constraint"
    assert merged["end"].reason_code == "combined_constraint"
    assert merged["start"].status == merged["end"].status == "uncertain"


def test_invalid_period_edges_do_not_drop_independent_previous_exclusions():
    previous = PreviousCandidate(
        request_id="older",
        text="12/4는 제외해 주세요",
        fields=[intent("excluded", "set", ["2026-12-04"], "12/4")],
        baselines={"excluded": []},
    )
    req = request(
        "period",
        "12/20부터 12/10까지",
        current={"start": "2026-12-02", "end": "2026-12-30", "excluded": []},
        previous=previous,
    )
    safe = verified_previous(req, context())
    assert safe is not None
    rule, _ = review_fields([intent("start", "set", "2026-12-20", "12/20")], "rule", req, context())
    model, _ = review_fields([intent("end", "set", "2026-12-10", "12/10")], "llm", req, context())
    merged = {item.field: item for item in merge_candidates(req, context(), rule, model, safe)}
    assert merged["start"].reason_code == "combined_constraint"
    assert merged["end"].reason_code == "combined_constraint"
    assert merged["excluded"].status == "valid"
    assert merged["excluded"].value == ["2026-12-04"]


def test_interior_date_requires_explicit_boundary_exclusion():
    req = request("period", "12/2부터 12/5까지 가능")
    reviewed, _ = review_fields(
        [intent("start", "set", "2026-12-03", req.text)],
        "llm",
        req,
        context(),
    )
    assert reviewed["start"].status == "invalid"
    req = request("period", "12/2부터 12/5까지 가능, 12/2는 안 돼요")
    reviewed, _ = review_fields(
        [intent("start", "set", "2026-12-03", req.text, value_source="derived")],
        "llm",
        req,
        context(),
    )
    assert reviewed["start"].status == "valid"


def test_empty_excluded_extraction_is_not_implicit_deletion():
    req = request("period", "12/2부터 12/5까지 가능해요")
    reviewed, _ = review_fields([intent("excluded", "set", [], req.text)], "llm", req, context())
    assert reviewed["excluded"].status == "invalid"


def test_korean_crew_and_abbreviated_calendar_use_shared_normalizer():
    crew = request("dates", "작업자 네 명")
    reviewed, _ = review_fields([intent("crew", "set", 4, "네 명")], "llm", crew, context())
    assert reviewed["crew"].status == "valid"
    reviewed, _ = review_fields(
        [intent("crew", "set", 4, "두세 명")],
        "llm",
        request("dates", "작업자 두세 명"),
        context(),
    )
    assert reviewed["crew"].status == "invalid"
    dates = request("dates", "12월7일부터9일까지 공사합니다")
    reviewed, _ = review_fields(
        [
            intent(
                "days",
                "set",
                [
                    "2026-12-07",
                    "2026-12-08",
                    "2026-12-09",
                ],
                "12월7일부터9일까지",
            )
        ],
        "llm",
        dates,
        AssistContext(today=date(2026, 11, 23), base_month=date(2026, 11, 1)),
    )
    assert reviewed["days"].status == "valid"
    listed = request("dates", "12/11,12,13일에 공사합니다")
    reviewed, _ = review_fields(
        [
            intent(
                "days",
                "set",
                [
                    "2026-12-11",
                    "2026-12-12",
                    "2026-12-13",
                ],
                "12/11,12,13일",
            )
        ],
        "llm",
        listed,
        context(),
    )
    assert reviewed["days"].status == "valid"


def test_rule_only_uses_identical_validation_and_trace_and_never_calls_model(monkeypatch):
    async def forbidden(*args, **kwargs):
        raise AssertionError("paid model must not be invoked")

    monkeypatch.setattr(vendor_assist, "interpret", forbidden)
    req = request(
        "quote",
        "견적 555만원",
        rule=[intent("amount", "set", 5_550_000, "555만원")],
    )
    trace = {}
    got = asyncio.run(assist_service.interpret(req, context(), use_model=False, trace=trace))
    assert got.source == "rule"
    assert fields(got)["amount"].value == 5_550_000
    assert set(trace) >= {
        "rule_raw",
        "model_raw",
        "previous_raw",
        "previous_verified",
        "previous_rejected",
        "rule_reviewed",
        "model_reviewed",
        "merged",
        "final",
        "decisions",
    }
    assert trace["model_raw"] is None and trace["model_reviewed"] is None
    assert trace["decisions"]["amount"]["choice"] == "valid"


def test_schema_rejects_baseline_names_outside_previous_fields():
    with pytest.raises(ValidationError):
        PreviousCandidate.model_validate(
            previous_quote().model_dump() | {"baselines": {"foreign": "tampered"}}
        )


def test_previous_bad_evidence_reason_is_not_mislabeled_stale(monkeypatch):
    prior = previous_quote().model_copy(
        update={
            "fields": [
                intent("amount", "set", 9_000_000, "500만원"),
                intent("days", "set", 3, "3일"),
            ]
        }
    )
    req = request(
        "quote",
        "유효기간은 12/20까지",
        current={"amount": None, "days": None},
        previous=prior,
    )
    model_stub(monkeypatch, [])
    trace = {}
    got = asyncio.run(assist_service.interpret(req, context(), trace=trace))
    assert trace["previous_rejected"]["amount"] == "unsupported_normalization"
    assert trace["decisions"]["amount"]["previous"] == "unsupported_normalization"
    assert fields(got)["amount"].reason_code == "unsupported_normalization"
    assert fields(got)["days"].status == "valid"


def test_clear_positive_and_negated_evidence():
    req = request("extra", "항목을 삭제해 주세요")
    got, _ = review_fields([intent("title", "clear", None, "삭제해 주세요")], "llm", req, context())
    assert got["title"].status == "valid"
    req = request("extra", "항목은 삭제하지 마세요")
    got, _ = review_fields(
        [intent("title", "clear", None, "삭제하지 마세요")], "llm", req, context()
    )
    assert got["title"].status == "invalid"


@pytest.mark.parametrize("suffix", [" 명", "\t일", "\n㎡"])
def test_quantity_with_whitespace_unit_cannot_become_amount(suffix):
    from app.vendor.assist_validation import _amount_mentions

    assert _amount_mentions("견적 3" + suffix) == set()


def test_rule_only_model_timeout_does_not_revive_previous_on_uncertain_input(monkeypatch):
    async def timeout(*args, **kwargs):
        raise TimeoutError("no provider response")

    monkeypatch.setattr(vendor_assist, "interpret", timeout)
    req = request(
        "quote",
        "금액은 600만 정도였나?",
        current={"amount": None, "days": None},
        previous=previous_quote(),
        rule=[intent("amount", "keep", None, "")],
    )
    got = asyncio.run(assist_service.interpret(req, context()))
    assert got.source == "rule"
    assert fields(got)["amount"].status == "uncertain"
    assert fields(got)["amount"].value is None
    assert got.warnings
