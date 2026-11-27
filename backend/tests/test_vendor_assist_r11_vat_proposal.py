"""VAT 후보 내용 인용형: 기존 실제 실패와 독립 정상/위험 대조."""

import asyncio
from datetime import date
from uuid import UUID

import pytest

from app.agent import llm, vendor_assist
from app.config import settings
from app.vendor.assist_schemas import AssistContext, AssistRequest, FieldIntent, ModelInterpretation
from app.vendor.assist_service import interpret

CTX = AssistContext(today=date(2026, 11, 25), base_month=date(2027, 1, 1))
CURRENT = {
    "quote": {"amount": 3260000, "vat": "included", "days": 4, "valid": "2027-01-27"},
    "extra": {"title": "기존 보강", "reason": "기존 손상", "amount": 250000, "vat": "included"},
}

# 실제 호출된 실패 원문만 사용. 다른 문장은 독립 합성 대조군.
CASES = [
    ("extra", "추가 금액은 84만원, 부가세는 별도라는 후보를 만들어 주세요.", "부가세는 별도", True),
    ("quote", "부가세는 별도라는 후보안을 작성해 주세요.", "별도", True),
    ("extra", "세금 조건은 별도라는 초안을 보여 주세요.", "세금 조건은 별도", True),
    ("quote", "부가세 구분은 별도라는 제안안을 만들어 주세요.", "부가세 구분은 별도", True),
    ("extra", "가능하면 부가세는 별도라는 후보를 만들어 주세요.", "부가세는 별도", False),
    ("quote", "부가세는 별도라는 후보를 만들어도 될까요?", "부가세는 별도", False),
    ("extra", "부가세는 별도라는 후보를 만들지 마세요.", "부가세는 별도", False),
    ("quote", "부가세는 별도라는 후보를 검토해 주세요.", "부가세는 별도", False),
    (
        "extra",
        "부가세는 별도라는 후보를 만들어 주세요. 부가세 변경은 취소합니다.",
        "부가세는 별도",
        False,
    ),
    (
        "quote",
        "부가세는 별도라는 후보를 만들어 주세요. 부가세는 포함으로 확정해 주세요.",
        "부가세는 별도",
        False,
    ),
    ("extra", "사유는 별도라는 후보를 만들어 주세요.", "별도", False),
    (
        "extra",
        '제목은 "부가세는 별도라는 후보를 만들어 주세요"로 적어 주세요.',
        "부가세는 별도",
        False,
    ),
    ("quote", "운반비는 별도라는 후보를 만들어 주세요.", "별도", False),
    ("quote", "별도라는 후보를 만들어 주세요.", "별도", False),
]


@pytest.mark.parametrize("producer", ["rule", "model"])
@pytest.mark.parametrize("form,text,evidence,accepted", CASES)
def test_vat_proposal_content(monkeypatch, producer, form, text, evidence, accepted):
    assert not settings.llm_live
    candidate = FieldIntent(
        field="vat",
        action="set",
        value="excluded",
        evidence=evidence,
        intent_kind="explicit_change",
        value_source="utterance",
    )
    request = AssistRequest(
        request_id="r11-vat-proposal",
        resource_id=UUID(int=179),
        form=form,
        text=text,
        current=CURRENT[form],
        previous_candidate=None,
        rule_fields=[candidate] if producer == "rule" else [],
    )

    async def forbidden(*args, **kwargs):
        raise AssertionError("external provider forbidden")

    async def stub(*args, **kwargs):
        return ModelInterpretation(fields=[candidate])

    monkeypatch.setattr(llm, "structured", forbidden)
    monkeypatch.setattr(vendor_assist, "interpret", stub if producer == "model" else forbidden)
    result = asyncio.run(interpret(request, CTX, use_model=producer == "model"))
    vat = next(f for f in result.fields if f.field == "vat")
    if accepted:
        assert (vat.status, vat.action, vat.value) == ("valid", "set", "excluded"), vat
    else:
        assert not (vat.status == "valid" and vat.action == "set" and vat.value == "excluded"), vat
        assert not any(a.action == "set" and a.value == "excluded" for a in vat.alternatives)
