"""후속 2단계: 필드별 의도, 현재/이전 출처, 취소 및 세금 확정 대조 시험."""

import asyncio
from datetime import date
from uuid import UUID

import pytest

from app.agent import vendor_assist
from app.vendor.assist_schemas import (
    AssistContext,
    AssistRequest,
    CorrectionRelation,
    FieldIntent,
    ModelInterpretation,
    PreviousCandidate,
)
from app.vendor.assist_service import interpret

CTX = AssistContext(today=date(2026, 11, 24), base_month=date(2026, 12, 1), crew_max=6)
Q = {"amount": 4_550_000, "days": 3, "vat": "included", "valid": "2026-12-17"}
E = {"title": "보강", "reason": "현장 추가", "amount": 1_000_000, "vat": "included"}


def set_field(field, value, evidence, **kwargs):
    return FieldIntent(field=field, action="set", value=value, evidence=evidence, **kwargs)


def keep(field, evidence, kind="retain_current"):
    return FieldIntent(
        field=field,
        action="keep",
        value=None,
        evidence=evidence,
        intent_kind=kind,
        value_source="current" if kind == "retain_current" else "previous",
    )


def uncertain(field, evidence):
    return FieldIntent(field=field, action="uncertain", value=None, evidence=evidence)


def prior_quote(*, text="견적 582만 원에 공사 4일로 잡아 주세요."):
    return PreviousCandidate(
        request_id="prior",
        text=text,
        fields=[set_field("amount", 5_820_000, "582만 원"), set_field("days", 4, "공사 4일")],
        baselines={"amount": Q["amount"], "days": Q["days"]},
        sources={"amount": "llm", "days": "rule"},
        origins={"amount": "origin-amount", "days": "origin-days"},
    )


def run(monkeypatch, form, text, fields, *, current=None, prior=None, mode="model", rules=()):
    data = AssistRequest(
        request_id="new",
        form=form,
        resource_id=UUID(int=179),
        text=text,
        current=(Q if form == "quote" else E) if current is None else current,
        previous_candidate=prior,
        rule_fields=list(rules),
    )
    calls = []

    async def stub(safe_request, context):
        calls.append(safe_request)
        if mode == "fallback":
            raise RuntimeError("offline fixture")
        return ModelInterpretation(fields=fields)

    monkeypatch.setattr(vendor_assist, "interpret", stub)
    trace = {}
    result = asyncio.run(interpret(data, CTX, use_model=mode != "rules", trace=trace))
    assert len(calls) == int(mode != "rules")
    assert result.source == ("llm" if mode == "model" else "rule"), trace
    return {f.field: f for f in result.fields}, trace


def kept(item):
    assert (item.action, item.status, item.value, item.intent_kind, item.value_source) == (
        "keep",
        "valid",
        None,
        "retain_current",
        "current",
    ), item
    assert item.alternatives == []


def held(item):
    assert item.action == "uncertain" and item.status in ("invalid", "uncertain"), item
    assert item.value is None and not item.alternatives


def selected(item, value):
    assert (item.status, item.action, item.value) == ("valid", "set", value), item
    assert not item.alternatives


@pytest.mark.parametrize(
    "phrase",
    [
        "금액은 현재 화면에 있는 값으로 두고",
        "금액은 지금 입력란에 적힌 값으로 둘게요",
        "금액은 현재 값으로 둬 주세요",
    ],
)
def test_current_keep_preserves_distinct_previous_days(monkeypatch, phrase):
    text = phrase + ", 공사 일수는 앞서 제안한 그대로 유지해 주세요."
    fields, _ = run(
        monkeypatch,
        "quote",
        text,
        [
            keep("amount", phrase),
            keep("days", "공사 일수는 앞서 제안한 그대로 유지해 주세요", "retain_previous"),
        ],
        prior=prior_quote(),
    )
    kept(fields["amount"])
    selected(fields["days"], 4)
    assert fields["days"].origin_request_id == "origin-days"
    assert fields["days"].source == "rule"
    assert fields["days"].value_source == "previous"


@pytest.mark.parametrize(
    "ending,confirmed",
    [
        (" 두겠습니다.", True),
        (" 둬 주세요.", True),
        (" 두지 말아 주세요.", False),
        (" 둘까요?", False),
        (" 두겠습니다. 아직 결정하지 않았습니다.", False),
        (" 두겠습니다. 금액은 다시 변경할지 고민 중입니다.", False),
        (". 공사 일수는 유지해 주세요.", False),
    ],
)
def test_short_current_reference_requires_adjacent_confirmation(monkeypatch, ending, confirmed):
    evidence = "금액은 현재 화면에 있는 값대로"
    fields, _ = run(
        monkeypatch,
        "quote",
        evidence + ending,
        [keep("amount", evidence)],
        prior=prior_quote(),
    )
    if confirmed:
        kept(fields["amount"])
    else:
        held(fields["amount"])


def test_current_reference_from_other_field_cannot_prove_keep(monkeypatch):
    text = "공사 일수는 지금 입력란에 있는 값대로 두겠습니다."
    fields, _ = run(
        monkeypatch,
        "quote",
        text,
        [keep("amount", "지금 입력란에 있는 값대로")],
        prior=prior_quote(),
    )
    held(fields["amount"])


def test_truncated_crew_evidence_keeps_current_and_verified_previous_dates(monkeypatch):
    current = {"days": ["2026-12-09", "2026-12-16"], "crew": 2}
    proposed = ["2026-12-07", "2026-12-21"]
    old = PreviousCandidate(
        request_id="old",
        text="작업 날짜는 12월 7일과 21일, 인원은 4명으로 제안해 주세요.",
        fields=[set_field("days", proposed, "12월 7일과 21일"), set_field("crew", 4, "4명")],
        baselines=current,
        sources={"days": "llm", "crew": "llm"},
        origins={"days": "origin-days", "crew": "origin-crew"},
    )
    text = "인원은 현재 화면에 있는 값대로 두겠습니다. 작업일은 앞서 정한 그대로 유지해 주세요."
    fields, _ = run(
        monkeypatch,
        "dates",
        text,
        [
            keep("crew", "인원은 현재 화면에 있는 값대로"),
            keep("days", "작업일은 앞서 정한 그대로 유지", "retain_previous"),
        ],
        current=current,
        prior=old,
    )
    kept(fields["crew"])
    selected(fields["days"], proposed)
    assert fields["days"].origin_request_id == "origin-days"
    assert fields["days"].value_source == "previous"


@pytest.mark.parametrize(
    "phrase",
    [
        "금액은 현재 화면에 있는 값으로 두고 싶지만 아직 미정입니다.",
        "금액은 현재 값으로 둬 주세요. 금액은 다시 변경할지 고민 중입니다.",
        "금액은 지금 값으로 두고 괜찮은지 확인해 주세요.",
        "금액은 앞서 제안한 값 그대로 유지해 주세요.",
        "금액은 이전 제안 그대로 유지해 주세요.",
        "금액은 지금 값으로 두고 그 요청은 취소합니다.",
    ],
)
def test_uncertain_or_previous_keep_cannot_be_current_keep(monkeypatch, phrase):
    fields, _ = run(monkeypatch, "quote", phrase, [keep("amount", phrase)], prior=prior_quote())
    held(fields["amount"])


@pytest.mark.parametrize(
    "text",
    [
        "추가금 18만 원을 26만 원으로 올리는 요청은 취소합니다.",
        "추가 금액 32만원에서 47만원으로 변경하려던 건 철회합니다.",
        "추가금 420,000원으로 수정하려던 제안은 취소할게요.",
    ],
)
@pytest.mark.parametrize("mode", ["model", "rules", "fallback"])
def test_described_change_cancellation_is_current_keep(monkeypatch, text, mode):
    title = set_field("title", "바닥 보수", "항목명만 바닥 보수로 확정")
    fields, _ = run(
        monkeypatch,
        "extra",
        text + " 항목명만 바닥 보수로 확정해 주세요.",
        [uncertain("amount", text), title],
        mode=mode,
        rules=[title],
    )
    kept(fields["amount"])
    selected(fields["title"], "바닥 보수")
    assert fields["amount"].origin_request_id == "new"


@pytest.mark.parametrize(
    "text",
    [
        "추가금 18만 원을 26만 원으로 올리는 요청은 취소할까요?",
        "추가금 18만 원을 26만 원으로 올리는 요청은 취소할지 검토해 주세요.",
        "추가금 18만 원을 26만 원으로 올리는 요청은 취소합니다. 금액은 30만 원인지 미정입니다.",
        "추가금 18만 원을 26만 원으로 올리는 요청은 취소합니다. 30만 원으로 바꿀지 고민 중입니다.",
        "추가금 18만 원을 26만 원으로 올리는 요청은 취소합니다. 아직 결정하지 않았어요.",
    ],
)
def test_cancel_question_and_competing_instruction_stay_held(monkeypatch, text):
    fields, _ = run(monkeypatch, "extra", text, [uncertain("amount", text)])
    held(fields["amount"])


def test_cancelled_old_and_new_values_are_not_offered(monkeypatch):
    text = "추가금 18만 원을 26만 원으로 올리는 요청은 취소합니다. 사유는 아직 미정입니다."
    fields, _ = run(
        monkeypatch,
        "extra",
        text,
        [
            set_field("amount", 260_000, "26만 원"),
            uncertain("reason", "사유는 아직 미정입니다"),
        ],
        rules=[set_field("amount", 180_000, "18만 원")],
    )
    kept(fields["amount"])
    held(fields["reason"])


def test_stale_scalar_cancellation_preserves_manual_value(monkeypatch):
    text = "아까 582만 원을 635만 원으로 정정하려던 건 취소할게요. 부가세만 별도로 해 주세요."
    fields, trace = run(
        monkeypatch,
        "quote",
        text,
        [
            uncertain("amount", "582만 원을 635만 원으로 정정하려던 건 취소할게요"),
            set_field("vat", "excluded", "부가세만 별도로 해 주세요"),
        ],
        current={**Q, "amount": 5_100_000},
        prior=prior_quote(),
    )
    kept(fields["amount"])
    selected(fields["vat"], "excluded")
    selected(fields["days"], 4)
    assert fields["days"].origin_request_id == "origin-days"
    assert trace["previous_rejected"]["amount"] == "stale_context"


@pytest.mark.parametrize(
    "phrase",
    [
        "견적 420만 원에 맞출 수 있는지부터 알려주세요.",
        "견적 420만원으로 진행 가능한지 확인해 주세요.",
        "견적 420만원으로 해도 되는지 알려주세요.",
        "견적 420만 원으로 진행할지 고민 중입니다.",
        "견적 420만원까지 가능한가요?",
        "견적 420만원에서 490만원 사이로 생각합니다.",
        "견적 420만원으로 진행하지 말아 주세요.",
    ],
)
@pytest.mark.parametrize("mode", ["model", "rules", "fallback"])
def test_scalar_question_and_range_cannot_authorize_change(monkeypatch, phrase, mode):
    text = phrase + " 부가세는 별도로 해 주세요."
    amount = set_field("amount", 4_200_000, "420만 원" if "420만 원" in phrase else "420만원")
    vat = set_field("vat", "excluded", "부가세는 별도로 해 주세요")
    fields, _ = run(monkeypatch, "quote", text, [amount, vat], mode=mode, rules=[amount, vat])
    held(fields["amount"])
    selected(fields["vat"], "excluded")


@pytest.mark.parametrize("mode", ["model", "rules", "fallback"])
def test_other_field_question_does_not_block_confirmed_amount(monkeypatch, mode):
    text = "견적 420만 원으로 맞춰 주세요. 부가세 포함이 가능한지 확인해 주세요."
    amount = set_field("amount", 4_200_000, "420만 원")
    vat = set_field("vat", "included", "부가세 포함")
    fields, _ = run(monkeypatch, "quote", text, [amount, vat], mode=mode, rules=[amount, vat])
    selected(fields["amount"], 4_200_000)
    held(fields["vat"])


@pytest.mark.parametrize(
    "text,evidence",
    [
        (
            "부가세를 포함하지 말라는 뜻이 아니었어요. 포함으로 확정해 주세요.",
            "포함으로 확정해 주세요",
        ),
        (
            "세금을 빼 달라는 말은 아닙니다. 부가세 포함으로 처리해 주세요.",
            "부가세 포함으로 처리해 주세요",
        ),
        ("부가세 포함 안 하는 건 아니고, 포함 맞습니다.", "포함 맞습니다"),
    ],
)
@pytest.mark.parametrize("mode", ["model", "rules", "fallback"])
def test_vat_double_negation_requires_final_affirmation(monkeypatch, text, evidence, mode):
    vat = set_field("vat", "included", evidence)
    fields, _ = run(
        monkeypatch, "quote", text, [vat], current={**Q, "vat": "excluded"}, mode=mode, rules=[vat]
    )
    selected(fields["vat"], "included")


@pytest.mark.parametrize(
    "text,evidence,value",
    [
        ("부가세를 포함하지 마세요.", "포함", "included"),
        ("부가세를 포함하지 말라는 뜻이 아니었어요.", "포함", "included"),
        (
            "부가세를 포함하지 말라는 뜻이 아니었어요. 포함으로 할까요?",
            "포함으로 할까요",
            "included",
        ),
        (
            "부가세를 포함하지 말라는 뜻이 아니었어요. 포함으로 확정해 주세요. 아직 미정입니다.",
            "포함으로 확정해 주세요",
            "included",
        ),
        (
            "부가세 포함으로 확정해 주세요. 금액은 420만원으로 해 주세요. 부가세는 취소할지 고민입니다.",
            "부가세 포함으로 확정해 주세요",
            "included",
        ),
        ("부가세 포함이나 별도 중 고민 중입니다.", "포함", "included"),
        ("부가세 미포함으로 확정해 주세요.", "미포함", "included"),
        ("현재 부가세 포함으로 되어 있습니다.", "포함", "included"),
        ("부가세는 별도입니다. 부가세 포함으로 확정할지 검토해 주세요.", "포함", "included"),
    ],
)
@pytest.mark.parametrize("mode", ["model", "rules", "fallback"])
def test_vat_negation_inquiry_and_report_are_not_actionable(
    monkeypatch, text, evidence, value, mode
):
    vat = set_field("vat", value, evidence)
    fields, _ = run(monkeypatch, "quote", text, [vat], mode=mode, rules=[vat])
    held(fields["vat"])


def test_vat_conflict_does_not_offer_ambiguous_modes(monkeypatch):
    text = "부가세는 포함 또는 별도로 할지 모르겠어요."
    fields, _ = run(
        monkeypatch,
        "quote",
        text,
        [set_field("vat", "included", "포함")],
        rules=[set_field("vat", "excluded", "별도")],
    )
    held(fields["vat"])


def test_vat_valid_correction_with_later_inquiry_stays_held(monkeypatch):
    text = "부가세는 포함 말고 별도로 해 주세요. 별도로 가능한지 먼저 알려주세요."
    relation = CorrectionRelation(
        kind="replace",
        rejected_value="included",
        rejected_evidence="포함",
        accepted_evidence="별도로 해 주세요",
    )
    fields, _ = run(
        monkeypatch,
        "quote",
        text,
        [
            set_field("vat", "excluded", "별도로 해 주세요", correction=relation),
        ],
    )
    held(fields["vat"])


@pytest.mark.parametrize(
    "text,evidence,value",
    [
        ("금액이 420만원이면, 부가세 별도로 해 주세요.", "부가세 별도로 해 주세요", "excluded"),
        ("부가세 포함으로 확정하지 않았어요.", "포함", "included"),
        ("부가세 별도로 할 수 있는지 확인해 주세요.", "별도로", "excluded"),
    ],
)
def test_vat_conditions_cannot_be_discarded(monkeypatch, text, evidence, value):
    fields, _ = run(monkeypatch, "quote", text, [set_field("vat", value, evidence)])
    held(fields["vat"])


def test_correction_evidence_cannot_hide_condition(monkeypatch):
    text = "가능하면 부가세 포함 말고 별도로 해 주세요."
    relation = CorrectionRelation(
        kind="replace",
        rejected_value="included",
        rejected_evidence="가능하면 부가세 포함",
        accepted_evidence="별도로 해 주세요",
    )
    fields, _ = run(
        monkeypatch,
        "quote",
        text,
        [
            set_field("vat", "excluded", "별도로 해 주세요", correction=relation),
        ],
    )
    held(fields["vat"])


@pytest.mark.parametrize(
    "old_text,evidence,value,valid",
    [
        ("부가세 포함 가능한지 알려 주세요.", "포함", "included", False),
        ("부가세를 포함하지 마세요.", "포함", "included", False),
        ("부가세를 포함하지 말라는 뜻이 아니었어요.", "포함", "included", False),
        ("부가세 포함으로 할게요.", "포함", "included", True),
        ("부가세 포함 말고 별도로 처리해 주세요.", "별도로 처리해 주세요", "excluded", True),
        (
            "부가세를 포함하지 말라는 뜻이 아니었어요. 포함으로 확정해 주세요.",
            "포함으로 확정해 주세요",
            "included",
            True,
        ),
    ],
)
def test_vat_history_uses_same_intent_gate(monkeypatch, old_text, evidence, value, valid):
    current = {**Q, "vat": "excluded" if value == "included" else "included"}
    prior = PreviousCandidate(
        request_id="old",
        text=old_text,
        fields=[set_field("vat", value, evidence)],
        baselines={"vat": current["vat"]},
        sources={"vat": "llm"},
        origins={"vat": "origin-vat"},
    )
    text = "부가세는 앞서 제안한 그대로 유지해 주세요."
    fields, trace = run(
        monkeypatch,
        "quote",
        text,
        [
            keep("vat", text, "retain_previous"),
        ],
        current=current,
        prior=prior,
    )
    if valid:
        selected(fields["vat"], value)
        assert fields["vat"].value_source == "previous"
        assert fields["vat"].origin_request_id == "origin-vat"
    else:
        held(fields["vat"])
        assert trace["previous_rejected"]["vat"] == "ambiguous_intent"
