"""독립 감사의 동결20 대조를 실제 서비스 회귀 테스트로 유지한다."""

import pytest
from test_vendor_assist_r6structure_state import exact, fresh, run, withheld

BASES = {
    "quote": {"amount": 4550000, "vat": "included", "days": 3, "valid": "2026-12-17"},
    "extra": {"amount": 100000, "vat": "included", "title": "보강", "reason": "현장 추가"},
    "dates": {"days": ["2026-12-09", "2026-12-16"], "crew": 2},
    "period": {"start": "2026-12-02", "end": "2026-12-24", "excluded": []},
}

# Worker22's original 20 synthetic controls are preserved in
# work/qa179-r8-worker22-audit.md; no heldout text or gold is used.
CASES = [
    (
        "quote",
        "days",
        "부가세는 아직 고민 중이고 공사 기간은 7일로 지정합니다.",
        7,
        "공사 기간은 7일로 지정합니다",
        True,
    ),
    (
        "quote",
        "valid",
        "부가세는 아직 모르겠고 유효기간은 12월 29일로 지정합니다.",
        "2026-12-29",
        "유효기간은 12월 29일로 지정합니다",
        True,
    ),
    (
        "quote",
        "valid",
        "금액은 아직 보류하고 유효기간은 12월 29일로 확정합니다.",
        "2026-12-29",
        "유효기간은 12월 29일로 확정합니다",
        True,
    ),
    (
        "quote",
        "days",
        "유효기간은 아직 모르겠고 공사 일수는 7일로 확정합니다.",
        7,
        "공사 일수는 7일로 확정합니다",
        True,
    ),
    (
        "quote",
        "vat",
        "공사 일수는 아직 모르겠고 부가세 별도로 확정합니다.",
        "excluded",
        "부가세 별도로 확정합니다",
        True,
    ),
    (
        "extra",
        "title",
        "부가세는 아직 고민 중이고 제목은 문틀 보강으로 지정합니다.",
        "문틀 보강",
        "문틀 보강",
        True,
    ),
    (
        "extra",
        "reason",
        "제목은 아직 모르겠고 사유는 습기 유입으로 지정합니다.",
        "습기 유입",
        "습기 유입",
        True,
    ),
    (
        "dates",
        "days",
        "인원은 아직 고민 중이고 작업일은 12월 10일과 17일로 확정합니다.",
        ["2026-12-10", "2026-12-17"],
        "작업일은 12월 10일과 17일로 확정합니다",
        True,
    ),
    (
        "period",
        "end",
        "시작일은 아직 보류하고 종료일은 12월 29일로 확정합니다.",
        "2026-12-29",
        "종료일은 12월 29일로 확정합니다",
        True,
    ),
    (
        "quote",
        "days",
        "부가세가 포함되는 경우 공사 기간은 7일로 지정합니다.",
        7,
        "공사 기간은 7일로 지정합니다",
        False,
    ),
    (
        "quote",
        "vat",
        "기간은 7일이라면 부가세는 별도로 지정합니다.",
        "excluded",
        "부가세는 별도로 지정합니다",
        False,
    ),
    (
        "extra",
        "title",
        "금액이 35만원이라면 제목은 문틀 보강으로 지정합니다.",
        "문틀 보강",
        "문틀 보강",
        False,
    ),
    (
        "quote",
        "valid",
        "유효기간은 12월 29일로 확정합니다. 그 요청은 취소합니다.",
        "2026-12-29",
        "유효기간은 12월 29일로 확정합니다",
        False,
    ),
    (
        "quote",
        "vat",
        "부가세를 포함할지 고민합니다. 별도로 확정합니다.",
        "excluded",
        "별도로 확정합니다",
        False,
    ),
    (
        "quote",
        "valid",
        "유효기간은 12월 29일로 확정합니다, 자재가 도착하지 않으면 취소합니다.",
        "2026-12-29",
        "유효기간은 12월 29일로 확정합니다",
        False,
    ),
    (
        "quote",
        "vat",
        "부가세는 별도로 지정합니다, 자재가 도착하지 않으면 취소합니다.",
        "excluded",
        "부가세는 별도로 지정합니다",
        False,
    ),
    (
        "extra",
        "reason",
        "항목명은 보류하고 사유는 현장 확인으로 입력합니다.",
        "현장 확인",
        "현장 확인",
        True,
    ),
    (
        "period",
        "start",
        "종료일은 나중에 정할게요, 시작일은 12월 8일로 지정합니다.",
        "2026-12-08",
        "시작일은 12월 8일로 지정합니다",
        True,
    ),
    (
        "quote",
        "valid",
        "부가세는 포함이고 만료일은 12월 29일로 확정합니다.",
        "2026-12-29",
        "만료일은 12월 29일로 확정합니다",
        True,
    ),
    (
        "quote",
        "days",
        "부가세는 포함하지 말고 공사 기간은 7일로 확정합니다.",
        7,
        "공사 기간은 7일로 확정합니다",
        True,
    ),
]


@pytest.mark.parametrize("mode", ["rule", "model", "both"])
@pytest.mark.parametrize("form,field,text,value,evidence,accepted", CASES)
def test_owned_clause_is_preserved(monkeypatch, mode, form, field, text, value, evidence, accepted):
    result, _ = run(
        monkeypatch, form, text, [fresh(field, value, evidence)], mode=mode, current=BASES[form]
    )
    if accepted:
        exact(result[field], value)
    else:
        withheld(result[field])
