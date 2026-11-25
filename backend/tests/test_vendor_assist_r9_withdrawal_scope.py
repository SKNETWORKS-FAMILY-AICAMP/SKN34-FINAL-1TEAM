"""확장된 행위 객체가 무대상 업무 취소나 인용값으로 전파되지 않는다."""

import pytest
from test_vendor_assist_r6structure_state import exact, fresh, run

from app.vendor.assist_validation import _pending_cancellation


@pytest.mark.parametrize(
    "form,field,text",
    [
        ("quote", "amount", "등록 취소합니다."),
        ("period", "start", "조정안을 철회합니다."),
        ("dates", "crew", "배치 취소합니다."),
        ("extra", "title", "입력 취소합니다."),
    ],
)
def test_new_operation_noun_requires_real_field_owner(form, field, text):
    proof = _pending_cancellation(form, field, text)
    assert proof is None or not proof[1]


@pytest.mark.parametrize(
    "form,field", [("quote", "amount"), ("period", "start"), ("dates", "crew"), ("extra", "title")]
)
def test_existing_generic_candidate_withdrawal_stays_supported(form, field):
    assert _pending_cancellation(form, field, "변경 요청을 취소합니다.") == (
        "변경 요청을 취소합니다",
        True,
    )


@pytest.mark.parametrize("mode", ["rule", "model", "both"])
@pytest.mark.parametrize("field", ["title", "reason"])
def test_other_field_report_is_not_implicit_text_assignment(monkeypatch, mode, field):
    value = "지난주에는 추가 금액 변경 요청을 철회했다고 합니다"
    text = value + ". 부가세는 별도로 청구해 주세요."
    result, _ = run(
        monkeypatch,
        "extra",
        text,
        [fresh(field, value, value), fresh("vat", "excluded", "부가세는 별도로")],
        mode=mode,
    )
    assert result[field].action not in ("set", "clear")
    exact(result["vat"], "excluded")


@pytest.mark.parametrize("mode", ["rule", "model", "both"])
def test_explicit_literal_withdrawal_word_is_a_value(monkeypatch, mode):
    text = "제목은 '취소된 공사 보수'로 지정합니다."
    result, _ = run(
        monkeypatch, "extra", text, [fresh("title", "취소된 공사 보수", text)], mode=mode
    )
    exact(result["title"], "취소된 공사 보수")
    assert _pending_cancellation("extra", "title", text) is None
