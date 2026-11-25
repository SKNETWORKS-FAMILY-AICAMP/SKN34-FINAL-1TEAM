"""공통 절 소유권이 실제 후보 검증까지 유지되는지 검사한다."""

import pytest
from test_vendor_assist_r6structure_state import exact, fresh, run, withheld

# 모든 원문은 이미 호출된 사례 또는 별도로 작성한 합성 대조군이다.
# 미사용 독립 평가 문장/정답은 사용하지 않는다.
POSITIVE = [
    (
        "quote",
        "유효기간 수정은 없던 일로 하고 견적 가격은 354만 원으로 확정해 주세요.",
        "amount",
        3540000,
        "견적 가격은 354만 원으로 확정",
    ),
    (
        "quote",
        "유효기간 변경은 철회하고 견적 총액은 385만 원으로 지정합니다.",
        "amount",
        3850000,
        "견적 총액은 385만 원",
    ),
    (
        "quote",
        "공사 일수는 미정이라고 하고 공사 비용은 397만 원으로 입력합니다.",
        "amount",
        3970000,
        "공사 비용은 397만 원",
    ),
    (
        "quote",
        "공사 기간 변경은 취소하고 견적 가격은 삼백팔십만원으로 확정합니다.",
        "amount",
        3800000,
        "삼백팔십만원",
    ),
    (
        "quote",
        "유효기간 수정은 철회하고 공사 소요일수는 8일로 확정합니다.",
        "days",
        8,
        "공사 소요일수는 8일",
    ),
    (
        "quote",
        "견적 금액은 385만원으로 지정합니다. 세금은 별도인지 모르겠어요.",
        "amount",
        3850000,
        "견적 금액은 385만원",
    ),
    (
        "quote",
        "견적 가격 변경은 취소하고 부가세 조건은 별도로 지정합니다.",
        "vat",
        "excluded",
        "부가세 조건은 별도로",
    ),
    (
        "quote",
        "공사 기간은 변경하지 마세요. 유효기간은 2027년 1월 21일로 지정합니다.",
        "valid",
        "2027-01-21",
        "유효기간은 2027년 1월 21일",
    ),
    (
        "dates",
        "작업 날짜 변경은 철회하고 투입할 인원 수는 4명으로 확정합니다.",
        "crew",
        4,
        "투입할 인원 수는 4명",
    ),
    (
        "dates",
        "작업 일정은 아직 미정이라고 하고 작업자 수는 3명으로 지정합니다.",
        "crew",
        3,
        "작업자 수는 3명",
    ),
    (
        "dates",
        "작업자 변경은 취소하고 작업 날짜는 12월 10일, 12월 17일로 지정합니다.",
        "days",
        ["2026-12-10", "2026-12-17"],
        "작업 날짜는 12월 10일, 12월 17일",
    ),
    (
        "period",
        "제외일 목록 변경은 철회하고 방문 가능한 마감일은 12월 28일로 지정합니다.",
        "end",
        "2026-12-28",
        "마감일은 12월 28일",
    ),
    (
        "period",
        "종료일 변경은 취소하고 제외일 목록은 12월 12일, 12월 20일로 지정합니다.",
        "excluded",
        ["2026-12-12", "2026-12-20"],
        "제외일 목록은 12월 12일, 12월 20일",
    ),
    (
        "extra",
        "추가 사유 수정은 철회하고 추가 공사 금액은 18만원으로 확정합니다.",
        "amount",
        180000,
        "추가 공사 금액은 18만원",
    ),
    (
        "extra",
        "항목명 수정은 없던 일로 하고 추가 공사 비용은 십팔만원으로 지정합니다.",
        "amount",
        180000,
        "십팔만원",
    ),
    (
        "extra",
        "비용 변경은 취소하고 추가 사유는 '기존 몰딩 균열'로 지정합니다.",
        "reason",
        "기존 몰딩 균열",
        "추가 사유는 '기존 몰딩 균열'",
    ),
    (
        "extra",
        "부가세 변경은 철회하고 항목 제목은 '거실 보강'으로 지정합니다.",
        "title",
        "거실 보강",
        "항목 제목은 '거실 보강'",
    ),
]

NEGATIVE = [
    (
        "quote",
        "견적 가격 수정은 없던 일로 하고 견적 가격은 354만 원으로 확정해 주세요.",
        "amount",
        3540000,
        "견적 가격은 354만 원으로 확정",
    ),
    (
        "quote",
        "가능하면 유효기간 수정은 취소하고 견적 가격은 354만원으로 확정해 주세요.",
        "amount",
        3540000,
        "견적 가격은 354만원",
    ),
    (
        "quote",
        "유효기간 수정은 취소하고 견적 가격은 354만원으로 확정하지 마세요.",
        "amount",
        3540000,
        "견적 가격은 354만원",
    ),
    (
        "quote",
        "그 변경은 취소하고 견적 가격은 354만원으로 확정해 주세요.",
        "amount",
        3540000,
        "견적 가격은 354만원",
    ),
    (
        "quote",
        "유효기간 수정은 취소하고 견적 가격은 354만원 또는 360만원으로 고민 중입니다.",
        "amount",
        3540000,
        "견적 가격은 354만원",
    ),
    (
        "quote",
        "견적 가격은 354만원으로 확정합니다. 그 요청은 철회합니다.",
        "amount",
        3540000,
        "견적 가격은 354만원",
    ),
    (
        "quote",
        "유효기간 수정은 취소하고 공사 소요일수는 8일 정도로 생각합니다.",
        "days",
        8,
        "공사 소요일수는 8일",
    ),
    (
        "quote",
        "견적 가격 변경은 취소하고 부가세 조건은 별도로 할까요?",
        "vat",
        "excluded",
        "부가세 조건은 별도로",
    ),
    (
        "dates",
        "작업 날짜 변경은 철회하고 투입할 인원 수는 4명으로 변경하지 마세요.",
        "crew",
        4,
        "투입할 인원 수는 4명",
    ),
    (
        "dates",
        "작업 날짜가 확정되면 투입할 인원 수는 4명으로 지정합니다.",
        "crew",
        4,
        "투입할 인원 수는 4명",
    ),
    (
        "dates",
        "투입할 인원 수는 4명으로 지정합니다. 그 요청은 철회합니다.",
        "crew",
        4,
        "투입할 인원 수는 4명",
    ),
    (
        "dates",
        "작업자 변경은 취소하고 작업 날짜는 12월 10일, 12월 17일로 할까요?",
        "days",
        ["2026-12-10", "2026-12-17"],
        "작업 날짜는 12월 10일, 12월 17일",
    ),
    (
        "period",
        "제외일 목록 변경은 철회하고 방문 가능한 마감일은 12월 28일 또는 30일로 고민 중입니다.",
        "end",
        "2026-12-28",
        "마감일은 12월 28일",
    ),
    (
        "period",
        "제외일 목록 변경은 취소하고 마감일은 12월 18일로 지정합니다.",
        "end",
        "2026-12-18",
        "마감일은 12월 18일",
    ),
    (
        "extra",
        "추가 사유 수정은 철회하고 추가 공사 금액은 18만원으로 넣지 마세요.",
        "amount",
        180000,
        "추가 공사 금액은 18만원",
    ),
    (
        "extra",
        "부가세 변경은 철회하고 항목 제목은 '거실 보강'으로 지정할까요?",
        "title",
        "거실 보강",
        "항목 제목은 '거실 보강'",
    ),
    (
        "extra",
        "추가 사유는 '보강 검토'로 지정해 주세요. 추가 사유 변경은 취소합니다.",
        "reason",
        "보강 검토",
        "추가 사유는 '보강 검토'",
    ),
]


@pytest.mark.parametrize("mode", ["rule", "model", "both"])
@pytest.mark.parametrize("form,text,field,value,evidence", POSITIVE)
def test_independent_clause_survives(monkeypatch, mode, form, text, field, value, evidence):
    fields, _ = run(monkeypatch, form, text, [fresh(field, value, evidence)], mode=mode)
    exact(fields[field], value)


@pytest.mark.parametrize("mode", ["rule", "model", "both"])
@pytest.mark.parametrize("form,text,field,value,evidence", NEGATIVE)
def test_own_or_unbound_risk_is_not_erased(monkeypatch, mode, form, text, field, value, evidence):
    fields, _ = run(monkeypatch, form, text, [fresh(field, value, evidence)], mode=mode)
    withheld(fields[field])


@pytest.mark.parametrize("mode", ["rule", "model", "both"])
def test_clipped_value_cannot_use_other_field_grounding(monkeypatch, mode):
    text = "공사 일수는 8일로 지정하고 유효기간은 아직 미정입니다."
    fields, _ = run(
        monkeypatch,
        "quote",
        text,
        [fresh("valid", "2026-12-08", "8일")],
        mode=mode,
    )
    withheld(fields["valid"])
