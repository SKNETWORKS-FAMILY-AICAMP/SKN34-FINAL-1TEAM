"""업체 도우미 날짜·인원 정규화는 원문 위치와 목록/범위 구분을 유지한다."""

from datetime import date

import pytest

from app.vendor.assist_normalize import crew_mentions, date_mentions, date_tokens
from app.vendor.assist_schemas import AssistContext


def context(*, base: date | None = date(2026, 11, 1)) -> AssistContext:
    return AssistContext(today=date(2026, 11, 23), base_month=base)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("12/11,12,13일", ["2026-12-11", "2026-12-12", "2026-12-13"]),
        ("12월7,8,9일", ["2026-12-07", "2026-12-08", "2026-12-09"]),
        ("12월7일부터9일까지", ["2026-12-07", "2026-12-09"]),
        ("12월7일부터 9일까지", ["2026-12-07", "2026-12-09"]),
        ("12월7일~9일", ["2026-12-07", "2026-12-09"]),
        ("12/7~9일", ["2026-12-07", "2026-12-09"]),
        ("11/30,12/1,2일", ["2026-11-30", "2026-12-01", "2026-12-02"]),
        ("2027-12-02부터 2027-12-05까지", ["2027-12-02", "2027-12-05"]),
        ("2028-02-29", ["2028-02-29"]),
        ("12월7일부터9일까지, 8일 제외", ["2026-12-07", "2026-12-09", "2026-12-08"]),
    ],
)
def test_inherited_month_and_original_offsets(text: str, expected: list[str]):
    tokens = date_tokens(text, context())
    assert [day.isoformat() for _, _, day in tokens] == expected
    assert all(text[start:end] for start, end, _ in tokens)
    assert len({(start, end) for start, end, _ in tokens}) == len(tokens)
    for start, end, _ in tokens:
        assert 0 <= start < end <= len(text)
        assert text[start:end] in text


def test_abbreviated_list_returns_only_named_days_with_november_base():
    text = "12/11,12,13일"
    tokens = date_tokens(text, context())
    assert [(text[a:b], day.isoformat()) for a, b, day in tokens] == [
        ("12/11", "2026-12-11"),
        ("12", "2026-12-12"),
        ("13일", "2026-12-13"),
    ]
    assert date_mentions(text, context(), expand=True) == {
        date(2026, 12, 11),
        date(2026, 12, 12),
        date(2026, 12, 13),
    }
    assert date_mentions("12/11,13일", context(), expand=True) == {
        date(2026, 12, 11),
        date(2026, 12, 13),
    }


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("2027 년 \t12 월 \n2 일", [date(2027, 12, 2)]),
        (
            "12 월 \t11 일, 12 일, 13 일",
            [date(2026, 12, 11), date(2026, 12, 12), date(2026, 12, 13)],
        ),
    ],
)
def test_calendar_whitespace_keeps_values_and_source_spans(text, expected):
    tokens = date_tokens(text, context())
    assert [value for _, _, value in tokens] == expected
    assert all(text[start:end].strip() for start, end, _ in tokens)


def test_explicit_month_overrides_november_context_for_bare_range_endpoint():
    text = "12월7일부터9일까지"
    assert [(text[a:b], day.isoformat()) for a, b, day in date_tokens(text, context())] == [
        ("12월7일", "2026-12-07"),
        ("9일", "2026-12-09"),
    ]
    assert date_mentions(text, context(), expand=True) == {
        date(2026, 12, 7),
        date(2026, 12, 8),
        date(2026, 12, 9),
    }
    assert date_mentions(text, context(), expand=False) == {date(2026, 12, 7), date(2026, 12, 9)}


def test_unknown_and_competing_anchors_are_not_filled():
    assert date_tokens("9일", context(base=None)) == []
    assert date_tokens("9일", context(), allow_bare=False) == []
    assert date_tokens("12/7,8,11/9", context()) == [
        (0, 4, date(2026, 12, 7)),
        (7, 11, date(2026, 11, 9)),
    ]
    # 명시적 12월이 없는 '9일'은 기존 base_month 해석을 유지한다.
    assert date_tokens("9일", context()) == [(0, 2, date(2026, 11, 9))]


def test_invalid_calendar_dates_and_durations_do_not_become_date_tokens():
    for text in ("2027-02-30", "2027-13-02", "12월32일", "12/00"):
        assert date_tokens(text, context()) == []
    assert [value.isoformat() for _, _, value in date_tokens("12/8부터 3일간", context())] == [
        "2026-12-08"
    ]
    assert [value.isoformat() for _, _, value in date_tokens("12/8부터 3일, 2명", context())] == [
        "2026-12-08"
    ]
    assert date_mentions("12/8부터 3일간", context(), expand=True) == {date(2026, 12, 8)}
    assert [x[2] for x in date_tokens("12/11,32,13일", context())] == [date(2026, 12, 11)]
    assert date_mentions("12월7일~9일", context(), expand=True) == {
        date(2026, 12, 7),
        date(2026, 12, 8),
        date(2026, 12, 9),
    }


def test_ranges_only_expand_when_both_endpoints_and_bounded_span_are_grounded():
    assert date_mentions("12/8~12/10", context(), expand=True) == {
        date(2026, 12, 8),
        date(2026, 12, 9),
        date(2026, 12, 10),
    }
    assert date_mentions("12/8~12/10", context(), expand=False) == {
        date(2026, 12, 8),
        date(2026, 12, 10),
    }
    assert date_mentions("12/10~12/8", context(), expand=True) == {
        date(2026, 12, 8),
        date(2026, 12, 10),
    }
    assert date_mentions("2027-01-01부터 2027-08-01까지", context(), expand=True) == {
        date(2027, 1, 1),
        date(2027, 8, 1),
    }


def test_year_anchor_policy_matches_existing_validator():
    dec = AssistContext(today=date(2026, 11, 23), base_month=date(2026, 12, 10))
    assert date_tokens("12/2부터12/5까지", dec)[0][2] == date(2026, 12, 2)
    assert date_tokens("1월2일", context(base=None))[0][2] == date(2027, 1, 2)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("작업자 네 명", {4}),
        ("한 명 두 명 세 명 네 명", {1, 2, 3, 4}),
        ("2명", {2}),
        ("104명", set()),
        ("123명", set()),
        ("12/8부터12/10까지 두 명", {2}),
        ("네, 일정은 그대로", set()),
        ("두세 명", set()),
        ("두 세 명", set()),
        ("한두 명", set()),
        ("두~세 명", set()),
        ("2~3명", set()),
        ("2명~3명", set()),
        ("두세 명 아니고 네 명", {4}),
        ("인원 네 명과 금액 4만 원", {4}),
        ("인원 변경, 12/3에 공사", set()),
    ],
)
def test_korean_crew_words_have_unit_and_ambiguous_ranges_are_ignored(text, expected):
    assert crew_mentions(text) == expected
