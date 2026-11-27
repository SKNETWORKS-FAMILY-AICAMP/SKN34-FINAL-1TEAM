"""부가세 값·대상·의도 대조: 동결한 합성 사례, 실제 오프라인 서비스."""

import asyncio
import os
import sys
from datetime import date
from pathlib import Path
from uuid import UUID

import pytest

from app.agent import vendor_assist
from app.config import settings
from app.vendor.assist_schemas import (
    AssistContext,
    AssistRequest,
    CorrectionRelation,
    FieldIntent,
    ModelInterpretation,
    PreviousCandidate,
)
from app.vendor.assist_service import interpret

CTX = AssistContext(today=date(2026, 11, 25), base_month=date(2026, 12, 1))
CURRENT = {"amount": 4_550_000, "vat": "included", "days": 3, "valid": "2026-12-17"}
EXTRA = {"amount": 100_000, "vat": "included", "title": "보강", "reason": "현장 확인"}


@pytest.fixture(autouse=True)
def _isolated_app():
    root = os.environ.get("R7VAT_ROOT")
    if root:
        backend = Path(root).resolve() / "backend"
        assert all(
            Path(module.__file__).resolve().is_relative_to(backend)
            for name, module in tuple(sys.modules.items())
            if (name == "app" or name.startswith("app.")) and getattr(module, "__file__", None)
        )


def _set(value, evidence, correction=None):
    return FieldIntent(
        field="vat",
        action="set",
        value=value,
        evidence=evidence,
        intent_kind="explicit_change",
        value_source="utterance",
        correction=correction,
    )


def _run(monkeypatch, form, text, intent, producer, *, previous=None, current=None):
    assert not settings.llm_live
    data = AssistRequest(
        request_id="r7-vat",
        resource_id=UUID(int=179),
        form=form,
        text=text,
        current=current or (CURRENT if form == "quote" else EXTRA),
        rule_fields=[intent] if producer in ("rule", "both") else [],
        previous_candidate=previous,
    )
    calls = []

    async def stub(request, context):
        calls.append(request)
        return ModelInterpretation(fields=[intent])

    monkeypatch.setattr(vendor_assist, "interpret", stub)
    trace = {}
    result = asyncio.run(interpret(data, CTX, use_model=producer != "rule", trace=trace))
    assert len(calls) == int(producer != "rule")
    assert result.source == ("rule" if producer == "rule" else "llm"), trace
    return next(field for field in result.fields if field.field == "vat")


# 실제로 확인된 표현의 구성 요소 및 독립 합성 대조. 미사용 실평가 문장 없음.
@pytest.mark.parametrize("producer", ["rule", "model", "both"])
@pytest.mark.parametrize(
    "form,text,evidence,value,expected",
    [
        (
            "quote",
            "부가세는 따로 청구하는 것으로 선택해 주세요.",
            "따로 청구하는 것으로 선택해 주세요",
            "excluded",
            "excluded",
        ),
        ("quote", "따로 청구하는 것으로 선택", "따로 청구하는 것으로 선택", "excluded", "excluded"),
        (
            "quote",
            "세금 방식은 따로 계산하는 걸로 정해 주세요.",
            "따로 계산하는 걸로",
            "excluded",
            "excluded",
        ),
        (
            "quote",
            "부가세는 따로 부과하는 방식으로 결정합니다.",
            "따로 부과하는 방식",
            "excluded",
            "excluded",
        ),
        ("quote", "부가세는 따로 받아 주세요.", "따로 받아 주세요", "excluded", "excluded"),
        ("quote", "부가가치세는 별도로 확정합니다.", "별도로 확정합니다", "excluded", "excluded"),
        (
            "quote",
            "VAT는 따로 청구하는 것으로 정합니다.",
            "따로 청구하는 것으로",
            "excluded",
            "excluded",
        ),
        (
            "quote",
            "부가세는 포함하지 않는 방식으로 확정합니다.",
            "포함하지 않는 방식",
            "excluded",
            "excluded",
        ),
        ("quote", "부가세는 미포함으로 정합니다.", "미포함", "excluded", "excluded"),
        ("quote", "부가세 포함으로 확정합니다.", "포함", "included", "included"),
        ("quote", "면세로 지정합니다.", "면세", "exempt", "exempt"),
        ("quote", "부가세는 면제입니다.", "부가세는 면제", "exempt", "exempt"),
        ("quote", "철거는 별도 비용으로 청구합니다.", "별도", "excluded", None),
        (
            "quote",
            "자재는 따로 청구하는 것으로 선택합니다.",
            "따로 청구하는 것으로 선택합니다",
            "excluded",
            None,
        ),
        ("quote", "취득세는 별도로 확정합니다.", "별도로 확정합니다", "excluded", None),
        ("quote", "세금계산서는 별도로 발급합니다.", "별도", "excluded", None),
        ("quote", "면세점에서 자재를 구입합니다.", "면세", "exempt", None),
        ("quote", "포함된 자재 목록을 검토합니다.", "포함", "included", None),
        ("quote", "부가세는 따로 청구할까요?", "따로 청구", "excluded", None),
        (
            "quote",
            "부가세는 따로 청구하는 것으로 선택하지 마세요.",
            "따로 청구하는 것으로 선택",
            "excluded",
            None,
        ),
        ("quote", "부가세를 따로 청구한다면 견적은 455만원입니다.", "따로 청구", "excluded", None),
        ("quote", "부가세 포함 또는 따로 청구 중 고민합니다.", "따로 청구", "excluded", None),
        (
            "quote",
            "부가세는 따로 청구하는 것으로 선택합니다. 그 요청은 취소합니다.",
            "따로 청구하는 것으로 선택합니다",
            "excluded",
            None,
        ),
        (
            "quote",
            "부가세는 따로 청구하는 것으로 선택합니다. 기간은 아직 미정입니다.",
            "따로 청구하는 것으로 선택합니다",
            "excluded",
            "excluded",
        ),
        (
            "quote",
            "부가세는 별도입니다. 공사 기간은 아직 미정입니다.",
            "부가세는 별도",
            "excluded",
            "excluded",
        ),
        ("quote", "부가세는 미포함으로 정합니다.", "포함", "included", None),
        (
            "extra",
            "사유: 따로 청구하는 것으로 선택한 자재 비용",
            "따로 청구하는 것으로 선택",
            "excluded",
            None,
        ),
        ("extra", "항목명: 면세점 벽면 수리", "면세", "exempt", None),
        (
            "extra",
            "부가세는 따로 청구하는 것으로 확정합니다.",
            "따로 청구하는 것으로",
            "excluded",
            "excluded",
        ),
    ],
)
def test_r7_vat_service(monkeypatch, producer, form, text, evidence, value, expected):
    item = _run(monkeypatch, form, text, _set(value, evidence), producer)
    if expected is None:
        assert item.action not in ("set", "clear") and not item.alternatives, item
    else:
        assert (item.status, item.action, item.value) == ("valid", "set", expected), item


@pytest.mark.parametrize("producer", ["rule", "model", "both"])
@pytest.mark.parametrize("cancel", [False, True])
def test_r7_vat_verified_correction(monkeypatch, producer, cancel):
    text = "부가세 포함 말고 따로 청구하는 것으로 선택해 주세요."
    if cancel:
        text += " 부가세 변경은 취소합니다."
    relation = CorrectionRelation(
        kind="replace",
        rejected_value="included",
        rejected_evidence="포함",
        accepted_evidence="따로 청구하는 것으로 선택해 주세요",
    )
    item = _run(
        monkeypatch, "quote", text, _set("excluded", relation.accepted_evidence, relation), producer
    )
    if cancel:
        assert item.action not in ("set", "clear") and not item.alternatives, item
    else:
        assert (item.status, item.action, item.value) == ("valid", "set", "excluded"), item


@pytest.mark.parametrize("producer", ["rule", "model", "both"])
@pytest.mark.parametrize("stale", [False, True])
def test_r7_vat_prior_origin(monkeypatch, producer, stale):
    origin = "부가세는 따로 계산하는 방식으로 정합니다."
    previous = PreviousCandidate(
        request_id="origin-vat",
        text=origin,
        fields=[_set("excluded", "따로 계산하는 방식")],
        sources={"vat": "rule"},
        origins={"vat": "origin-vat"},
        baselines={"vat": "exempt" if stale else "included"},
    )
    text = "부가세는 앞서 제안한 방식 그대로 유지해 주세요."
    keep = FieldIntent(
        field="vat",
        action="keep",
        value=None,
        evidence=text,
        intent_kind="retain_previous",
        value_source="previous",
    )
    item = _run(monkeypatch, "quote", text, keep, producer, previous=previous)
    if stale:
        assert item.action not in ("set", "clear") and not item.alternatives, item
    else:
        assert (item.status, item.action, item.value, item.origin_request_id) == (
            "valid",
            "set",
            "excluded",
            "origin-vat",
        ), item


# 별도 동결 후속 대조: 통합 워커가 발견한 관계 보어/일반 부사 범위.
@pytest.mark.parametrize("producer", ["rule", "model", "both"])
@pytest.mark.parametrize(
    "text,evidence,value,expected",
    [
        (
            "부가세는 따로 확인하고 철거비는 별도로 청구합니다.",
            "따로 확인하고",
            "excluded",
            None,
        ),
        (
            "부가세는 별도 확인하고 철거비는 따로 청구합니다.",
            "별도 확인하고",
            "excluded",
            None,
        ),
        ("부가세는 따로 확인해 주세요.", "따로", "excluded", None),
        ("부가세는 따로 처리해 주세요.", "따로", "excluded", "excluded"),
        (
            "부가세는 금액과 별개로 청구해 주세요.",
            "금액과 별개로 청구",
            "excluded",
            "excluded",
        ),
        (
            "부가세는 포함입니다. 철거비는 별도로 청구합니다.",
            "포함",
            "included",
            "included",
        ),
    ],
)
def test_r7_vat_ownership_followup(monkeypatch, producer, text, evidence, value, expected):
    item = _run(monkeypatch, "quote", text, _set(value, evidence), producer)
    if expected is None:
        assert item.action not in ("set", "clear") and not item.alternatives, item
    else:
        assert (item.status, item.action, item.value) == ("valid", "set", expected), item


# 청구 명사와 대입 지시의 조합: 별도 후보, 다른 비용, 문의/조건을 대조한다.
@pytest.mark.parametrize("producer", ["rule", "model", "both"])
@pytest.mark.parametrize(
    "text,evidence,expected",
    [
        ("부가세 별도 청구로 지정 바랍니다.", "별도 청구로 지정 바랍니다", "excluded"),
        ("세금 방식은 별도 계산으로 설정 바랍니다.", "별도 계산으로 설정 바랍니다", "excluded"),
        ("철거비는 별도 청구로 지정 바랍니다.", "별도 청구로 지정 바랍니다", None),
        ("부가세 별도 청구로 지정할까요?", "별도 청구로 지정할까요", None),
    ],
)
def test_r7_vat_billing_nominal(monkeypatch, producer, text, evidence, expected):
    item = _run(monkeypatch, "quote", text, _set("excluded", evidence), producer)
    if expected is None:
        assert item.action not in ("set", "clear") and not item.alternatives, item
    else:
        assert (item.status, item.action, item.value) == ("valid", "set", expected), item
