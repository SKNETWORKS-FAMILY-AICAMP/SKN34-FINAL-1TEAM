"""공통 철회 증명이 생산자 심사와 최종 상태 전이에 연결되는지 검증."""

import pytest
from test_vendor_assist_r6structure_state import exact, fresh, keep, run


def confirmed(field):
    assert (field.action, field.status, field.intent_kind, field.value_source) == (
        "keep",
        "valid",
        "retain_current",
        "current",
    ), field
    assert field.value is None and not field.alternatives and field.evidence


# Authored controls; the first clause is from the already called stop row.
# No untouched or quarantined sample is used.
@pytest.mark.parametrize("mode", ["rule", "model", "both"])
@pytest.mark.parametrize(
    "form,field,clause,sibling,value,evidence",
    [
        (
            "period",
            "start",
            "시작 날짜 조정은 철회합니다",
            "end",
            "2026-12-28",
            "실측 마감은 12월 28일로 설정합니다",
        ),
        (
            "period",
            "end",
            "마감일 설정 요청은 취소합니다",
            "start",
            "2026-12-03",
            "시작일은 12월 3일로 지정합니다",
        ),
        (
            "quote",
            "amount",
            "견적 금액 확정 요청은 철회합니다",
            "days",
            8,
            "공사 일수는 8일로 지정합니다",
        ),
        (
            "quote",
            "valid",
            "유효기간 조정은 취소합니다",
            "amount",
            3800000,
            "견적 금액은 380만원으로 확정합니다",
        ),
        (
            "quote",
            "days",
            "공사 일수 설정은 철회합니다",
            "vat",
            "excluded",
            "부가세는 별도로 정합니다",
        ),
        (
            "dates",
            "crew",
            "인원 조정 요청은 철회합니다",
            "days",
            ["2026-12-08", "2026-12-18"],
            "작업일은 12월 8일과 18일로 지정합니다",
        ),
        (
            "dates",
            "days",
            "작업일 지정 요청은 취소합니다",
            "crew",
            4,
            "작업 인원은 4명으로 확정합니다",
        ),
        (
            "extra",
            "title",
            "항목명 입력 요청은 철회합니다",
            "amount",
            180000,
            "추가 금액은 18만원으로 확정합니다",
        ),
        (
            "extra",
            "reason",
            "사유 정정은 취소합니다",
            "title",
            "거실 보강",
            "제목은 '거실 보강'으로 지정합니다",
        ),
        (
            "extra",
            "vat",
            "부가세 설정은 철회합니다",
            "amount",
            180000,
            "추가 금액은 18만원으로 확정합니다",
        ),
    ],
)
def test_withdrawal_is_valid_at_review_not_only_recovered_in_merge(
    monkeypatch, mode, form, field, clause, sibling, value, evidence
):
    text = f"{clause}. {evidence}."
    result, trace = run(
        monkeypatch,
        form,
        text,
        [keep(field, clause, "current"), fresh(sibling, value, evidence)],
        mode=mode,
    )
    confirmed(result[field])
    exact(result[sibling], value)
    for key in (
        ["rule_reviewed"]
        if mode == "rule"
        else ["model_reviewed"]
        if mode == "model"
        else ["rule_reviewed", "model_reviewed"]
    ):
        reviewed = next(f for f in trace[key] if f["field"] == field)
        assert (
            reviewed["status"],
            reviewed["action"],
            reviewed["intent_kind"],
            reviewed["value_source"],
        ) == ("valid", "keep", "retain_current", "current")
        assert reviewed["evidence"] in text


@pytest.mark.parametrize("mode", ["rule", "model", "both"])
@pytest.mark.parametrize(
    "form,field,clause",
    [
        ("quote", "amount", "견적 금액 조정은 철회할까요?"),
        ("period", "start", "시작일 조정을 철회하지 마세요."),
        ("dates", "crew", "인원 조정은 승인이 나면 철회합니다."),
        ("extra", "title", "항목명 설정을 철회한다고 말했었습니다."),
        ("quote", "days", "공사 일수 설정은 철회할지 아직 고민 중입니다."),
        ("period", "end", "가능하면 종료일 조정은 취소합니다."),
        ("extra", "amount", "추가 금액 조정은 철회합니다. 추가 금액은 18만원으로 확정합니다."),
    ],
)
def test_uncertain_withdrawal_cannot_create_current_keep(monkeypatch, mode, form, field, clause):
    result, trace = run(monkeypatch, form, clause, [keep(field, clause, "current")], mode=mode)
    assert result[field].action == "uncertain" and result[field].status != "valid"
    for key in (
        ["rule_reviewed"]
        if mode == "rule"
        else ["model_reviewed"]
        if mode == "model"
        else ["rule_reviewed", "model_reviewed"]
    ):
        reviewed = next(f for f in trace[key] if f["field"] == field)
        assert reviewed["status"] != "valid"


def test_misattributed_model_evidence_is_not_approved(monkeypatch):
    clause = "시작일 조정은 철회합니다"
    independent = "종료일은 12월 28일로 지정합니다"
    text = clause + ". " + independent + "."
    result, trace = run(
        monkeypatch,
        "period",
        text,
        [fresh("end", "2026-12-28", independent)],
        mode="both",
        model=[keep("start", independent, "current"), fresh("end", "2026-12-28", independent)],
    )
    reviewed = next(f for f in trace["model_reviewed"] if f["field"] == "start")
    assert reviewed["status"] != "valid"
    # An independently proven real withdrawal may recover the state, but its
    # provenance may never be the forged/other-field model evidence.
    confirmed(result["start"])
    assert result["start"].evidence == clause and result["start"].source == "rule"
    exact(result["end"], "2026-12-28")
