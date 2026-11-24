"""단일 날짜 교체와 다른 절의 명시적 유지 증거를 분리해 검사한다."""

from datetime import date

import pytest

from app.vendor.assist_date_edit import validated_date_edit
from app.vendor.assist_schemas import AssistContext


@pytest.mark.parametrize(
    "suffix", ["로 가능한지 알아보고 있어요", "로 한다면 어떨지 논의 중이에요"]
)
def test_unconfirmed_request_cannot_override_model_abstention(ctx, suffix):
    prior = ["2026-12-08", "2026-12-09", "2026-12-16"]
    assert (
        validated_date_edit("days", "9일 대신 11일" + suffix, prior, ctx, require_affirmative=True)
        is None
    )


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


@pytest.mark.parametrize(
    "phrase",
    [
        "9일 대신 11일로 정정해 주세요. 16일은 유지요",
        "9일 대신 11일. 16일은 그대로 둘게요. 인원은 같아요",
        "8일은 그대로 두고, 9일 대신 11일로 수정해 주세요",
        "8일과 16일은 그대로 두고 9일 대신 11일",
        "9일 대신 11일. 8일과 16일은 그대로",
        "9일 대신 11일, 작업일 16일은 유지요",
        "시작일 12월 2일은 그대로, 9일 대신 11일",
        "9일 대신 11일, 종료일 12월 29일은 그대로요",
        "9일 대신 11일. 인원은 3명 그대로",
    ],
)
def test_one_replacement_with_other_explicit_retention(ctx, phrase):
    prior = ["2026-12-08", "2026-12-09", "2026-12-16"]
    snapshot = prior.copy()
    assert validated_date_edit("days", phrase, prior, ctx) == [
        "2026-12-08",
        "2026-12-11",
        "2026-12-16",
    ]
    assert prior == snapshot


@pytest.mark.parametrize(
    "phrase",
    [
        "제외할 날 중 16일만 18일로 고쳐 주세요",
        "제외할 날 중 16일만 18일로 고쳐 주세요. 20일은 유지요",
        "20일은 유지요. 제외할 날 중 16일만 18일로 고쳐 주세요",
        "제외할 날 중 16일만 18일로 고쳐 주세요. 시작일 2일은 그대로",
        "제외일 16일은 18일로 바꿔 주세요. 20일은 그대로",
    ],
)
def test_explicit_exclusion_scope_and_single_day_only(ctx, phrase):
    assert validated_date_edit("excluded", phrase, ["2026-12-16", "2026-12-20"], ctx) == [
        "2026-12-18",
        "2026-12-20",
    ]


@pytest.mark.parametrize(
    ("phrase", "expected"),
    [
        (
            "12월 22일 종료일은 건드리지 마세요. 제외할 날 중 16일만 18일로 고쳐 주세요.",
            ["2026-12-09", "2026-12-18"],
        ),
        (
            "아까 정한 제외일 중 9일 대신 11일로 정정해 주세요. 16일은 유지요.",
            ["2026-12-11", "2026-12-16"],
        ),
        (
            "12월 22일 종료일은 그대로, 제외할 날 중 16일만 18일로 고쳐 주세요.",
            ["2026-12-09", "2026-12-18"],
        ),
    ],
)
def test_archived_period_boundary_retention_does_not_block_exclusion(ctx, phrase, expected):
    assert validated_date_edit("excluded", phrase, ["2026-12-09", "2026-12-16"], ctx) == expected


@pytest.mark.parametrize(
    "phrase",
    [
        "9일 대신 11일. 16일은 추가해 주세요",
        "9일 대신 11일. 16일은 그대로. 18일도 추가해 주세요",
        "9일 대신 11일. 16일은 유지요. 17일도 그대로",
        "9일 대신 11일. 16일도 넣어 주세요",
        "9일 대신 11일. 16일은 유지요. 인원은 4명으로 수정",
        "9일 대신 11일. 인원 3명에서 4명으로",
        "인원은 3명으로 변경하고 9일 대신 11일",
        "9일 대신 11일. 시작일 2일에서 4일로 바꿔 주세요",
        "9일 대신 11일. 16일은 유지. 8일 대신 12일",
        "9일 대신 11일. 16일은 유지 아닙니다",
        "9일 대신 11일. 16일은 유지?",
        "아마 9일 대신 11일. 16일은 유지",
        "9일 대신 11일. 16일은 그대로 두고 수정 취소",
        "9일 대신 11일. 32일은 그대로",
        "9일 대신 11일. 0일은 유지요",
        "9일 대신 11일. 시작일 12월 32일은 그대로",
        "9일 대신 1월 11일. 16일은 유지요",
        "12월 9일 대신 2027년 1월 11일. 16일은 유지",
        "9일 대신 11일, 16일은 1월로 변경",
    ],
)
def test_other_changes_ambiguous_dates_and_non_retention_hold(ctx, phrase):
    assert (
        validated_date_edit("days", phrase, ["2026-12-08", "2026-12-09", "2026-12-16"], ctx) is None
    )


def test_retained_day_needs_unique_prior_and_exclusion_has_correct_scope(ctx):
    assert (
        validated_date_edit(
            "days",
            "9일 대신 11일. 16일은 유지요",
            ["2026-12-09", "2026-12-16", "2027-01-16"],
            ctx.model_copy(update={"required_days": 3}),
        )
        is None
    )
    assert (
        validated_date_edit(
            "excluded",
            "작업일 16일만 18일로 수정, 20일은 유지",
            ["2026-12-16", "2026-12-20"],
            ctx,
        )
        is None
    )
    assert (
        validated_date_edit(
            "days",
            "제외할 날 중 16일만 18일로 고쳐 주세요",
            ["2026-12-08", "2026-12-09", "2026-12-16"],
            ctx,
        )
        is None
    )


@pytest.mark.parametrize(
    ("field", "phrase", "prior", "expected"),
    [
        (
            "days",
            "아까 말한 14일만 하루 밀려서 12월 15일로요. 7일이랑 21일은 그대로입니다.",
            ["2026-12-07", "2026-12-14", "2026-12-21"],
            ["2026-12-07", "2026-12-15", "2026-12-21"],
        ),
        (
            "excluded",
            "아 16일 대신 17일 쉬는 걸로요 나머진 그대로",
            ["2026-12-09", "2026-12-16"],
            ["2026-12-09", "2026-12-17"],
        ),
    ],
)
def test_explicit_single_change_with_neighbor_retention(ctx, field, phrase, prior, expected):
    assert validated_date_edit(field, phrase, prior, ctx, require_affirmative=True) == expected


@pytest.mark.parametrize(
    "phrase",
    [
        "14일만 하루 밀려서 12월 16일로요. 7일이랑 21일은 그대로입니다.",
        "15일만 하루 밀려서 12월 14일로요. 7일이랑 21일은 그대로입니다.",
        "14일만 하루 당겨서 12월 13일로요. 7일이랑 21일은 그대로입니다.",
        "14일만 하루 밀려서 12월 15일로 가능할까요? 7일이랑 21일은 그대로입니다.",
        "14일만 하루 밀려서 12월 15일로요. 7일이랑 22일은 그대로입니다.",
        "14일만 하루 밀려서 12월 15일로요. 7일이랑 21일은 그대로가 아니에요.",
        "14일만 하루 밀려서 12월 15일로요. 7일은 그대로고 21일은 22일로 바꿔요.",
    ],
)
def test_shift_requires_exact_one_day_and_true_retention(ctx, phrase):
    assert (
        validated_date_edit(
            "days",
            phrase,
            ["2026-12-07", "2026-12-14", "2026-12-21"],
            ctx,
            require_affirmative=True,
        )
        is None
    )


@pytest.mark.parametrize(
    "phrase",
    [
        "16일 대신 17일 쉬는 걸로요 나머진 그대로?",
        "16일 대신 17일 쉬는 걸로요. 9일은 제외하지 않을게요.",
        "작업일 16일 대신 17일 쉬는 걸로요 나머진 그대로",
        "시작일 16일 대신 17일 쉬는 걸로요 나머진 그대로",
        "16일 대신 17일 쉬는 걸로요. 20일도 그대로",
    ],
)
def test_exclusion_result_does_not_hide_conflicting_clauses(ctx, phrase):
    assert (
        validated_date_edit(
            "excluded", phrase, ["2026-12-09", "2026-12-16"], ctx, require_affirmative=True
        )
        is None
    )


def test_exclusion_result_cannot_be_mistaken_for_workday_edit(ctx):
    assert (
        validated_date_edit(
            "days",
            "16일 대신 17일 쉬는 걸로요 나머진 그대로",
            ["2026-12-09", "2026-12-14", "2026-12-16"],
            ctx,
            require_affirmative=True,
        )
        is None
    )
