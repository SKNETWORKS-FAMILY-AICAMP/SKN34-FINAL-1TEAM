"""Regression coverage for sequential lexical parsing and source offsets."""

import pytest

from app.vendor.assist_intent import _exclusion_predicate_starts, _extra_implicit_lead
from app.vendor.assist_intent_safety import (
    _ends_with_clause_separator,
    _literal_quote,
    _mask_quoted_spans,
    _text_explicit_lead,
)
from app.vendor.assist_lexical import compact_whitespace
from app.vendor.assist_normalize import _calendar_prefix, _duration_malformed, duration_tokens
from app.vendor.assist_vat_semantics import standalone_vat_modes, vat_selection_mentions


def test_compact_whitespace_keeps_original_boundaries():
    raw = " \t부가세   포함\n확정"
    compact, offsets = compact_whitespace(raw)
    assert compact == " 부가세 포함 확정"
    assert len(offsets) == len(compact) + 1
    assert offsets[0] == 0 and offsets[-1] == len(raw)
    for i, char in enumerate(compact):
        original = raw[offsets[i] : offsets[i + 1]]
        if char == " ":
            assert original and original.isspace()
        else:
            assert original == char


@pytest.mark.parametrize(
    "text,expected",
    [
        ('"문틀 보수"', True),
        ("'현장 보강'", True),
        ("“문틀 보수”", True),
        ("‘현장 보강’", True),
        ("“문틀 보수", False),
        ("‘현장 보강”", False),
        ('"문틀\n보수"', False),
    ],
)
def test_quote_contract(text, expected):
    assert _literal_quote(text) is expected


def test_mask_quoted_spans_keeps_other_text():
    first, second = "“문틀 보수”", '"나사 풀림"'
    raw = f"제목은 {first}이고 사유는 {second}입니다."
    expected = raw.replace(first, " " * len(first)).replace(second, " " * len(second))
    assert _mask_quoted_spans(raw) == expected


@pytest.mark.parametrize(
    "prefix,extra,text",
    [
        ("", True, True),
        ("확인합니다. 그리고 \t", True, True),
        ("일정이 확정되면 ", True, False),
        ("기존 값을 입력하고 ", True, False),
        ("자재 명칭 ", False, False),
    ],
)
def test_independent_assignment_leads(prefix, extra, text):
    assert _extra_implicit_lead(prefix) is extra
    assert _text_explicit_lead(prefix) is text


@pytest.mark.parametrize("text", ["확인합니다.  ", "확인합니다\n\t", "확인합니다？ "])
def test_clause_separator_preserves_boundary(text):
    assert _ends_with_clause_separator(text)


def test_postposed_exclusion_coordinates():
    raw = "12월 3일, 9일 방문 제외"
    assert list(_exclusion_predicate_starts(raw)) == [raw.index("방문")]
    raw = "9일   못 가는 날"
    assert list(_exclusion_predicate_starts(raw)) == [len("9일")]


@pytest.mark.parametrize("text", ["12월 ", "2026년\t", "다음 달 ", "이번 해"])
def test_calendar_prefix(text):
    assert _calendar_prefix(text)


@pytest.mark.parametrize(
    "text,expected",
    [
        ("7일", False),
        ("7일간", False),
        ("2.5일", True),
        ("1,000일", True),
        ("- 2일", True),
        ("1000일", True),
        ("반나절", True),
    ],
)
def test_complete_duration_token_risk(text, expected):
    assert _duration_malformed(text) is expected


def test_calendar_not_duration():
    assert duration_tokens("12월 7일") == []
    assert [v for _, _, v in duration_tokens("작업 기간은 7일입니다.")] == [7]


def test_vat_selection_offsets():
    raw = "부가세 포함  선택은 취소합니다."
    selections = vat_selection_mentions(raw)
    assert len(selections) == 1
    selection = selections[0]
    assert (selection.start, selection.end, selection.mode) == (0, raw.index("은 취소"), "included")


def test_vat_grammar_whitespace():
    assert standalone_vat_modes("포함 \t 말고   별도로 확정합니다.") == {"included", "excluded"}


@pytest.mark.parametrize(
    "text,expected",
    [
        ("별도로 다시 우선 확정합니다.", {"excluded"}),
        ("별도로 새로 먼저 최종적으로 지정합니다.", {"excluded"}),
        ("별도로 우선 확인해 주세요.", set()),
    ],
)
def test_standalone_vat_adverbs_preserve_lexical_contract(text, expected):
    assert standalone_vat_modes(text) == expected
