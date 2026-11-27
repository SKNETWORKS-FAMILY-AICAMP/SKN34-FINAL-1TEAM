"""Owned unresolved intent survives producer abstention; unrelated reports do not."""

import pytest
from test_vendor_assist_r6structure_state import exact, fresh, keep, run


@pytest.mark.parametrize("mode", ["rule", "model", "both"])
@pytest.mark.parametrize(
    "form,field,text",
    [
        ("quote", "amount", "견적 금액을 386만원으로 바꿀 수 있을까요?"),
        ("quote", "amount", "견적 금액은 아직 미정입니다."),
        ("dates", "crew", "작업 인원을 5명으로 바꿀 수 있을까요?"),
        ("extra", "title", "항목명을 문틀 보강으로 변경할 수 있을까요?"),
        ("extra", "amount", "추가 금액을 36만원으로 올릴 수 있을까요?"),
        ("dates", "crew", "직전 작업 인원 후보 그대로 적용해 주세요."),
    ],
)
def test_explicit_unresolved_owner_with_no_producer_claim(monkeypatch, mode, form, field, text):
    result, _ = run(monkeypatch, form, text, [keep(field)], mode=mode)
    assert result[field].action == "uncertain"
    assert result[field].status == "uncertain"
    assert result[field].value is None
    assert not result[field].alternatives
    assert all(f.action == "keep" for name, f in result.items() if name != field)


@pytest.mark.parametrize("mode", ["rule", "model", "both"])
@pytest.mark.parametrize("field", ["title", "reason"])
def test_other_field_withdrawal_report_does_not_claim_new_text(monkeypatch, mode, field):
    report = "지난주에 추가 금액 조정 요청을 취소했다고 합니다"
    text = report + ". 부가세는 별도로 지정합니다."
    result, _ = run(
        monkeypatch,
        "extra",
        text,
        [fresh(field, report, report), fresh("vat", "excluded", "부가세는 별도로")],
        mode=mode,
    )
    assert result[field].intent_kind == "no_mention"
    assert result[field].action == "keep"
    assert result[field].value is None
    exact(result["vat"], "excluded")


@pytest.mark.parametrize("mode", ["rule", "model", "both"])
def test_unrelated_unresolved_field_preserves_verified_withdrawal(monkeypatch, mode):
    text = "추가 금액은 아직 미정입니다. 부가세 설정 요청은 철회합니다."
    result, _ = run(monkeypatch, "extra", text, [keep("amount"), keep("vat")], mode=mode)
    assert result["amount"].action == "uncertain"
    assert result["vat"].intent_kind == "retain_current"
    assert result["vat"].action == "keep"
    assert result["title"].intent_kind == result["reason"].intent_kind == "no_mention"
