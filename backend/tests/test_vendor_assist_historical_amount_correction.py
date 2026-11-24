"""Verify historical amount corrections using the real service and a stub model."""

import pytest
from test_vendor_assist_multiturn_followup import (
    assert_held,
    assert_set,
    correct,
    extra_prior,
    interpreted,
    quote_prior,
    request,
)

from app.vendor.assist_schemas import FieldIntent


@pytest.mark.parametrize(
    ("form", "text", "old", "new", "old_evidence", "new_evidence"),
    [
        (
            "quote",
            "앞서 455만 원이라고 적었는데 계산을 틀리게 했어요. 487만 2천 원으로 수정해 주세요.",
            4_550_000,
            4_872_000,
            "455만 원이라고 적었는데",
            "487만 2천 원",
        ),
        (
            "extra",
            "부가세는 그대로, 12만 원이라고 말했던 추가 비용만 16만 4천 원으로 바꿀게요.",
            120_000,
            164_000,
            "12만 원이라고 말했던 추가 비용",
            "16만 4천 원으로",
        ),
    ],
)
def test_explicit_historical_amount_correction(
    monkeypatch, form, text, old, new, old_evidence, new_evidence
):
    prior = quote_prior() if form == "quote" else extra_prior()
    data = request(form, text, current=prior.baselines, prior=prior)
    candidate = FieldIntent(
        field="amount",
        action="set",
        value=new,
        evidence=new_evidence,
        intent_kind="explicit_change",
        value_source="utterance",
        correction=correct(old, old_evidence, new_evidence),
    )
    fields, _, _ = interpreted(monkeypatch, data, [candidate])
    assert_set(fields["amount"], new, origin=data.request_id, source="llm")


@pytest.mark.parametrize(
    ("text", "state"),
    [
        ("455만 원이라고 했는데 487만 원으로 수정할게요.", "missing"),
        ("455만 원이라고 했는데 487만 원으로 수정할게요.", "stale"),
        ("455만 원이라고 했는데 487만 원으로 수정할게요.", "wrong"),
        ("455만 원이라고 했는데 487만 원으로 수정할게요?", "valid"),
        ("455만 원이라고 했는데 487만 원으로 수정하지 마세요.", "valid"),
        ("455만 원이라고 했는데 487만 원으로 수정할게요. 취소할게요.", "valid"),
        ("가능하면, 455만 원이라고 했는데 487만 원으로 수정할게요.", "valid"),
        ("455만 원이라고 했는데 487만 원으로 수정할게요. 아니면 490만 원.", "valid"),
        ("455만 원이라고 했는데 487만 원으로 수정할게요. 금액은 미정.", "valid"),
        ("455만 원이라고 했는데 487만 원으로 수정할게요. 아직 확정하지 않아요.", "valid"),
    ],
)
def test_historical_amount_holds_unverified_changes(monkeypatch, text, state):
    prior = quote_prior()
    current = dict(prior.baselines)
    if state == "missing":
        prior = None
    elif state == "stale":
        current["amount"] = 6_100_000
    elif state == "wrong":
        prior = prior.model_copy(
            update={
                "text": "견적 456만 원",
                "sources": {"amount": "llm"},
                "origins": {"amount": "original-amount"},
                "baselines": {"amount": current["amount"]},
                "fields": [
                    FieldIntent(field="amount", action="set", value=4_560_000, evidence="456만 원")
                ],
            }
        )
    data = request("quote", text, current=current, prior=prior)
    candidate = FieldIntent(
        field="amount",
        action="set",
        value=4_870_000,
        evidence="487만 원",
        intent_kind="explicit_change",
        value_source="utterance",
        correction=correct(4_550_000, "455만 원", "487만 원"),
    )
    fields, _, _ = interpreted(monkeypatch, data, [candidate])
    assert_held(fields["amount"])
