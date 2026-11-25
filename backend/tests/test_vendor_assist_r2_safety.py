"""R2: 문자열에 들어간 지시문과 날짜 문의의 필드 범위."""

from datetime import date

import pytest

from app.vendor.assist_date_edit import validated_date_edit
from app.vendor.assist_intent_safety import assignment_directive_in_value, date_set_is_inquiry
from app.vendor.assist_schemas import AssistContext


@pytest.mark.parametrize(
    "field,text,bad,clean",
    [
        (
            "reason",
            "추가 항목명은 선반 보강, 사유는 벽체 고정 불량으로 제안",
            "벽체 고정 불량으로 제안",
            "벽체 고정 불량",
        ),
        (
            "title",
            "추가 항목명은 주방 문짝 조절로 확정",
            "주방 문짝 조절로 확정",
            "주방 문짝 조절",
        ),
        (
            "title",
            "작업명은 마루 부분 보수로 확정하고 금액은 40만원으로",
            "마루 부분 보수로 확정하고",
            "마루 부분 보수",
        ),
        (
            "reason",
            "사유는 경첩 변형 확인으로 먼저 제안해주세요",
            "경첩 변형 확인으로 먼저 제안해주세요",
            "경첩 변형 확인",
        ),
        (
            "reason",
            "사유는 고정 나사 손상으로 다시 제안해주세요",
            "고정 나사 손상으로 다시 제안해주세요",
            "고정 나사 손상",
        ),
        (
            "title",
            "공사 제목은 문틀 보수로 기재하겠습니다.",
            "문틀 보수로 기재하겠습니다",
            "문틀 보수",
        ),
        (
            "reason",
            "이유는 지지대 손상으로 하겠습니다.",
            "지지대 손상으로 하겠습니다",
            "지지대 손상",
        ),
        (
            "title",
            "항목명은 현관 보강으로 확정해 주세요. 비용은 아직 미정입니다.",
            "현관 보강으로 확정",
            "현관 보강",
        ),
        (
            "reason",
            "사유는 외벽 손상으로 제안\n추가 금액은 40만원입니다.",
            "외벽 손상으로 제안",
            "외벽 손상",
        ),
    ],
)
def test_command_tail_is_not_a_selectable_text_value(field, text, bad, clean):
    assert assignment_directive_in_value(field, bad, text)
    assert not assignment_directive_in_value(field, clean, text)


@pytest.mark.parametrize(
    "field,text,value",
    [
        ("reason", "사유는 최종 확정 도면과 현장 불일치", "최종 확정 도면과 현장 불일치"),
        ("title", "항목명은 설계 제안 검토", "설계 제안 검토"),
        (
            "reason",
            "이유는 기존 도면으로 확정된 설계와 불일치",
            "기존 도면으로 확정된 설계와 불일치",
        ),
        ("reason", "사유는 고객 요청으로 공정 변경", "고객 요청으로 공정 변경"),
        ("reason", "사유는 경첩 변형 확인으로 먼저 제안해주세요", "경첩 변형 확인"),
        ("title", "항목명은 '선반 보강으로 확정'입니다", "선반 보강으로 확정"),
        ("title", "항목명은 선반 보강, 사유는 벽체 고정 불량으로 제안", "선반 보강"),
        ("reason", "항목명은 선반 보강으로 확정, 사유는 벽체 고정 불량", "벽체 고정 불량"),
        (
            "reason",
            "사유는 고객 제안으로 확정된 공법과 불일치",
            "고객 제안으로 확정된 공법과 불일치",
        ),
    ],
)
def test_actual_nouns_and_independent_text_are_not_command_suffixes(field, text, value):
    assert not assignment_directive_in_value(field, value, text)


@pytest.mark.parametrize(
    "form,text,held",
    [
        (
            "period",
            "제외일을 12월 12일로 해도 될까요? 종료일은 12월 30일로 변경해주세요",
            {"excluded"},
        ),
        (
            "dates",
            "12월 12일과 20일에 작업 가능할지 확인 부탁드려요. 인원은 4명으로 확정",
            {"days"},
        ),
        (
            "dates",
            "작업 날짜는 12월 12일로 확정하고 인원은 4명으로 할까요?",
            set(),
        ),
        (
            "dates",
            "인원은 4명으로 확정하고 12월 12일과 20일에 작업 가능할지 확인 부탁드려요.",
            {"days"},
        ),
        (
            "dates",
            "작업일은 12월 12일로 확정해 주세요. 4명은 가능할까요?",
            set(),
        ),
        (
            "dates",
            "작업일은 12월 12일로 확정하고 4명은 가능할까요?",
            set(),
        ),
        (
            "dates",
            "인원은 4명으로 확정, 12월 12일에 작업 가능할까요?",
            {"days"},
        ),
        (
            "period",
            "시작일을 12월 5일로 변경하고 종료일은 12월 30일로 해도 될까요",
            {"end"},
        ),
        (
            "period",
            "시작일은 12월 5일로 확정, 12월 12일은 제외해도 될까요?",
            {"excluded"},
        ),
        (
            "period",
            "12월 2일부터 24일까지 가능하고 9일은 제외해도 될까요?",
            {"excluded"},
        ),
        (
            "period",
            "제외일 12월 7일, 15일, 22일은 가능할지 확인 부탁드려요.",
            {"excluded"},
        ),
        (
            "period",
            "시작일과 종료일을 12월 5일, 30일로 정해도 될까요?",
            {"start", "end"},
        ),
        (
            "period",
            "시작일, 종료일, 제외일을 이 날짜로 해도 될까요?",
            {"start", "end", "excluded"},
        ),
        (
            "period",
            "12월 2일부터 30일까지 실측 가능할지 확인 부탁드려요",
            {"start", "end"},
        ),
        (
            "period",
            "12월 12일은 제외해도 될까요? 12월 30일까지는 잡아도 돼요.",
            {"excluded"},
        ),
        (
            "period",
            "제외일을 12월 12일로 변경해 주세요. 가능할까요?",
            {"excluded"},
        ),
        (
            "period",
            "시작일은 12.05로 변경하고 종료일은 12.30까지 가능할까요?",
            {"end"},
        ),
        (
            "dates",
            "작업 날짜는 12월 12일로 변경해 주실 수 있을까요?",
            set(),
        ),
        (
            "dates",
            "작업 날짜는 12월 12일에 가능한지 확인해 주실 수 있을까요?",
            {"days"},
        ),
        (
            "period",
            "제외일을 12월 12일로 설정해 주시겠어요?",
            set(),
        ),
        (
            "period",
            "12월 12일부터 30일까지 가능해요.",
            set(),
        ),
        (
            "period",
            "12월 12일까지는 잡아도 돼요.",
            set(),
        ),
        (
            "period",
            "26일까지는 일정 잡아도 돼요",
            set(),
        ),
        (
            "period",
            "12월 12일은 안 돼요. 종료일은 12월 30일로 확정합니다.",
            set(),
        ),
        (
            "dates",
            "작업일 12월 12일, 20일로 잡아 주세요.",
            set(),
        ),
        (
            "period",
            "제외일 9일 대신 11일로 바꾸는 게 가능할지 확인해 주세요.",
            {"excluded"},
        ),
        (
            "dates",
            "작업일 9일 대신 11일로 변경할까요?",
            {"days"},
        ),
        (
            "quote",
            "공사 기간 3일로 정해도 될까요? 유효기간 12월 30일로 확정",
            {"days"},
        ),
        (
            "quote",
            "유효기간은 12월 30일로 해도 될까요? 공사 기간은 3일로 확정",
            {"valid"},
        ),
        (
            "quote",
            "견적 금액은 40만원으로 할까요? 유효기간은 12월 30일로 확정",
            set(),
        ),
        (
            "period",
            "종료일을 12월 30일로 확정해 주세요. 제외일은 아직 미정입니다.",
            {"excluded"},
        ),
        (
            "period",
            "제외일 12월 12일은 보류하고 종료일은 12월 30일로 변경해주세요.",
            {"excluded"},
        ),
    ],
)
def test_inquiry_is_limited_to_the_requested_date_fields(form, text, held):
    fields = {
        "period": ("start", "end", "excluded"),
        "dates": ("days", "crew"),
        "quote": ("days", "valid", "amount", "vat"),
    }[form]
    assert {field for field in fields if date_set_is_inquiry(form, field, text)} == held


def test_clipped_evidence_cannot_hide_the_question():
    text = "제외일은 12월 12일로 해도 될까요? 종료일은 12월 30일로 변경해 주세요."
    assert date_set_is_inquiry("period", "excluded", text, "12월 12일")
    assert not date_set_is_inquiry("period", "end", text, "12월 30일")


@pytest.mark.parametrize(
    "text",
    [
        "현재 입력한 9일은 건드리지 말고 작업일 16일만 23일로 교체해 주세요.",
        "작업일 9일은 손대지 말고, 16일 대신 23일로 바꿔 주세요.",
        "작업일 16일 대신 23일로 변경해 주세요. 9일은 건드리지 마세요.",
        "16일만 23일로 옮기고, 인원은 아까 제안한 값대로 유지해 주세요.",
        "16일만 23일로 교체하고 인원은 현재 값 그대로 유지해 주세요.",
    ],
)
def test_existing_affirmative_replacement_and_protection_remain_eligible(text):
    context = AssistContext(
        today=date(2026, 11, 24),
        base_month=date(2026, 12, 1),
        required_days=2,
    )
    assert not date_set_is_inquiry("dates", "days", text)
    assert validated_date_edit(
        "days", text, ["2026-12-09", "2026-12-16"], context, require_affirmative=True
    ) == ["2026-12-09", "2026-12-23"]


def test_existing_date_replacement_guard_not_weakened_by_independent_crew_question():
    text = "16일만 23일로 옮기고, 인원은 그대로 둘 수 있는지 확인해 주세요."
    context = AssistContext(
        today=date(2026, 11, 24),
        base_month=date(2026, 12, 1),
        required_days=2,
    )
    assert not date_set_is_inquiry("dates", "days", text)
    assert (
        validated_date_edit(
            "days", text, ["2026-12-09", "2026-12-16"], context, require_affirmative=True
        )
        is None
    )
