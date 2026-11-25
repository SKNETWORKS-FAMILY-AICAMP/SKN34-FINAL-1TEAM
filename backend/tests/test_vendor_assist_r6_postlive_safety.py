"""Executed R6 regressions: separate unavailable days and polite assignments."""

import pytest

from app.vendor.assist_intent_safety import date_set_is_inquiry, text_set_is_inquiry


@pytest.mark.parametrize(
    "text,held",
    [
        (
            "방문을 12월 14일에 못 받는 걸로 할까요? 마지막 실측 가능일은 12월 29일로 확정합니다.",
            {"excluded"},
        ),
        ("12월 8일하고 15일, 23일은 실측 못 받는 날로 빼주시겠어요?", set()),
        ("방문 못 받는 날짜는 12월 11일로 정해도 될까요?", {"excluded"}),
        ("12월 14일 방문 못 받는 날로 빼지 마세요", {"excluded"}),
    ],
)
def test_unavailable_day_scope(text, held):
    assert {
        field
        for field in ("start", "end", "excluded")
        if date_set_is_inquiry("period", field, text)
    } == held


@pytest.mark.parametrize(
    "text,risk",
    [
        ("추가 작업 제목은 '확정 도면 보완'으로 적어주시겠어요?", False),
        ("제목은 '확정 도면 보완'으로 써 주실래요?", False),
        ("제목은 '확정 도면 보완'으로 적어도 되나요?", True),
        ("제목은 '확정 도면 보완'으로 안 적어 주시겠어요?", True),
        ("가능하면 제목은 '확정 도면 보완'으로 적어주시겠어요?", True),
        ("제목은 '확정 도면 보완'으로 적어주시겠어요? 그 요청은 취소합니다", True),
    ],
)
def test_polite_text_assignment_not_permission_question(text, risk):
    assert text_set_is_inquiry("title", "확정 도면 보완", text) is risk
