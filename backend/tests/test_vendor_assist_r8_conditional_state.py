"""조건부 실행을 값 생산자가 생략해도 명시적 보류 상태로 유지한다."""

import pytest
from test_vendor_assist_r6structure_state import exact, fresh, run


@pytest.mark.parametrize("mode", ["rule", "model", "both"])
@pytest.mark.parametrize(
    "form,text,targets",
    [
        (
            "quote",
            "현장이 준비되면 견적 금액은 380만원으로, 일수는 8일로 변경합니다.",
            {"amount", "days"},
        ),
        (
            "dates",
            "고객이 확인하면 작업일은 12월 8일과 18일로, 인원은 4명으로 정합니다.",
            {"days", "crew"},
        ),
        (
            "extra",
            "승인이 나면 추가 금액은 18만원으로, 부가세는 별도로 정합니다.",
            {"amount", "vat"},
        ),
        (
            "period",
            "일정이 확정되면 시작일은 12월 3일로, 종료일은 12월 28일로 변경합니다.",
            {"start", "end"},
        ),
    ],
)
def test_no_claim_does_not_hide_dependent_target(monkeypatch, mode, form, text, targets):
    result, _ = run(monkeypatch, form, text, [], mode=mode)
    for field, candidate in result.items():
        if field in targets:
            assert (candidate.action, candidate.status, candidate.value) == (
                "uncertain",
                "uncertain",
                None,
            )
            assert not candidate.alternatives
        else:
            assert candidate.action == "keep" and candidate.intent_kind == "no_mention"


@pytest.mark.parametrize("mode", ["rule", "model", "both"])
def test_completed_question_does_not_hold_independent_assignment(monkeypatch, mode):
    text = "공사 일수가 확정되면 가능할까요? 견적 금액은 380만원으로 확정합니다."
    result, _ = run(monkeypatch, "quote", text, [fresh("amount", 3800000, "380만원")], mode=mode)
    exact(result["amount"], 3800000)


@pytest.mark.parametrize("mode", ["rule", "model", "both"])
def test_quoted_condition_is_not_an_instruction(monkeypatch, mode):
    text = "추가 사유는 '승인이 나면'으로 기록합니다. 추가 금액은 18만원으로 확정합니다."
    result, _ = run(
        monkeypatch,
        "extra",
        text,
        [fresh("reason", "승인이 나면", "'승인이 나면'"), fresh("amount", 180000, "18만원")],
        mode=mode,
    )
    exact(result["amount"], 180000)
