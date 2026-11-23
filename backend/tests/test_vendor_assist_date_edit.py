"""검증된 이전 날짜 목록의 한 원소 정정만 허용하는 순수 함수 회귀 검사."""

from datetime import date

import pytest

from app.vendor.assist_date_edit import validated_date_edit
from app.vendor.assist_schemas import AssistContext


@pytest.fixture
def ctx():
    return AssistContext(
        today=date(2026, 11, 23),
        base_month=date(2026, 12, 1),
        required_days=3,
        days_max=30,
        crew_max=6,
        no_weekend=False,
    )


@pytest.fixture
def workdays():
    return ["2026-12-08", "2026-12-09", "2026-12-10"]


@pytest.mark.parametrize(
    "phrase",
    [
        "9일 대신 11일",
        "앞에서 말한 9일 대신 11일을 넣을게요. 인원은 같아요.",
        "작업일 9일 말고 11일로 고쳐 주세요",
        "9일을 11일로 바꿀게요",
        "공사 날짜 9일에서 11일로 변경하겠습니다",
        "12월 9일 대신 11일",
        "2026년 12월 9일 대신 2026년 12월 11일",
        "2026-12-09 대신 2026-12-11",
        "12/9 → 12/11",
    ],
)
def test_full_list_replaces_only_named_day_and_preserves_count(ctx, workdays, phrase):
    existing = workdays.copy()
    assert validated_date_edit("days", phrase, workdays, ctx) == [
        "2026-12-08",
        "2026-12-11",
        "2026-12-10",
    ]
    assert workdays == existing  # 원본 후보 수정 금지


@pytest.mark.parametrize(
    "phrase",
    [
        "빼는 날은 4일 대신 5일로 고쳐 주세요. 기간은 전과 같아요.",
        "제외일 12월 4일 말고 12월 5일로 바꿔 주세요",
        "제외할 날짜 2026-12-04에서 2026-12-05로 변경해 주세요",
    ],
)
def test_period_exclusion_is_explicitly_scoped(ctx, phrase):
    prior = ["2026-12-04", "2026-12-08"]
    assert validated_date_edit("excluded", phrase, prior, ctx) == [
        "2026-12-05",
        "2026-12-08",
    ]


@pytest.mark.parametrize(
    "phrase",
    [
        "9일 대신 11일",
        "시작일 9일 대신 11일",
        "종료일 9일 대신 11일, 제외일은 그대로",
        "공사 작업일 9일 대신 11일",
        "견적 유효기간 9일을 11일로 변경해 주세요",
        "기간 시작은 9일에서 11일로 고쳐 주세요",
    ],
)
def test_excluded_cannot_inherit_unscoped_or_wrong_field_change(ctx, phrase):
    prior = ["2026-12-09"]
    assert validated_date_edit("excluded", phrase, prior, ctx) is None


@pytest.mark.parametrize(
    "phrase",
    [
        "제외일 9일 대신 11일",
        "빼는 날 9일 말고 11일로 변경해 주세요",
        "시작일 9일을 11일로 고쳐 주세요",
        "종료일은 9일 말고 11일로 바꿔 주세요",
        "유효기간 9일 대신 11일",
    ],
)
def test_days_rejects_exclusion_or_other_field_scope(ctx, workdays, phrase):
    assert validated_date_edit("days", phrase, workdays, ctx) is None


@pytest.mark.parametrize(
    "phrase",
    [
        "9일",
        "9일과 11일",
        "9일 또는 11일",
        "9일은 그대로 두고 11일도 넣어 주세요",
        "9일 대신 11일 그리고 10일 대신 12일",
        "9일 대신 11일, 12일도 변경해 주세요",
        "9일을 11일로",  # 교체 동사 없이는 조사만으로 확정하지 않는다.
        "9일에서 11일까지",  # 기간 변경이지 단일 원소 교체가 아니다.
        "9일 대신 11일 대신 12일",
        "9일 말고 11일 아니고",
        "9일 대신 11일, 시작일도 12일로 수정",
        "9일 대신 11일은 아닌데요",
        "아마 9일 대신 11일",
        "9일 대신 11일일 수도 있어요",
        "9일 대신 11일로 바꿀까요?",
        "9일 대신 11일로 바꾸지 마세요",
        "9일 대신 11일로 변경 안 할게요",
        "9일 대신 11일로 안 바꿀게요",
        "9일 대신 11일로 바꾸지는 않을게요",
        "9일 대신 11일로 변경은 취소",
        "9일 대신 11일로 하면 될 것 같아요",
        "9일 대신 11일쯤",
        "9일 대신 11일 정도",
        "9일 대신 11일로 할 생각 중",
        "9일 대신 11일로 바꿀 예정입니다",
    ],
)
def test_ambiguous_multiple_negated_and_speculative_edits_fail_closed(ctx, workdays, phrase):
    assert validated_date_edit("days", phrase, workdays, ctx) is None


@pytest.mark.parametrize(
    "prior",
    [
        None,
        [],
        ["2026-12-08", "2026-12-10"],  # 옛 9일이 없음
        ["2026-12-09", "2026-12-09"],  # 이전 후보 중복
        ["2026-12-09", "2027-01-09"],  # '9일'의 월·연도 기준이 둘
        ["2026-02-31", "2026-12-09"],
        ["20261209"],  # ISO 형식 미준수
    ],
)
def test_missing_duplicate_or_ambiguous_previous_list_is_blocked(ctx, prior):
    assert validated_date_edit("days", "9일 대신 11일", prior, ctx) is None


@pytest.mark.parametrize(
    "phrase",
    [
        "9일 대신 9일",
        "9일 대신 10일",  # 이미 목록에 있는 날짜
        "12월 9일 대신 1월 11일",  # 다른 월로 교체할 근거가 없음
        "2026-12-09 대신 2027-12-11",  # 다른 연도
        "11월 9일 대신 12월 11일",  # 옛 날짜가 기존 목록과 일치하지 않음
        "12월 9일 대신 12월 32일",  # 달력에 없는 날
    ],
)
def test_same_month_year_and_unique_result_are_required(ctx, workdays, phrase):
    assert validated_date_edit("days", phrase, workdays, ctx) is None


def test_context_constraints_are_enforced_for_dates(ctx, workdays):
    assert (
        validated_date_edit(
            "days", "9일 대신 11일", workdays, ctx.model_copy(update={"required_days": 4})
        )
        is None
    )
    assert (
        validated_date_edit(
            "days", "9일 대신 11일", workdays, ctx.model_copy(update={"days_max": 2})
        )
        is None
    )
    assert (
        validated_date_edit(
            "days",
            "9일 대신 11일",
            workdays,
            ctx.model_copy(update={"holidays": [date(2026, 12, 11)]}),
        )
        is None
    )
    assert (
        validated_date_edit(
            "days", "9일 대신 12일", workdays, ctx.model_copy(update={"no_weekend": True})
        )
        is None
    )


def test_period_excluded_defers_full_period_bounds_to_final_validator(ctx):
    assert validated_date_edit("excluded", "제외일 4일 대신 5일", ["2026-12-04"], ctx) == [
        "2026-12-05"
    ]
    assert validated_date_edit("excluded", "제외일 4일 대신 5일", ["2026-12-04"] * 32, ctx) is None


def test_unknown_field_and_stale_previous_days_rejected(ctx):
    assert validated_date_edit("amount", "9일 대신 11일", ["2026-12-09"], ctx) is None
    assert validated_date_edit("days", "9일 대신 11일", ["2026-11-09"], ctx) is None
