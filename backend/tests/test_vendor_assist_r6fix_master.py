"""R6 오프라인 통합: 견적 일수의 전체 근거, 경쟁 지시 및 이력 안전성."""

import asyncio
from datetime import date
from uuid import UUID

import pytest

from app.agent import vendor_assist
from app.config import settings
from app.vendor.assist_schemas import (
    AssistContext,
    AssistRequest,
    FieldIntent,
    ModelInterpretation,
    PreviousCandidate,
)
from app.vendor.assist_service import interpret

CTX = AssistContext(today=date(2026, 11, 25), base_month=date(2026, 12, 1))
CURRENT = {"amount": 4550000, "days": 3, "vat": "included", "valid": "2026-12-17"}


def _set(field, value, evidence):
    return FieldIntent(field=field, action="set", value=value, evidence=evidence)


def _run(monkeypatch, text, fields, mode, *, previous=None, current=None, request_id="new"):
    assert not settings.llm_live
    model = [
        f.model_copy(update={"intent_kind": "explicit_change", "value_source": "utterance"})
        if f.action == "set"
        else f
        for f in fields
    ]

    async def stub(data, context):
        return ModelInterpretation(fields=model)

    monkeypatch.setattr(vendor_assist, "interpret", stub)
    request = AssistRequest(
        form="quote",
        resource_id=UUID(int=179),
        request_id=request_id,
        current=CURRENT if current is None else current,
        text=text,
        rule_fields=fields if mode != "model" else [],
        previous_candidate=previous,
    )
    response = asyncio.run(interpret(request, CTX, use_model=mode != "rule"))
    return request, {f.field: f for f in response.fields}


def _accepted(field, value):
    assert (field.status, field.action, field.value) == ("valid", "set", value)


def _blocked(field):
    assert not (field.status == "valid" and field.action in ("set", "clear"))
    assert not field.alternatives


@pytest.mark.parametrize("mode", ["rule", "model", "both"])
@pytest.mark.parametrize(
    ("word", "value"),
    [("하루", 1), ("사흘", 3), ("닷새", 5), ("엿새", 6), ("여드레", 8), ("열흘", 10)],
)
def test_native_duration_independent_of_calendar(monkeypatch, mode, word, value):
    tax_date = "견적 유효기간은 12월 23일까지로 확정합니다"
    duration = f"시공 기간은 {word}로 정해주세요"
    _, fields = _run(
        monkeypatch,
        tax_date + ". " + duration,
        [_set("valid", "2026-12-23", tax_date), _set("days", value, duration)],
        mode,
    )
    _accepted(fields["days"], value)
    _accepted(fields["valid"], "2026-12-23")


@pytest.mark.parametrize("mode", ["rule", "model", "both"])
@pytest.mark.parametrize(
    ("text", "evidence", "value"),
    [
        ("공사 기간은 닷새 정도 예상합니다", "닷새", 5),
        ("공사 기간은 닷새 또는 엿새 중 고민 중입니다", "닷새", 5),
        ("작업 일수는 닷새로 잡지 마세요", "닷새", 5),
        ("공사 일수 닷새로 확정합니다. 그 요청은 취소합니다", "닷새", 5),
        ("공사 기간은 3일~5일로 잡겠습니다", "5일", 5),
        ("공사 기간은 닷새 안으로 잡아주세요", "닷새", 5),
        ("공사 기간은 닷새면 될까요?", "닷새", 5),
        ("가능하면 공사 일수를 닷새로 잡아주세요", "닷새", 5),
        ("유효기간은 12월 5일까지로 확정합니다", "5일", 5),
        ("견적 효력은 닷새간 유지합니다", "닷새", 5),
        ("시공 기간은 2.5일로 적어주세요", "5일", 5),
        ("공사 일수는 닷새로 할게요. 작업 기간은 엿새로 해주세요", "닷새", 5),
    ],
)
def test_duration_not_speculative_or_clipped(monkeypatch, mode, text, evidence, value):
    _, fields = _run(monkeypatch, text, [_set("days", value, evidence)], mode)
    _blocked(fields["days"])


@pytest.mark.parametrize("mode", ["rule", "model", "both"])
def test_duration_and_independent_inquiry(monkeypatch, mode):
    text = "시공은 닷새로 잡아주세요. 견적 효력을 12월 24일까지 둬도 되나요?"
    _, fields = _run(
        monkeypatch,
        text,
        [_set("days", 5, "닷새"), _set("valid", "2026-12-24", "12월 24일")],
        mode,
    )
    _accepted(fields["days"], 5)
    _blocked(fields["valid"])


@pytest.mark.parametrize("mode", ["rule", "model", "both"])
def test_duration_prior_value_and_origin(monkeypatch, mode):
    seed_text = "시공 기간은 엿새로 확정합니다"
    req, seed = _run(monkeypatch, seed_text, [_set("days", 6, seed_text)], mode, request_id="seed")
    old = seed["days"]
    _accepted(old, 6)
    prior = PreviousCandidate(
        request_id=req.request_id,
        text=req.text,
        fields=[
            FieldIntent(
                field="days",
                action="set",
                value=6,
                evidence=old.evidence,
                value_source=old.value_source,
                correction=old.correction,
            )
        ],
        sources={"days": old.source},
        origins={"days": old.origin_request_id},
        baselines={"days": CURRENT["days"]},
    )
    follow = "부가세는 별도 처리로 확정합니다. 시공 기간은 앞서 정한 그대로 유지해 주세요"
    _, fields = _run(
        monkeypatch,
        follow,
        [_set("vat", "excluded", "부가세는 별도 처리로 확정합니다")],
        mode,
        previous=prior,
    )
    _accepted(fields["vat"], "excluded")
    _accepted(fields["days"], 6)
    assert fields["days"].origin_request_id == "seed"
    assert fields["days"].source == old.source
    assert fields["days"].evidence == old.evidence
    for text, current in [
        ("시공 기간은 앞서 정한 그대로 유지해 주세요", {**CURRENT, "days": 4}),
        ("시공 기간은 앞서 정한 그대로 유지할까요?", CURRENT),
        ("시공 기간 변경은 취소합니다", CURRENT),
    ]:
        _, rejected = _run(monkeypatch, text, [], mode, previous=prior, current=current)
        _blocked(rejected["days"])
