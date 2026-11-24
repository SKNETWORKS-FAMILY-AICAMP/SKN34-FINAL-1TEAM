"""금액과 인원은 원문의 완전한 숫자 표현만 근거로 인정한다."""

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


def _request(form, text, previous=None):
    return AssistRequest(
        request_id="scalar-grounding",
        resource_id=UUID("00000000-0000-4000-8000-000000000179"),
        form=form,
        text=text,
        current={"amount": None} if form == "quote" else {"crew": None},
        rule_fields=[],
        previous_candidate=previous,
    )


def _context():
    return AssistContext(today=date(2026, 11, 23), base_month=date(2026, 12, 1), crew_max=20)


@pytest.mark.parametrize("source", ["rule", "llm"])
@pytest.mark.parametrize(
    ("text", "evidence", "value"),
    [
        ("견적은 1천500만원입니다", "1천500만원", 15_000_000),
        ("견적은 1천 500만 원입니다", "1천 500만", 15_000_000),
        ("견적은 2천300만입니다", "2천300만", 23_000_000),
        ("견적은 1억2천만원입니다", "1억2천만원", 120_000_000),
        ("견적은 1억 2천만원입니다", "1억 2천만", 120_000_000),
        ("견적은 21만5천 원입니다", "21만5천", 215_000),
        ("견적은 1만500원입니다", "1만500원", 10_500),
        ("견적은 1,500만원입니다", "1,500만원", 15_000_000),
        ("견적은 천만 원입니다", "천만 원", 10_000_000),
    ],
)
def test_complete_amount_is_grounded(source, text, evidence, value):
    candidate = FieldIntent(field="amount", action="set", value=value, evidence=evidence)
    fields, _ = review_fields([candidate], source, _request("quote", text), _context())
    assert (fields["amount"].status, fields["amount"].value) == ("valid", value)


@pytest.mark.parametrize("source", ["rule", "llm"])
@pytest.mark.parametrize(
    ("text", "evidence", "value"),
    [
        ("견적은 1천500만원입니다", "500만원", 5_000_000),
        ("견적은 1천500만원입니다", "1천500만원", 5_000_000),
        ("견적은 1천 500만 원입니다", "500만 원", 5_000_000),
        ("견적은 2천300만입니다", "300만", 3_000_000),
        ("견적은 2천300만입니다", "2천", 2_000),
        ("견적은 1억2천만원입니다", "1억", 100_000_000),
        ("견적은 1억 2천만원입니다", "2천만원", 20_000_000),
        ("견적은 21만 5천 원입니다", "21만", 210_000),
        ("견적은 21만 5천 원입니다", "5천 원", 5_000),
        ("견적은 1,500만원입니다", "500만원", 5_000_000),
        ("견적은 천만 원입니다", "천만", 10_000_000),
        ("견적 500만원", "견적 500", 500),
    ],
)
def test_partial_or_unprinted_amount_is_not_grounded(source, text, evidence, value):
    candidate = FieldIntent(field="amount", action="set", value=value, evidence=evidence)
    fields, _ = review_fields([candidate], source, _request("quote", text), _context())
    assert fields["amount"].status == "invalid"


@pytest.mark.parametrize("source", ["rule", "llm"])
@pytest.mark.parametrize(
    ("text", "evidence", "value", "valid"),
    [
        ("인원 12명", "12명", 12, True),
        ("인원은10명", "10명", 10, True),
        ("인원 12명", "2명", 2, False),
        ("인원 10명", "0명", 0, False),
        ("인원 2~3명", "3명", 3, False),
        ("인원 2명~3명", "2명", 2, False),
        ("인원 2명~3명", "3명", 3, False),
        ("인원 2~3명", "2~3명", 2, False),
        ("인원 2〜3명", "3명", 3, False),
        ("인원 12명/10명", "12명", 12, False),
        ("인원 12명/10명", "10명", 10, False),
        ("인원 두세 명", "세 명", 3, False),
        ("인원 두세 명 아니고 네 명", "네 명", 4, True),
    ],
)
def test_crew_requires_complete_unambiguous_token(source, text, evidence, value, valid):
    candidate = FieldIntent(field="crew", action="set", value=value, evidence=evidence)
    fields, _ = review_fields([candidate], source, _request("dates", text), _context())
    assert (fields["crew"].status == "valid") is valid


@pytest.mark.parametrize("historical", [False, True])
@pytest.mark.parametrize("clipped", [False, True])
def test_money_correction_requires_complete_old_amount(historical, clipped):
    old_value = 5_000_000 if clipped else 15_000_000
    old_evidence = "500만" if clipped else "1천500만"
    previous = (
        PreviousCandidate(
            request_id="old",
            text="견적 500만원" if clipped else "견적 1천500만원",
            fields=[
                FieldIntent(field="amount", action="set", value=old_value, evidence=old_evidence)
            ],
            baselines={"amount": None},
        )
        if historical
        else None
    )
    text = (
        "아까 1천500만이라고 했는데 555만으로 고칠게요"
        if historical
        else "1천500만 말고 555만으로 수정해 주세요"
    )
    candidate = FieldIntent(
        field="amount",
        action="set",
        value=5_550_000,
        evidence="555만",
        correction=CorrectionRelation(
            kind="replace",
            rejected_value=old_value,
            rejected_evidence=old_evidence,
            accepted_evidence="555만",
        ),
    )
    request = _request("quote", text, previous)
    context = _context()
    verified = verified_previous(request, context)
    if historical:
        assert verified is not None
    fields, _ = review_fields([candidate], "llm", request, context, previous=verified)
    assert (fields["amount"].status == "valid") is not clipped


def test_previous_with_clipped_amount_cannot_be_restored():
    previous = PreviousCandidate(
        request_id="old",
        text="견적 1천500만원",
        fields=[FieldIntent(field="amount", action="set", value=5_000_000, evidence="500만원")],
        baselines={"amount": None},
    )
    assert verified_previous(_request("quote", "금액은 그대로", previous), _context()) is None
