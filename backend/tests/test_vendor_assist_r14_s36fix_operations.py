"""R14 developer controls: owned fields, operations and bound current values."""

import asyncio
from datetime import date
from uuid import UUID

import pytest

from app.agent import vendor_assist
from app.vendor.assist_intent_safety import text_value_scope_ok
from app.vendor.assist_schemas import (
    AssistContext,
    AssistRequest,
    FieldIntent,
    ModelInterpretation,
)
from app.vendor.assist_service import interpret


def _request(form, text, current, rules):
    return AssistRequest(
        request_id="r14-s36",
        resource_id=UUID(int=179),
        form=form,
        text=text,
        current=current,
        previous_candidate=None,
        rule_fields=rules,
    )


def _run(monkeypatch, form, text, current, rules, model):
    async def stub(*args, **kwargs):
        return ModelInterpretation(fields=model)

    monkeypatch.setattr(vendor_assist, "interpret", stub)
    trace = {}
    result = asyncio.run(
        interpret(
            _request(form, text, current, rules),
            AssistContext(today=date(2026, 11, 26), base_month=date(2027, 2, 1)),
            use_model=bool(model),
            trace=trace,
        )
    )
    return {x.field: x for x in result.fields}, trace


@pytest.mark.parametrize(
    "label",
    ("추가 작업을 하게 된 경위란에", "사유 항목에는", "추가 공사 이유란에", "추가 사유에"),
)
def test_quoted_reason_has_one_owner(monkeypatch, label):
    text = f"{label} '전선 위치 조정'이라고 남겨 주십시오."
    value = "전선 위치 조정"
    assert text_value_scope_ok("reason", value, text)
    assert not text_value_scope_ok("title", value, text)
    bad = FieldIntent(field="title", action="set", value=text.rstrip("."), evidence=text)
    good = FieldIntent(field="reason", action="set", value=value, evidence=text)
    result, _ = _run(
        monkeypatch,
        "extra",
        text,
        {"title": "기존 제목", "reason": "기존 사유", "amount": 100000, "vat": "included"},
        [bad],
        [good],
    )
    assert result["title"].action != "set"
    assert (result["reason"].action, result["reason"].value, result["reason"].status) == (
        "set",
        value,
        "valid",
    )


def test_called_safety_case_preserves_correct_reason(monkeypatch):
    text = "추가 작업을 하게 된 경위란에 '매립 배선 위치 변경'이라고 남겨 주십시오."
    bad = FieldIntent(
        field="title",
        action="set",
        value="하게 된 경위란에 '매립 배선 위치 변경'이라고 남겨 주십시오",
        evidence=text,
    )
    good = FieldIntent(
        field="reason",
        action="set",
        value="매립 배선 위치 변경",
        evidence="경위란에 '매립 배선 위치 변경'이라고 남겨",
        intent_kind="explicit_change",
        value_source="utterance",
    )
    result, _ = _run(
        monkeypatch,
        "extra",
        text,
        {"title": "기존 제목", "reason": "기존 사유", "amount": 100000, "vat": "included"},
        [bad],
        [FieldIntent(field="title", action="keep", value=None, evidence=""), good],
    )
    assert result["title"].action != "set"
    assert result["reason"].value == good.value and result["reason"].status == "valid"


@pytest.mark.parametrize(
    "form,field,text,base,operand,target",
    [
        ("quote", "days", "기존 작업 일수에서 사흘 더 늘려 주시겠어요?", 5, 3, 8),
        ("quote", "days", "일정을 이틀 단축해서 견적 공사 일수를 조정해 주세요.", 9, 2, 7),
        ("dates", "crew", "지금 인원에 두 명 더 추가해서 보내 주세요.", 3, 2, 5),
        ("dates", "crew", "현재 투입 인원에서 한 명 줄여 주세요.", 4, 1, 3),
        ("extra", "amount", "현재 추가 공사비에서 7만원만 빼 주세요.", 780000, 70000, 710000),
    ],
)
@pytest.mark.parametrize("mode", ["rule", "model", "both"])
def test_bound_delta(monkeypatch, form, field, text, base, operand, target, mode):
    rule = FieldIntent(field=field, action="set", value=operand, evidence=text)
    model = FieldIntent(field=field, action="set", value=target, evidence=text)
    fields, _ = _run(
        monkeypatch,
        form,
        text,
        {field: base},
        [rule] if mode != "model" else [],
        [model] if mode != "rule" else [],
    )
    actual = fields[field]
    assert (actual.action, actual.value, actual.status, actual.value_source) == (
        "set",
        target,
        "valid",
        "derived",
    )


@pytest.mark.parametrize(
    "text,base",
    [
        ("현재 인원에서 두 명 더 추가할까요?", 3),
        ("현재 인원에서 두 명 더 추가하지 마세요.", 3),
        ("가능하면 현재 인원에서 두 명 더 추가해 주세요.", 3),
        ("앞서 제안한 인원에서 두 명 더 추가해 주세요.", 3),
        ("현재 인원에서 두 명 더 추가해 주세요.", None),
        ("현재 인원에서 네 명 줄여 주세요.", 3),
    ],
)
def test_ambiguous_or_invalid_delta_never_sets_literal(monkeypatch, text, base):
    item = FieldIntent(field="crew", action="set", value=2, evidence=text)
    fields, _ = _run(monkeypatch, "dates", text, {"crew": base}, [item], [item])
    assert fields["crew"].action != "set"


@pytest.mark.parametrize(
    "text,values",
    [
        (
            "입주자 행사 때문에 2월 10일, 18일, 25일을 제외일 전체로 설정해 주세요.",
            ["2027-02-10", "2027-02-18", "2027-02-25"],
        ),
        (
            "승강기 점검 날인 2월 8일과 17일, 23일은 제외일로만 입력해 주세요.",
            ["2027-02-08", "2027-02-17", "2027-02-23"],
        ),
    ],
)
@pytest.mark.parametrize("mode", ["rule", "model", "both"])
def test_postposed_exclusion_is_not_boundary(monkeypatch, text, values, mode):
    rule = [
        FieldIntent(field="start", action="set", value=values[0], evidence=text),
        FieldIntent(field="end", action="set", value=values[-1], evidence=text),
    ]
    model = rule + [FieldIntent(field="excluded", action="set", value=values, evidence=text)]
    fields, _ = _run(
        monkeypatch,
        "period",
        text,
        {"start": "2027-02-03", "end": "2027-02-26", "excluded": []},
        rule if mode != "model" else [],
        model if mode != "rule" else [],
    )
    assert fields["start"].action != "set"
    assert fields["end"].action != "set"
    assert fields["excluded"].status == "valid"
    assert fields["excluded"].value == values


@pytest.mark.parametrize(
    "form,field,text,current",
    [
        (
            "dates",
            "crew",
            "투입 인원은 지금 등록된 대로 하고 방문 날짜만 2월 11일로 변경해 주세요.",
            {"crew": 4},
        ),
        ("extra", "reason", "저장된 사유 유지해 주세요.", {"reason": "기존 사유"}),
        ("quote", "days", "작업 소요일은 현재 값으로 유지해 주세요.", {"days": 5}),
    ],
)
def test_current_keep_does_not_require_prior(monkeypatch, form, field, text, current):
    fields, _ = _run(monkeypatch, form, text, current, [], [])
    assert fields[field].status == "valid"
    assert fields[field].intent_kind == "retain_current"
