import pytest

from app.calc.schedule import JOB_MAX_DAYS
from app.vendor.assist_date_edit import _ordinal_mentions
from app.vendor.assist_intent_safety import _other_quote_value, mask_bound_text_literals


@pytest.mark.parametrize(
    "text, expected",
    [
        ("첫 방문", [1]),
        ("두 번째 작업일, 셋째 날짜", [2, 3]),
        ("2번째, 4 번째, ０３번째", [2, 4, 3]),
        ("12월 7일, 14일, 21일", []),
        ("0번째", [0]),
        (f"{JOB_MAX_DAYS + 1}번째", [JOB_MAX_DAYS + 1]),
    ],
)
def test_ordinal_token_contract(text, expected):
    assert [number for _, _, number in _ordinal_mentions(text)] == expected


def test_ordinal_source_span():
    text = "방문 일정 02 \t번째 날짜"
    assert [(text[a:b], n) for a, b, n in _ordinal_mentions(text)] == [("02 \t번째", 2)]


@pytest.mark.parametrize(
    "text, anywhere, at_start",
    [
        ("12월 7일까지 유효합니다", False, False),
        ("견적 1,250,000원", True, False),
        ("1.5억", True, True),
        ("12만 원", True, True),
        ("부가세 포함", True, False),
        ("별도", True, True),
        ("면세로 설정", True, True),
        ("12일", False, False),
        (",500원", True, False),
        ("１２３원", True, True),
    ],
)
def test_quote_value_mention_contract(text, anywhere, at_start):
    assert _other_quote_value(text) is anywhere
    assert _other_quote_value(text, at_start=True) is at_start


@pytest.mark.parametrize("separator", [" ", "\t", "  \t  "])
def test_markerless_literal_keeps_offsets(separator):
    literal = '"부가세 별도"'
    text = f"공사명{separator}{literal}으로 바꿔 주세요."
    assert mask_bound_text_literals(text) == text.replace(literal, " " * len(literal))


def test_quote_amount_is_not_masked_as_text():
    text = '추가 금액은 "12만원"으로 바꿔 주세요.'
    assert mask_bound_text_literals(text) == text
