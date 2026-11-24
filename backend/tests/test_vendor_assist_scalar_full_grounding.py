"""전체 발화의 금액·인원과 잘린 후보 근거를 구분한다."""

from datetime import date
from uuid import UUID

import pytest

from app.vendor.assist_schemas import (
    AssistContext,
    AssistRequest,
    CorrectionRelation,
    FieldIntent,
    PreviousCandidate,
)
from app.vendor.assist_validation import review_fields, verified_previous


def _request(form, text, **kwargs):
    return AssistRequest(
        request_id="scalar-full-grounding",
        resource_id=UUID("00000000-0000-4000-8000-000000000179"),
        form=form,
        text=text,
        current=kwargs.pop("current", {}),
        previous_candidate=kwargs.pop("previous_candidate", None),
        rule_fields=kwargs.pop("rule_fields", []),
        **kwargs,
    )


def _context():
    return AssistContext(today=date(2026, 11, 23), base_month=date(2026, 12, 1))


@pytest.mark.parametrize("source", ["rule", "llm"])
@pytest.mark.parametrize(
    ("text", "value", "evidence"),
    [
        ("견적 1천500만원", 5_000_000, "500만원"),
        ("견적 2천300만원", 3_000_000, "300만원"),
        ("견적 1천 500만 원", 5_000_000, "500만 원"),
        ("견적 1천500만원", 1_000, "1천"),
        ("견적 21만 5천 원", 210_000, "21만"),
        ("견적 21만 5천 원", 5_000, "5천 원"),
        ("견적 1억 2천만원", 20_000_000, "2천만원"),
        ("견적 15,000,000원", 5_000_000, "5,000,000원"),
        ("견적 천만원", 10_000_000, "천만원"),
        ("견적 500만원", 500, "견적 500"),
    ],
)
def test_partial_amount_is_rejected(text, value, evidence, source):
    item = FieldIntent(field="amount", action="set", value=value, evidence=evidence)
    reviewed, _ = review_fields([item], source, _request("quote", text), _context())
    assert reviewed["amount"].status != "valid"
    assert reviewed["amount"].value is None


@pytest.mark.parametrize(
    ("text", "value", "evidence"),
    [
        ("견적 1천500만원", 15_000_000, "1천500만"),
        ("견적 2천300만 원", 23_000_000, "2천300만 원"),
        ("견적 1천 500만 원", 15_000_000, "1천 500만 원"),
        ("견적 21만 5천 원", 215_000, "21만 5천 원"),
        ("견적 1억 2천만원", 120_000_000, "1억 2천만원"),
        ("견적 15,000,000원", 15_000_000, "15,000,000원"),
    ],
)
def test_full_amount_remains_valid(text, value, evidence):
    item = FieldIntent(field="amount", action="set", value=value, evidence=evidence)
    reviewed, _ = review_fields([item], "llm", _request("quote", text), _context())
    assert (reviewed["amount"].status, reviewed["amount"].value) == ("valid", value)


@pytest.mark.parametrize("source", ["rule", "llm"])
@pytest.mark.parametrize(
    ("text", "value", "evidence"),
    [
        ("작업 인원 12명", 2, "2명"),
        ("작업 인원 10명", 1, "10명"),
        ("작업 인원 10명", 0, "0명"),
        ("작업 인원 2~3명", 3, "3명"),
        ("작업 인원 2~3명", 2, "2명"),
        ("작업 인원 2명~3명", 2, "2명"),
        ("작업 인원 2명~3명", 3, "3명"),
    ],
)
def test_partial_or_uncertain_crew_is_rejected(text, value, evidence, source):
    item = FieldIntent(field="crew", action="set", value=value, evidence=evidence)
    reviewed, _ = review_fields([item], source, _request("dates", text), _context())
    assert reviewed["crew"].status != "valid"
    assert reviewed["crew"].value is None


@pytest.mark.parametrize("value", [2, 10, 12])
def test_complete_crew_within_business_limit_remains_valid(value):
    text = f"작업 인원 {value}명"
    item = FieldIntent(field="crew", action="set", value=value, evidence=f"{value}명")
    reviewed, _ = review_fields([item], "llm", _request("dates", text), _context())
    assert (reviewed["crew"].status, reviewed["crew"].value) == ("valid", value)


def test_correction_rejects_clipped_old_amount_even_with_valid_new_amount():
    text = "1천500만원 아니고 2천300만원으로 수정해 주세요."
    item = FieldIntent(
        field="amount",
        action="set",
        value=23_000_000,
        evidence=text,
        correction=CorrectionRelation(
            kind="replace",
            rejected_value=5_000_000,
            rejected_evidence="500만원",
            accepted_evidence="2천300만원",
        ),
    )
    reviewed, _ = review_fields([item], "llm", _request("quote", text), _context())
    assert reviewed["amount"].status != "valid"


def test_previous_candidate_cannot_resurrect_clipped_amount():
    item = FieldIntent(field="amount", action="set", value=5_000_000, evidence="500만원")
    previous = PreviousCandidate(
        request_id="old",
        text="견적 1천500만원",
        fields=[item],
        baselines={"amount": None},
    )
    req = _request(
        "quote", "금액은 전과 같아요.", current={"amount": None}, previous_candidate=previous
    )
    assert verified_previous(req, _context()) is None
