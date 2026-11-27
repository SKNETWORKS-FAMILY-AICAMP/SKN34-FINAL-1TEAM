"""한글 금액의 전체 표기·범위·한도·서버 후보를 오프라인에서 검증한다."""

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
)
from app.vendor.assist_service import interpret
from app.vendor.assist_validation import _amount_mentions, review_fields
from app.vendor.assist_written_money import written_money_tokens

FRESH_TEXT = "현장 추가 물량까지 다시 잡았어요. 이번 견적 금액은 천이백육십만 원으로 넣어 주세요."
FRESH_EVIDENCE = "이번 견적 금액은 천이백육십만 원으로 넣어 주세요"


def _request(form, text):
    return AssistRequest(
        request_id="written-money",
        form=form,
        resource_id=UUID("00000000-0000-4000-8000-000000000179"),
        text=text,
        current={"amount": 2_820_000},
        previous_candidate=None,
        rule_fields=[],
    )


@pytest.mark.parametrize("form", ["quote", "extra"])
@pytest.mark.parametrize(
    ("text", "evidence", "value"),
    [
        (FRESH_TEXT, FRESH_EVIDENCE, 12_600_000),
        ("견적 천만 원이요", "천만 원", 10_000_000),
        ("견적 삼백만원입니다", "삼백만원", 3_000_000),
        ("이 천 오 백 만 원으로", "이 천 오 백 만 원", 25_000_000),
        ("금액 오십육만칠천팔백구십 원", "오십육만칠천팔백구십 원", 567_890),
        (
            "추가금 일억 이천삼백사십오만 육천칠백팔십구 원",
            "일억 이천삼백사십오만 육천칠백팔십구 원",
            123_456_789,
        ),
        ("견적 십억 원", "십억 원", 1_000_000_000),
        ("금액 오 원", "오 원", 5),
        ("견적은 이천오백만원입니다", "이천오백만원", 25_000_000),
        ("공사 3일, 금액 삼백만 원", "삼백만 원", 3_000_000),
    ],
)
def test_written_money_full_expression(form, text, evidence, value):
    field = FieldIntent(field="amount", action="set", value=value, evidence=evidence)
    fields, _ = review_fields(
        [field], "llm", _request(form, text), AssistContext(today=date(2026, 11, 23))
    )
    assert (fields["amount"].status, fields["amount"].value) == ("valid", value)


@pytest.mark.parametrize("form", ["quote", "extra"])
@pytest.mark.parametrize(
    ("text", "evidence", "value"),
    [
        (FRESH_TEXT, "이백육십만 원", 2_600_000),
        (FRESH_TEXT, "육십만 원", 600_000),
        (FRESH_TEXT, "천이백육십만", 12_600_000),
        (FRESH_TEXT, "천이백육십만 원", 12_000_000),
        ("견적 천만으로", "천만", 10_000_000),
        ("견적 일백백만원", "백만원", 1_000_000),
        ("견적 일억만 원", "일억만 원", 100_010_000),
        ("견적 이십오백만원", "오백만원", 5_000_000),
        ("견적 십억 일 원", "십억 일 원", 1_000_000_001),
        ("견적 십억 일 원", "십억", 1_000_000_000),
        ("견적 영 원", "영 원", 0),
        ("견적 삼백만 500원", "500원", 500),
        ("견적 1억 삼백만원", "삼백만원", 3_000_000),
        ("견적 삼백만 5,000원", "5,000원", 5_000),
        ("견적 천500만원", "500만원", 5_000_000),
        ("견적은삼 500만원", "500만원", 5_000_000),
        ("견적은삼백만 500원", "500원", 500),
        ("견적 삼.백만원", "백만원", 1_000_000),
        ("견적 삼점 오백만원", "오백만원", 5_000_000),
        ("견적 삼백만원자재", "삼백만원", 3_000_000),
        ("견적 삼백만원,사백만원", "사백만원", 4_000_000),
        ("견적 삼백만원,금액은사백만원", "삼백만원", 3_000_000),
        ("견적 삼백만원~사백만원", "사백만원", 4_000_000),
        ("견적 삼~사백만원", "사백만원", 4_000_000),
        ("견적 삼~400만원", "400만원", 4_000_000),
        ("견적 삼백~400만원", "400만원", 4_000_000),
        ("견적 삼백만원, 이만", "삼백만원", 3_000_000),
        ("견적 삼백만원 또는 400만원", "400만원", 4_000_000),
        ("견적 삼백만원, 4000000", "삼백만원", 3_000_000),
        ("견적 삼백만원 이상", "삼백만원", 3_000_000),
        ("견적 삼백만원 내외", "삼백만원", 3_000_000),
        ("견적 한 삼백만원", "삼백만원", 3_000_000),
        ("견적 -삼백만원", "삼백만원", 3_000_000),
        ("견적 삼백만원으로 할까?", "삼백만원", 3_000_000),
        ("견적 삼백만원으로 넣지 마세요", "삼백만원", 3_000_000),
        ("견적 삼백만원, 취소해 주세요", "삼백만원", 3_000_000),
        ("견적 삼백만원은 미정", "삼백만원", 3_000_000),
        ("아까 300만원이라고 했는데 사백만원으로 고칠게요", "사백만원", 4_000_000),
        ("아까 삼백만원이라고 했는데 400만원으로 고칠게요", "400만원", 4_000_000),
    ],
)
def test_written_money_partial_or_ambiguous_expression_held(form, text, evidence, value):
    field = FieldIntent(field="amount", action="set", value=value, evidence=evidence)
    fields, _ = review_fields(
        [field], "llm", _request(form, text), AssistContext(today=date(2026, 11, 23))
    )
    assert fields["amount"].status != "valid"
    assert fields["amount"].value is None


def test_written_money_correction_does_not_enable_last_amount():
    text = "아까 삼백만원이라고 했는데 400만원으로 고칠게요"
    field = FieldIntent(
        field="amount",
        action="set",
        value=4_000_000,
        evidence="400만원",
        correction=CorrectionRelation(
            kind="replace",
            rejected_value=3_000_000,
            rejected_evidence="삼백만원",
            accepted_evidence="400만원",
        ),
    )
    fields, _ = review_fields(
        [field], "llm", _request("quote", text), AssistContext(today=date(2026, 11, 23))
    )
    assert fields["amount"].status != "valid"


def test_fresh_written_quote_replay_with_model_stub(monkeypatch):
    async def fake_model(_request, _context):
        return ModelInterpretation(
            fields=[
                FieldIntent(
                    field="amount",
                    action="set",
                    value=12_600_000,
                    evidence=FRESH_EVIDENCE,
                    intent_kind="explicit_change",
                    value_source="utterance",
                )
            ]
        )

    monkeypatch.setattr(vendor_assist, "interpret", fake_model)
    result = asyncio.run(
        interpret(_request("quote", FRESH_TEXT), AssistContext(today=date(2026, 11, 23)))
    )
    amount = next(field for field in result.fields if field.field == "amount")
    assert (amount.status, amount.action, amount.value, amount.source) == (
        "valid",
        "set",
        12_600_000,
        "llm",
    )


@pytest.mark.parametrize(
    ("text", "values"),
    [
        ("견적 1천500만 원", {15_000_000}),
        ("견적 1천500만이라고 했는데 555만으로 고칠게요", {15_000_000, 5_550_000}),
        ("견적 1천500만이 아니라 555만 원", {15_000_000, 5_550_000}),
        ("견적 1천500만 사유 변경", {15_000_000}),
        ("견적 2천3백5십만 원", {23_500_000}),
        ("견적 1억 2천만원", {120_000_000}),
        ("공사 3일 555만원", {5_550_000}),
        ("공사 555만원", {5_550_000}),
    ],
)
def test_existing_arabic_amounts_preserved(text, values):
    assert _amount_mentions(text) == values


@pytest.mark.parametrize(
    "text",
    [
        "견적은삼백만 500원",
        "금액이삼 500만원",
        "견적 삼백부터 400만원",
        "견적 삼백점 400만원",
        "견적 삼백만원, 4000000",
        "견적 삼백만원, 4,000,000",
        "견적 삼백만원, 4000000 원",
    ],
)
def test_written_amount_does_not_fall_back_to_partial_number(text):
    assert written_money_tokens(text) == []


def test_non_money_words_do_not_block_normal_numeric_amount():
    assert written_money_tokens("공사 견적 555만원") is None
