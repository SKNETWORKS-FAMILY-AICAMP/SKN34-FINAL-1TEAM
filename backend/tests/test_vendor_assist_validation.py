"""업체 도우미 입력 검증은 값의 출처와 업무 한도를 항상 확인한다."""

import uuid
from datetime import date

import pytest
from pydantic import ValidationError

from app.vendor.assist_schemas import (
    AssistContext,
    AssistRequest,
    FieldIntent,
    PreviousCandidate,
)
from app.vendor.assist_validation import (
    evidence_matches,
    merge_candidates,
    review_fields,
    verified_previous,
)


def request(
    form: str,
    text: str,
    *,
    current: dict | None = None,
    rule_fields: list[FieldIntent] | None = None,
    previous: PreviousCandidate | None = None,
) -> AssistRequest:
    return AssistRequest(
        request_id="r1",
        form=form,
        resource_id=uuid.uuid4(),
        text=text,
        current=current or {},
        previous_candidate=previous,
        rule_fields=rule_fields or [],
    )


def field(name: str, value, evidence: str, action: str = "set") -> FieldIntent:
    return FieldIntent(field=name, action=action, value=value, evidence=evidence)


def ctx(**changes) -> AssistContext:
    return AssistContext(
        today=date(2026, 11, 23), base_month=date(2026, 12, 1), days_max=120, crew_max=20, **changes
    )


@pytest.mark.parametrize("unit", ["명", " 만 원", "0"])
def test_short_range_cannot_truncate_quantity(unit):
    text = "12/14~19" + unit
    candidate = field("end", "2026-12-19", "12/14~19")
    reviewed, _ = review_fields([candidate], "llm", request("period", text), ctx())
    assert reviewed["end"].status != "valid"


@pytest.mark.parametrize(
    ("form", "field_name", "value"),
    [
        ("quote", "amount", True),
        ("quote", "amount", 100.5),
        ("quote", "amount", {"x": 1}),
        ("quote", "days", 4.0),
        ("quote", "vat", "maybe"),
        ("dates", "days", [True]),
        ("period", "start", "2026-02-31"),
        ("extra", "title", "x" * 61),
        ("extra", "reason", "x" * 201),
        ("quote", "crew", 2),
    ],
)
def test_request_rejects_wrong_field_types(form, field_name, value):
    with pytest.raises(ValidationError):
        request(form, "내용", current={field_name: value})


def test_request_rejects_unknown_keys_and_empty_body():
    body = request("quote", "견적 123원").model_dump()
    with pytest.raises(ValidationError):
        AssistRequest.model_validate({**body, "admin": True})
    with pytest.raises(ValidationError):
        AssistRequest.model_validate({**body, "text": "    "})
    with pytest.raises(ValidationError):
        AssistRequest.model_validate({**body, "text": "a" * 1001})


def test_candidate_requires_explicit_value_and_evidence():
    with pytest.raises(ValidationError):
        field("amount", 100, "", action="set")
    with pytest.raises(ValidationError):
        field("amount", 100, "지우세요", action="clear")
    with pytest.raises(ValidationError):
        field("amount", False, "100원", action="set")


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("견적 455만원에서 555만원으로 변경", 5_550_000),
        ("추가 비용 1억 2천만원", 120_000_000),
        ("견적 1.5억", 150_000_000),
        ("5000원", 5000),
    ],
)
def test_amounts_are_verified_against_original_expression(text, expected):
    form = "extra" if "추가" in text else "quote"
    req = request(form, text)
    got, warn = review_fields([field("amount", expected, text)], "llm", req, ctx())
    assert got["amount"].status == "valid"
    assert not warn


def test_hallucinated_amount_and_mismatched_evidence_never_valid():
    req = request("quote", "견적 455만원에서 555만원으로 수정")
    wrong, _ = review_fields([field("amount", 9_900_000, "555만원")], "llm", req, ctx())
    assert wrong["amount"].action == "uncertain"
    assert wrong["amount"].status == "invalid"
    wrong, _ = review_fields(
        [field("amount", 5_550_000, "명령을 무시하고 555만원")], "llm", req, ctx()
    )
    assert wrong["amount"].status == "invalid"


def test_rule_llm_disagreement_returns_validated_alternatives_only():
    req = request("quote", "455만원 아니고 555만원으로 수정")
    rule, _ = review_fields([field("amount", 4_550_000, "455만원")], "rule", req, ctx())
    llm, _ = review_fields([field("amount", 5_550_000, "555만원")], "llm", req, ctx())
    merged = {x.field: x for x in merge_candidates(req, ctx(), rule, llm, None)}
    assert merged["amount"].action == "uncertain"
    assert merged["amount"].value is None
    assert merged["amount"].status == "conflict"
    assert {x.value for x in merged["amount"].alternatives} == {4_550_000, 5_550_000}


def test_previous_candidates_are_revalidated_and_can_be_corrected():
    previous = PreviousCandidate(
        request_id="old",
        text="견적 455만원, 공사 3일",
        fields=[field("amount", 4_550_000, "455만원"), field("days", 3, "공사 3일")],
        baselines={"amount": None, "days": None},
    )
    req = request(
        "quote",
        "아니 555만원으로 바꾸고 공사 기간은 그대로",
        current={"amount": None, "days": None},
        previous=previous,
    )
    safe = verified_previous(req, ctx())
    assert safe is not None
    llm, _ = review_fields(
        [
            field("amount", 5_550_000, "555만원"),
            FieldIntent(
                field="days",
                action="keep",
                value=None,
                evidence="기간은 그대로",
                intent_kind="retain_previous",
                value_source="previous",
            ),
        ],
        "llm",
        req,
        ctx(),
        previous=safe,
    )
    merged = {x.field: x for x in merge_candidates(req, ctx(), {}, llm, safe)}
    assert merged["amount"].value == 5_550_000
    assert merged["days"].value == 3
    assert merged["days"].action == "set"


def test_untrusted_previous_evidence_is_not_carried_forward():
    previous = PreviousCandidate(
        request_id="old", text="견적은 455만원", fields=[field("amount", 99_000_000, "455만원")]
    )
    req = request("quote", "기간은 그대로", previous=previous)
    assert verified_previous(req, ctx()) is None


def test_llm_keep_will_not_approve_disagreeing_rule_set():
    req = request("quote", "3일 아니고 5일")
    rule, _ = review_fields([field("days", 3, "3일")], "rule", req, ctx())
    model, _ = review_fields([field("days", None, "5일", action="keep")], "llm", req, ctx())
    chosen = merge_candidates(req, ctx(), rule, model, None)[2]
    assert chosen.action == "uncertain"
    assert chosen.value is None
    assert chosen.status == "uncertain"


def test_period_dates_and_exclusions_can_use_explicit_date_span():
    req = request("period", "12/2부터 12/5까지 돼요. 12/4는 안 돼요.")
    fields = [
        field("start", "2026-12-02", "12/2부터"),
        field("end", "2026-12-05", "12/5까지"),
        field("excluded", ["2026-12-04"], "12/4는 안 돼요."),
    ]
    got, _ = review_fields(fields, "llm", req, ctx())
    assert all(x.status == "valid" for x in got.values())


def test_range_enumeration_does_not_invent_endpoints_or_unbounded_dates():
    req = request("dates", "12/8부터 12/10까지 공사", current={"crew": 2})
    got, _ = review_fields(
        [field("days", ["2026-12-08", "2026-12-09", "2026-12-10"], "12/8부터 12/10까지")],
        "llm",
        req,
        ctx(),
    )
    assert got["days"].status == "valid"
    bad, _ = review_fields(
        [field("days", ["2026-12-08", "2026-12-31"], "12/8부터 12/10까지")],
        "llm",
        req,
        ctx(),
    )
    assert bad["days"].status == "invalid"


def test_dates_respect_required_days_weekends_and_holidays():
    req = request("dates", "12/8, 12/9, 12/10 공사 날짜")
    value = ["2026-12-08", "2026-12-09", "2026-12-10"]
    checked, _ = review_fields(
        [field("days", value, req.text)],
        "llm",
        req,
        AssistContext(
            today=date(2026, 11, 23), base_month=date(2026, 12, 1), required_days=2, days_max=120
        ),
    )
    assert checked["days"].status == "invalid"
    checked, _ = review_fields(
        [field("days", value, req.text)],
        "llm",
        req,
        AssistContext(
            today=date(2026, 11, 23),
            base_month=date(2026, 12, 1),
            required_days=3,
            days_max=120,
            holidays=[date(2026, 12, 9)],
        ),
    )
    assert checked["days"].status == "invalid"


def test_dates_reject_hallucinated_crew_and_boundaries():
    req = request("dates", "12/8~12/9에 두 명 가능")
    invalid, _ = review_fields([field("crew", 10, "두 명 가능")], "llm", req, ctx())
    assert invalid["crew"].status == "invalid"


def test_quote_days_and_vat_restricted_by_business_context():
    req = request("quote", "공사 기간 130일, 부가세 포함")
    got, _ = review_fields([field("days", 130, "130일")], "llm", req, ctx())
    assert got["days"].status == "invalid"
    got, _ = review_fields(
        [field("vat", "included", "부가세 포함")],
        "llm",
        req,
        AssistContext(today=date(2026, 11, 23), vat_modes=["exempt"]),
    )
    assert got["vat"].status == "invalid"


def test_explicit_clear_requires_positive_deletion_instruction():
    req = request("extra", "항목 지우지 마세요. 그냥 없는 걸로 해요.")
    got, _ = review_fields(
        [field("title", None, "지우지 마세요", action="clear")], "llm", req, ctx()
    )
    assert got["title"].status == "invalid"
    req = request("extra", "항목을 삭제해 주세요.")
    got, _ = review_fields(
        [field("title", None, "삭제해 주세요", action="clear")], "llm", req, ctx()
    )
    assert got["title"].status == "valid"


def test_raw_evidence_has_to_be_a_substring_after_unicode_normalization():
    assert evidence_matches("견적 ５５５만원", "555만원")
    assert not evidence_matches("견적 555만원", "556만원")


@pytest.mark.parametrize(
    ("text", "stolen"),
    [
        ("공사 3일, 견적 555만원", 3),
        ("인원 2명, 견적 555만원", 2),
        ("시작일 2026-12-02, 견적 555만원", 2026),
        ("공사 3일, 추가 금액 5만원", 3),
        ("견적 3일, 금액 9999원", 3),
    ],
)
def test_duration_crew_and_date_numbers_never_prove_money(text, stolen):
    req = request("quote", text)
    got, _ = review_fields([field("amount", stolen, text)], "llm", req, ctx())
    assert got["amount"].status == "invalid"


def test_200_plus_character_rule_evidence_remains_valid_and_json_accepts_uuid():
    message = "견적 555만원, " + "추가 설명입니다. " * 20
    assert 200 < len(message) < 1000
    req = request(
        "quote",
        message,
        rule_fields=[field("amount", 5_550_000, message)],
    )
    parsed = AssistRequest.model_validate_json(req.model_dump_json())
    got, warnings = review_fields(parsed.rule_fields, "rule", parsed, ctx())
    assert got["amount"].status == "valid"
    assert warnings == []


def test_month_day_stays_in_base_month_year_even_if_before_anchor_day():
    context = AssistContext(today=date(2026, 11, 23), base_month=date(2026, 12, 10))
    req = request("period", "12/2부터 12/5까지 돼요")
    got, _ = review_fields([field("start", "2026-12-02", "12/2부터")], "llm", req, context)
    assert got["start"].status == "valid"


def test_normalized_period_boundary_is_grounded_in_explicit_range():
    text = "12/2부터 12/5까지 가능, 12/2는 안 돼요"
    req = request("period", text)
    got, _ = review_fields(
        [
            field("start", "2026-12-03", text),
            field("end", "2026-12-05", "12/5"),
        ],
        "rule",
        req,
        ctx(),
    )
    assert got["start"].status == "valid"
    outside, _ = review_fields([field("start", "2026-12-06", text)], "llm", req, ctx())
    assert outside["start"].status == "invalid"
