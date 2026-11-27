"""정확한 일수·인원 수량과 날짜 목록 개수는 원문의 완전한 토큰으로 검증한다."""

from datetime import date

import pytest

from app.vendor.assist_normalize import (
    crew_mentions,
    crew_replacement,
    crew_tokens,
    date_list_count_mismatch,
    date_list_declared_counts,
    date_list_leading_count_mismatch,
    date_mentions,
    date_tokens,
    duration_mentions,
    duration_quantity_risk,
    duration_tokens,
)
from app.vendor.assist_schemas import AssistContext

CTX = AssistContext(today=date(2026, 11, 25), base_month=date(2026, 12, 1))


@pytest.mark.parametrize(
    "word,value",
    [
        ("하루", 1),
        ("이틀", 2),
        ("사흘", 3),
        ("나흘", 4),
        ("닷새", 5),
        ("엿새", 6),
        ("이레", 7),
        ("여드레", 8),
        ("아흐레", 9),
        ("열흘", 10),
        ("열하루", 11),
        ("열 이틀", 12),
        ("열사흘", 13),
        ("열나흘", 14),
        ("보름", 15),
        ("열닷새", 15),
        ("열엿새", 16),
        ("열이레", 17),
        ("열여드레", 18),
        ("열아흐레", 19),
        ("세 날", 3),
        ("열 한 날", 11),
        ("스물 두 날", 22),
        ("120일", 120),
    ],
)
def test_exact_duration_preserves_complete_original_span(word, value):
    text = f"시공은 {word}로 잡아주세요"
    start = text.index(word)
    assert duration_tokens(text) == [(start, start + len(word), value)]
    assert duration_mentions(text) == {value}
    assert not duration_quantity_risk(text)


@pytest.mark.parametrize(
    "text,expected",
    [
        ("공사 기간은 3일입니다", [("3일", 3)]),
        ("시공은 닷새예요", [("닷새", 5)]),
        ("시공은 닷새동안 진행합니다", [("닷새", 5)]),
        ("공사 기간은 7일간입니다", [("7일", 7)]),
        ("공사는 3일 동안 진행합니다", [("3일", 3)]),
        ("작업은 세 일간 진행합니다", [("세 일", 3)]),
        ("12/8부터 3일간 시공합니다", [("3일", 3)]),
        ("견적 효력은 2027년 1월 8일까지, 시공은 닷새로", [("닷새", 5)]),
        ("시공은 닷새로 잡고 유효기간은 12월 30일까지", [("닷새", 5)]),
        ("3일에서 닷새로 정정합니다", [("3일", 3), ("닷새", 5)]),
        ("이전 닷새 대신 이레로 변경합니다", [("닷새", 5), ("이레", 7)]),
        ("기간은 앞서 제안한 값 그대로", []),
        ("기간은 현재 폼 입력값 그대로", []),
        ("기간은 제가 수동으로 수정했습니다", []),
        ("부가세 별도", []),
        ("항목명은 새 도배 작업", []),
    ],
)
def test_duration_lexical_tokens_do_not_invent_state_or_values(text, expected):
    assert [(text[a:b], n) for a, b, n in duration_tokens(text)] == expected


@pytest.mark.parametrize(
    "text",
    [
        "유효기간은 12월 30일까지",
        "2027-01-08",
        "2027년 1월 8일",
        "12/30일",
        "12월 300일",
        "2027년 2월 30일",
        "12월 30일, 31일, 1일",
        "12월 30일과 31일",
        "12월 30일 31일",
        "이번 달 5일",
        "2.5일",
        "1,003일",
        "1003일",
        "-3일",
        "1 003일",
        "두 3일",
        "5일자",
        "3일짜리",
        "3일치",
        "3일씩",
        "3일째",
        "3일 후",
        "3일 이전",
        "3일 이후",
        "3일정",
        "3일요일",
        "하루이틀",
        "하루 이틀",
        "두세 날",
        "한 두 날",
        "사나흘",
        "세 일",
        "열매",
    ],
)
def test_calendar_partial_and_ambiguous_quantity_has_no_exact_token(text):
    assert duration_tokens(text) == []


@pytest.mark.parametrize(
    "text",
    [
        "닷새 정도",
        "닷새가량",
        "대략 닷새",
        "약닷새",
        "최대 5일",
        "5일 이내",
        "닷새 안으로",
        "3일~5일",
        "3~5일",
        "3일 또는 닷새",
        "3일이나 5일",
        "3일에서 5일 사이",
        "하루이틀",
        "하루 이틀",
        "두세 날",
        "사나흘",
        "3일 반",
        "3일간 반",
        "3일 반나절",
        "3일 동안 2시간",
        "1주 3일",
        "한 달 사흘",
        "3일 2주",
        "2.5일",
        "1,003일",
        "-3일",
        "1003일",
    ],
)
def test_quantity_risk_rejects_nonexact_duration_in_resolved_clause(text):
    assert duration_quantity_risk(text)


@pytest.mark.parametrize(
    "text,expected",
    [
        ("3일에서 닷새로 변경", [3, 5]),
        ("닷새 대신 이레로 변경", [5, 7]),
        ("3일~5일", [3, 5]),
        ("3~5일", [5]),
        ("3일 또는 닷새", [3, 5]),
    ],
)
def test_exact_endpoints_remain_available_for_full_clause_proof(text, expected):
    # 범위 여부와 정정 관계/이력은 값 토큰만으로 확정하지 않는다.
    assert [n for _, _, n in duration_tokens(text)] == expected


@pytest.mark.parametrize(
    "word,value",
    [
        ("열 한 명", 11),
        ("열두 명", 12),
        ("열세 명", 13),
        ("스무 명", 20),
        ("스물 두 명", 22),
        ("서른세 명", 33),
        ("아흔아홉 명", 99),
        ("네 인", 4),
        ("3인", 3),
    ],
)
def test_complete_compound_crew_tokens(word, value):
    text = f"작업 인원은 {word}으로 정합니다"
    start = text.index(word)
    assert crew_tokens(text) == [(start, start + len(word), value)]


@pytest.mark.parametrize(
    "text",
    [
        "다섯 여섯 명",
        "십 두 명",
        "두 3명",
        "1 03명",
        "-3명",
        "1.5명",
        "3명분",
        "3명당",
        "3명씩",
        "3명대",
        "3인분",
        "6인치",
        "서너 명",
        "열한 명 또는 열두 명",
        "스물 한 명~스물 두 명",
    ],
)
def test_crew_partial_or_range_does_not_become_smaller_exact_count(text):
    assert crew_mentions(text) == set()


def test_native_crew_replacement_requires_exact_positive_old_new():
    text = "인원은 열 한 명에서 열 두 명으로 변경합니다"
    relation = crew_replacement(text)
    assert relation is not None
    assert (relation.old, relation.new) == (11, 12)
    assert text[slice(*relation.old_span)] == "열 한"
    assert text[slice(*relation.new_span)] == "열 두 명"
    assert crew_replacement(text + "?") is None
    assert crew_replacement(text + ". 그 요청은 취소합니다") is None
    assert crew_replacement("인원은 열 한 명에서 열 한 명으로 변경합니다") is None
    assert crew_replacement("인원은 열 한 명에서 열 두 명으로 변경하지 마세요") is None


@pytest.mark.parametrize(
    "count",
    ["닷새", "다섯 날짜", "다섯 개 날짜", "5개 날짜", "다섯 날"],
)
def test_counted_native_date_list_links_only_named_days(count):
    text = f"작업일 12월 3일 5일 7일 9일 11일 {count}로 지정합니다"
    expected = [date(2026, 12, d) for d in (3, 5, 7, 9, 11)]
    assert [day for _, _, day in date_tokens(text, CTX)] == expected
    assert date_mentions(text, CTX, expand=True) == set(expected)
    assert not date_list_count_mismatch(text, CTX)


@pytest.mark.parametrize(
    "text",
    [
        "작업일 12월 3일 5일 사흘로",
        "작업일 12월 3일, 5일 세 날짜로",
        "작업일 12월 3일, 3일 두 날짜로",
        "작업일 12월 3일 32일 이틀로",
        "작업일 12월 3일~5일 이틀로",
        "작업일 12/3, 12/5 세 날짜로",
        "작업일 2026-12-03, 2026-12-05 세 날짜로",
        "작업일 12/3~12/5 이틀로",
    ],
)
def test_counted_date_list_mismatch_does_not_validate_partial_list(text):
    assert date_list_count_mismatch(text, CTX)


@pytest.mark.parametrize(
    "text",
    [
        "작업일 12월 3일 5일 두 날짜로",
        "작업일 12월 3일, 5일 두 날짜로",
        "작업일 12월 3일~5일 사흘로",
        "작업일 12/3, 12/5 두 날짜로",
        "작업일 2026-12-03, 2026-12-05 두 날짜로",
        "작업일 12/3~12/5 사흘로",
        "작업일 12월 3일 5일 두 날짜로 인원은 3명",
        "작업일 12월 3일, 5일",
        "인원은 다섯 명으로 정합니다",
    ],
)
def test_date_count_check_respects_independent_exact_quantity(text):
    assert not date_list_count_mismatch(text, CTX)


@pytest.mark.parametrize(
    "declaration,enumeration,mismatch",
    [
        ("작업일은 사흘로 정하고", "날짜는 12월4일과20일로 등록합니다", True),
        ("방문 제외일은 사흘로 잡겠습니다.", "12월 10일과 20일입니다.", True),
        ("작업일은 사흘로 정하고", "날짜는 12월4일과12일과20일로 등록합니다", False),
        ("방문 제외일은 사흘로 잡겠습니다.", "12월 10일과 15일과 20일입니다.", False),
        ("작업일은 세 날짜로", "12월 3일부터 5일까지", False),
        ("제외일은 두 날짜로", "12/3, 12/5", False),
        ("제외일은 두 날짜로", "12월 3일, 3일", True),
        ("제외일은 사흘로 잡겠습니다", "", True),
        ("작업일은 총 3일로", "12월 4일과 20일", True),
        ("날짜 개수는 3개로", "12월 4일과 20일", True),
        ("날짜 개수는 세 개로", "12월 4일, 12일, 20일", False),
        ("작업일은 12월 3일로", "12월 3일", False),
        ("사흘 뒤 작업", "12월 3일", False),
        ("날짜는 이전 제안대로", "12월 3일", False),
        ("작업일은 3일로", "12월 3일", False),
        ("작업일은 사흘로, 날짜 개수는 두 개로", "12월 3일, 5일", True),
    ],
)
def test_leading_count_checked_against_actual_same_field_dates(declaration, enumeration, mismatch):
    assert date_list_leading_count_mismatch(declaration, enumeration, CTX) is mismatch


@pytest.mark.parametrize(
    "text,expected",
    [
        ("작업일은 사흘로 정하고 날짜는 12월4일과20일로 등록합니다", [("사흘", 3)]),
        ("방문 제외일은 사흘로 잡겠습니다. 12월 10일과 20일입니다.", [("사흘", 3)]),
        ("제외일은 세 날짜로 지정", [("세 날짜", 3)]),
        ("작업일 총 3일로 정합니다", [("총 3일", 3)]),
        ("작업 날짜 3개로", [("3개", 3)]),
        ("작업 날짜 3개 날짜로", [("3개 날짜", 3)]),
        ("12월 3일로", []),
        ("하루이틀로", []),
        ("두 세 날짜로", []),
    ],
)
def test_declared_date_count_preserves_complete_span(text, expected):
    assert [(text[a:b], value) for a, b, value in date_list_declared_counts(text)] == expected


@pytest.mark.parametrize(
    "text,word,value",
    [
        (
            "투입 작업자는 두 사람으로 확정합니다. 시공일은 12월 5일과 22일로 지정해 주세요.",
            "두 사람",
            2,
        ),
        ("작업자는 세 분으로 확정합니다", "세 분", 3),
        ("고객은 세 분이고 투입 인원은 두 사람입니다", "두 사람", 2),
        ("작업자 열 한 사람으로", "열 한 사람", 11),
        ("투입 인원은 두 사람이고 고객은 세 분", "두 사람", 2),
        ("인력은 총 세 분으로 배정", "세 분", 3),
    ],
)
def test_people_units_require_explicit_worker_target(text, word, value):
    a = text.index(word)
    assert crew_tokens(text) == [(a, a + len(word), value)]
    # 잘린 근거 자체는 실제 작업 대상이 입증되지 않는다.
    assert crew_tokens(word) == []
    assert crew_tokens(word, allow_people=True) == [(0, len(word), value)]


@pytest.mark.parametrize(
    "text",
    [
        "고객은 두 사람으로 예약합니다",
        "방문객 세 분입니다",
        "작업자 세 분 뒤 도착합니다",
        "작업자 세 분 후에 도착합니다",
        "작업자는 세 분 동안 작업합니다",
        "작업자는 세 분의 고객을 응대합니다",
        "작업자 세 분이내",
        "작업자 두세 사람",
        "작업자는 두 사람 또는 세 분",
        "작업자는 열 한 사람~열 두 사람",
        "작업자 2.5분",
        "작업자 -3분",
        "두 사람",
        "세 분",
    ],
)
def test_people_units_do_not_mistake_other_people_minutes_or_range(text):
    assert crew_tokens(text) == []
