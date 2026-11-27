"""인원 범위와 검증된 이전 인원의 명시적 정정을 구분한다."""

import asyncio
from datetime import date
from uuid import UUID

import pytest

from app.agent import vendor_assist
from app.vendor import assist_service
from app.vendor.assist_schemas import (
    AssistContext,
    AssistRequest,
    CorrectionRelation,
    FieldIntent,
    ModelInterpretation,
    PreviousCandidate,
)

CTX = AssistContext(today=date(2026, 11, 23), crew_max=6)


def prior(value=5, baseline=3, text="인원은 다섯 명으로 잡아 주세요"):
    return PreviousCandidate(
        request_id="prior",
        text=text,
        fields=[FieldIntent(field="crew", action="set", value=value, evidence=text)],
        baselines={"crew": baseline},
        sources={"crew": "llm"},
        origins={"crew": "original"},
    )


def run(monkeypatch, text, old, new, rejected, accepted, previous, current=3, evidence=None):
    data = AssistRequest(
        request_id="new",
        resource_id=UUID("00000000-0000-4000-8000-000000000179"),
        form="dates",
        text=text,
        current={"days": [], "crew": current},
        previous_candidate=previous,
        rule_fields=[FieldIntent(field="crew", action="keep", value=None, evidence="")],
    )
    item = FieldIntent(
        field="crew",
        action="set",
        value=new,
        evidence=evidence or text,
        intent_kind="explicit_change",
        value_source="utterance",
        correction=CorrectionRelation(
            kind="replace",
            rejected_value=old,
            rejected_evidence=rejected,
            accepted_evidence=accepted,
        ),
    )

    async def model(*args):
        return ModelInterpretation(fields=[item])

    monkeypatch.setattr(vendor_assist, "interpret", model)
    trace = {}
    result = asyncio.run(assist_service.interpret(data, CTX, trace=trace))
    return next(field for field in result.fields if field.field == "crew"), trace


@pytest.mark.parametrize(
    "text,old,new,rejected,accepted,previous",
    [
        (
            "일정은 전대로 하고, 인원만 다섯에서 네 명으로 정정할게요.",
            5,
            4,
            "다섯",
            "네 명",
            prior(),
        ),
        ("작업 인원은 2명에서 6명으로 바꿔 주세요", 2, 6, "2명", "6명", prior(2, text="인원 2명")),
        (
            "인원은 여섯에서 한 명으로 수정해 주세요",
            6,
            1,
            "여섯",
            "한 명",
            prior(6, text="인원 여섯 명"),
        ),
    ],
)
def test_verified_previous_count_is_corrected(
    monkeypatch, text, old, new, rejected, accepted, previous
):
    result, trace = run(monkeypatch, text, old, new, rejected, accepted, previous)
    assert (result.action, result.status, result.value) == ("set", "valid", new)
    assert result.origin_request_id == "new"
    assert result.source == "llm"
    assert result.correction is not None and result.correction.rejected_value == old
    assert trace["previous_verified"]["fields"][0]["value"] == old


@pytest.mark.parametrize(
    "text,old,new,rejected,accepted,previous,current",
    [
        ("인원만 다섯에서 네 명으로 정정할게요", 5, 4, "다섯", "네 명", None, 3),
        ("인원만 다섯에서 네 명으로 정정할게요", 5, 4, "다섯", "네 명", prior(), 2),
        (
            "인원만 다섯에서 네 명으로 정정할게요",
            5,
            4,
            "다섯",
            "네 명",
            prior(6, text="인원 여섯 명"),
            3,
        ),
        (
            "인원만 다섯에서 네 명으로 정정할게요",
            5,
            4,
            "다섯",
            "네 명",
            prior(5, text="인원은 4명"),
            3,
        ),
        ("인원만 다섯에서 네 명으로 정정할게요", 5, 4, "다", "네 명", prior(), 3),
        ("인원만 다섯에서 네 명으로 정정할게요", 5, 4, "다섯", "네", prior(), 3),
        ("인원만 다섯에서 네 명으로 정정할게요", 5, 5, "다섯", "네 명", prior(), 3),
        ("인원은 다섯에서 일곱 명으로 수정해 주세요", 5, 7, "다섯", "일곱 명", prior(), 3),
        ("인원 5에서 4명 정도", 5, 4, "5", "4명", prior(), 3),
        ("인원은 5~4명", 5, 4, "5", "4명", prior(), 3),
        ("인원은 다섯에서 네 명으로 정정할까요?", 5, 4, "다섯", "네 명", prior(), 3),
        (
            "인원은 다섯에서 네 명으로 정정할게요. 다시 보류할게요",
            5,
            4,
            "다섯",
            "네 명",
            prior(),
            3,
        ),
        ("인원은 5.5에서 4명으로 정정할게요", 5, 4, "5", "4명", prior(), 3),
        ("인원은 1,005에서 4명으로 정정할게요", 5, 4, "005", "4명", prior(), 3),
    ],
)
def test_unverified_or_ambiguous_count_is_held(
    monkeypatch, text, old, new, rejected, accepted, previous, current
):
    result, _ = run(monkeypatch, text, old, new, rejected, accepted, previous, current)
    assert result.action == "uncertain"
    assert result.value is None
    assert result.status != "valid"


def test_corrected_count_can_be_reverified_without_scalar_wire_metadata(monkeypatch):
    history = "인원 5명\n인원만 다섯에서 네 명으로 정정할게요"
    previous = prior(4, text=history)
    previous.fields[0].evidence = history.splitlines()[-1]
    result, trace = run(
        monkeypatch,
        "인원은 네 명에서 두 명으로 변경해 주세요",
        4,
        2,
        "네 명",
        "두 명",
        previous,
    )
    assert result.value == 2 and result.status == "valid"
    assert trace["previous_verified"]["fields"][0]["value"] == 4
