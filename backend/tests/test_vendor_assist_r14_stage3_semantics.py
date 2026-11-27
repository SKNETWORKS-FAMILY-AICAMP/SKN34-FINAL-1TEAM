"""Contrast tests for stage-3 semantic proofs, independent of replay IDs."""

import pytest
from test_vendor_assist_r13_backend_quality import CURRENT, run

from app.vendor.assist_schemas import CorrectionRelation, FieldIntent, PreviousCandidate


def set_field(field, value, evidence, correction=None):
    return FieldIntent(
        field=field,
        action="set",
        value=value,
        evidence=evidence,
        intent_kind="explicit_change",
        value_source="utterance",
        correction=correction,
    )


def seed(form, field, value, evidence, *, stale=False):
    baseline = CURRENT[form][field]
    if stale:
        baseline = value
    return PreviousCandidate(
        request_id="seed",
        text=evidence,
        fields=[set_field(field, value, evidence)],
        baselines={field: baseline},
        sources={field: "llm"},
        origins={field: "seed"},
    )


def accepted(actual, value):
    return (actual.action, actual.status, actual.value) == ("set", "valid", value)


@pytest.mark.parametrize("producer", ["rule", "model", "both"])
@pytest.mark.parametrize(
    "prefix,suffix,field,value,ok",
    [
        ("운송 준비를 고려하면 ", "", "start", "2026-12-09", True),
        ("", "", "start", "2026-12-09", True),
        ("승인되면 ", "", "start", "2026-12-09", False),
        ("승인 여부를 고려하면 ", "", "start", "2026-12-09", False),
        ("예를 들어 ", "", "start", "2026-12-09", False),
        ("운송 준비를 고려하면 ", "", "end", "2026-12-09", False),
        ("운송 준비를 고려하면 ", "", "start", "2026-12-10", False),
        ("운송 준비를 고려하면 ", " 아직 확정하지 마세요.", "start", "2026-12-09", False),
    ],
)
def test_preview_boundary(monkeypatch, producer, prefix, suffix, field, value, ok):
    text = (
        prefix + "12월 9일에 착공하는 것으로 변경하는 게 좋겠습니다. 변경안을 보여 주세요." + suffix
    )
    proposal = set_field(field, value, "12월 9일에 착공하는 것으로 변경")
    actual = run(monkeypatch, "period", text, proposal, producer)
    assert accepted(actual, value) == ok, actual


@pytest.mark.parametrize("producer", ["rule", "model", "both"])
@pytest.mark.parametrize(
    "mode", ["verified", "stale", "absent", "wrong", "conditional", "reversed"]
)
def test_pending_quantity_replace(monkeypatch, producer, mode):
    old = seed(
        "dates",
        "crew",
        5 if mode != "wrong" else 4,
        "작업 인원은 5명으로 확정해 주세요."
        if mode != "wrong"
        else "작업 인원은 4명으로 확정해 주세요.",
        stale=mode == "stale",
    )
    if mode == "absent":
        old = None
    value = 6 if mode == "reversed" else 3
    text = f"정정합니다. 다섯 명까지는 필요 없고 작업 인원 {value}명으로 수정해 주세요."
    if mode == "conditional":
        text = "승인되면 " + text
    evidence = f"작업 인원 {value}명으로 수정"
    correction = CorrectionRelation(
        kind="replace",
        rejected_value=5,
        rejected_evidence="다섯 명까지는 필요 없고",
        accepted_evidence=evidence,
    )
    actual = run(
        monkeypatch,
        "dates",
        text,
        set_field("crew", value, evidence, correction),
        producer,
        prior=old,
    )
    assert accepted(actual, value) == (mode == "verified"), actual
    if mode == "verified":
        assert actual.correction == correction and actual.value_source == "utterance"


@pytest.mark.parametrize("producer", ["rule", "model", "both"])
@pytest.mark.parametrize("mode", ["verified", "stale", "absent", "conditional"])
def test_pending_money_object(monkeypatch, producer, mode):
    old = seed(
        "quote", "amount", 5200000, "견적 금액은 520만원으로 수정해 주세요.", stale=mode == "stale"
    )
    if mode == "absent":
        old = None
    text = "견적 금액 520만원 후보를 530만원으로 정정합니다."
    if mode == "conditional":
        text = "허락을 받으면 " + text
    correction = CorrectionRelation(
        kind="replace",
        rejected_value=5200000,
        rejected_evidence="520만원",
        accepted_evidence="530만원으로 정정",
    )
    actual = run(
        monkeypatch,
        "quote",
        text,
        set_field("amount", 5300000, "530만원", correction),
        producer,
        prior=old,
    )
    assert accepted(actual, 5300000) == (mode == "verified"), actual


@pytest.mark.parametrize("producer", ["rule", "model", "both"])
@pytest.mark.parametrize("mode", ["verified", "stale", "absent", "conditional"])
def test_owned_error_report(monkeypatch, producer, mode):
    old = seed(
        "quote", "amount", 5200000, "견적 금액은 520만원으로 수정해 주세요.", stale=mode == "stale"
    )
    if mode == "absent":
        old = None
    text = "앞서 금액을 잘못 계산했습니다. 견적 총액은 530만원으로 정정하고 부가세 별도 견적으로 변경해 주세요."
    if mode == "conditional":
        text = "허락을 받으면 " + text
    actual = run(
        monkeypatch,
        "quote",
        text,
        set_field("amount", 5300000, "견적 총액은 530만원으로 정정"),
        producer,
        prior=old,
    )
    assert accepted(actual, 5300000) == (mode == "verified"), actual


@pytest.mark.parametrize("producer", ["rule", "model", "both"])
@pytest.mark.parametrize(
    "form,field,text,ok",
    [
        (
            "dates",
            "crew",
            "작업 인원은 지금 등록된 대로 하고 방문 날짜만 12월 8일로 변경해 주세요.",
            True,
        ),
        (
            "dates",
            "days",
            "이미 예약한 방문 일정은 건드리지 마시고 현장 인원만 세 명으로 줄여 주십시오.",
            True,
        ),
        (
            "period",
            "end",
            "완공일은 현재 값으로 고정하고, 작업하지 않는 날은 12월 20일로 변경해 주세요.",
            True,
        ),
        (
            "period",
            "end",
            "완공일은 현재 값으로 고정하지 마시고, 작업하지 않는 날은 12월 20일로 변경해 주세요.",
            False,
        ),
        (
            "dates",
            "crew",
            "허락되면 작업 인원은 지금 등록된 대로 하고 방문 날짜만 12월 8일로 변경해 주세요.",
            False,
        ),
    ],
)
def test_owned_keep(monkeypatch, producer, form, field, text, ok):
    evidence = text.split("하고")[0] if "하고" in text else text.split("마시고")[0] + "마시고"
    # The full original clause is still independently parsed by BE.
    proposal = FieldIntent(
        field=field,
        action="keep",
        value=None,
        evidence=evidence,
        intent_kind="retain_current",
        value_source="current",
    )
    actual = run(monkeypatch, form, text, proposal, producer)
    assert (actual.status == "valid" and actual.intent_kind == "retain_current") == ok, actual


@pytest.mark.parametrize("producer", ["rule", "model", "both"])
@pytest.mark.parametrize(
    "text,ok",
    [
        ("12월 3일은 그대로 두고, 17일 공사만 12월 20일로 옮겨 주세요.", True),
        ("12월 3일은 그대로 두고, 17일 공사만 12월 20일로 옮길까요?", False),
        ("승인되면 12월 3일은 그대로 두고, 17일 공사만 12월 20일로 옮겨 주세요.", False),
        ("12월 3일은 그대로 두고, 19일 공사만 12월 20일로 옮겨 주세요.", False),
    ],
)
def test_member_object_move(monkeypatch, producer, text, ok):
    value = ["2026-12-03", "2026-12-20"]
    actual = run(monkeypatch, "dates", text, set_field("days", value, "12월 20일"), producer)
    assert accepted(actual, value) == ok, actual
    if ok:
        assert actual.value_source == "derived"
        assert actual.correction.rejected_value == CURRENT["dates"]["days"]
