"""공통 조건과 어휘 정규화 경로의 독립 대조."""

import pytest
from test_vendor_assist_r6structure_state import exact, fresh, run, withheld

from app.vendor.assist_normalize import crew_replacement


@pytest.mark.parametrize("mode", ["rule", "model", "both"])
@pytest.mark.parametrize(
    "form,text,field,value,evidence",
    [
        ("quote", "승인이 나면 부가세는 별도로 지정합니다.", "vat", "excluded", "부가세는 별도로"),
        (
            "extra",
            "승인이 나면 추가 사유는 '누수 보강'으로 입력합니다.",
            "reason",
            "누수 보강",
            "추가 사유는 '누수 보강'",
        ),
        (
            "extra",
            "승인이 나면 추가금은 18만원으로 지정합니다.",
            "amount",
            180000,
            "추가금은 18만원",
        ),
        ("quote", "승인이 나면 공사 일수는 8일로 지정합니다.", "days", 8, "공사 일수는 8일"),
        (
            "quote",
            "승인이 나면 유효기간은 2027년 1월 21일로 지정합니다.",
            "valid",
            "2027-01-21",
            "유효기간은 2027년 1월 21일",
        ),
        (
            "dates",
            "승인이 나면 작업 날짜는 12월 10일, 12월 17일로 지정합니다.",
            "days",
            ["2026-12-10", "2026-12-17"],
            "작업 날짜는 12월 10일, 12월 17일",
        ),
        (
            "period",
            "승인이 나면 종료일은 12월 28일로 지정합니다.",
            "end",
            "2026-12-28",
            "종료일은 12월 28일",
        ),
    ],
)
def test_dependent_state_is_not_new_assignment(
    monkeypatch, mode, form, text, field, value, evidence
):
    result, _ = run(monkeypatch, form, text, [fresh(field, value, evidence)], mode=mode)
    withheld(result[field])


@pytest.mark.parametrize("mode", ["rule", "model", "both"])
@pytest.mark.parametrize(
    "form,text,field,value,evidence",
    [
        (
            "quote",
            "견적은 삼백팔십만원으로 확정하고 유효기간은 2027년 1월 21일까지로 지정합니다.",
            "amount",
            3800000,
            "삼백팔십만원",
        ),
        (
            "quote",
            "견적은 삼백팔십만원으로 확정하고 공사 일수는 8일로 지정합니다.",
            "amount",
            3800000,
            "삼백팔십만원",
        ),
        (
            "extra",
            "추가 사유는 '화면 확인'으로 지정합니다.",
            "reason",
            "화면 확인",
            "추가 사유는 '화면 확인'",
        ),
        (
            "quote",
            "공사 일수가 확정되면 가능할까요? 견적 금액은 380만원으로 지정합니다.",
            "amount",
            3800000,
            "견적 금액은 380만원",
        ),
    ],
)
def test_independent_written_value_and_literal(
    monkeypatch, mode, form, text, field, value, evidence
):
    result, _ = run(monkeypatch, form, text, [fresh(field, value, evidence)], mode=mode)
    exact(result[field], value)


@pytest.mark.parametrize(
    "text,expected",
    [
        ("작업 날짜 변경은 철회하고 투입할 인원 수는 3명에서 4명으로 변경합니다.", (3, 4)),
        ("작업 날짜 변경은 철회하고 작업자 수는 열 한 명에서 열 두 명으로 변경합니다.", (11, 12)),
        ("작업 날짜 변경은 철회하고 작업자 수는 3명에서 4명으로 변경하지 마세요.", None),
        ("작업 날짜가 확정되면 작업자 수는 3명에서 4명으로 변경합니다.", None),
        ("작업자 수는 3명에서 4명으로 변경합니다. 그 요청은 취소합니다.", None),
    ],
)
def test_crew_lexical_correction_preserves_independent_scope(text, expected):
    result = crew_replacement(text)
    if expected is None:
        assert result is None
    else:
        assert result is not None
        assert (result.old, result.new) == expected
        assert text[slice(*result.old_span)].strip()
        assert text[slice(*result.new_span)].strip()
