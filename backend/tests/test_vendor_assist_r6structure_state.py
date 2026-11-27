"""필드 상태와 기간 제약 책임의 독립 회귀 (외부 호출 없음)."""

import asyncio
import os
import sys
from copy import deepcopy
from datetime import date
from pathlib import Path
from uuid import UUID

import pytest

from app.agent import llm, vendor_assist
from app.config import settings
from app.vendor.assist_schemas import (
    FORM_FIELDS,
    AssistContext,
    AssistRequest,
    FieldIntent,
    ModelInterpretation,
    PreviousCandidate,
)
from app.vendor.assist_service import interpret

ROOT = Path(os.environ.get("R6STRUCTURE_WORKER17_ROOT", Path(__file__).resolve().parents[2]))
CONTEXT = AssistContext(
    today=date(2026, 11, 25),
    base_month=date(2026, 12, 1),
    days_max=120,
    crew_max=6,
    required_days=2,
)
BASES = {
    "quote": {"amount": 3650000, "vat": "included", "days": 4, "valid": "2027-01-27"},
    "period": {
        "start": "2026-12-02",
        "end": "2026-12-29",
        "excluded": ["2026-12-11", "2026-12-21"],
    },
    "dates": {"days": ["2026-12-09", "2026-12-16"], "crew": 2},
    "extra": {"title": "기존 공사", "reason": "기존 사유", "amount": 100000, "vat": "included"},
}
MODES = ("rule", "model", "both")


def day(n):
    return f"2026-12-{n:02d}"


def fresh(field, value, evidence):
    return FieldIntent(
        field=field,
        action="set",
        value=value,
        evidence=evidence,
        intent_kind="explicit_change",
        value_source="utterance",
    )


def hold(field, text):
    return FieldIntent(
        field=field, action="uncertain", value=None, evidence=text, intent_kind="ambiguous"
    )


def keep(field, text="", reference=None):
    return FieldIntent(
        field=field,
        action="keep",
        value=None,
        evidence=text,
        intent_kind=f"retain_{reference}" if reference else "no_mention",
        value_source=reference,
    )


def verify_imports():
    assert not settings.llm_live
    backend = (ROOT / "backend").resolve()
    for name, module in tuple(sys.modules.items()):
        if (name == "app" or name.startswith("app.")) and getattr(module, "__file__", None):
            assert Path(module.__file__).resolve().is_relative_to(backend), name


def run(mp, form, text, fields, *, mode, current=None, prior=None, rid="new", model=None):
    verify_imports()
    calls = []

    async def forbidden(*args, **kwargs):
        pytest.fail("외부 모델 호출 금지")

    async def stub(data, context):
        calls.append(data.request_id)
        supplied = {f.field: f for f in (fields if model is None else model)}
        return ModelInterpretation(fields=[supplied.get(k, keep(k)) for k in FORM_FIELDS[form]])

    mp.setattr(llm, "structured", forbidden)
    mp.setattr(vendor_assist, "interpret", stub)
    req = AssistRequest(
        form=form,
        text=text,
        current=deepcopy(BASES[form] if current is None else current),
        previous_candidate=prior,
        request_id=rid,
        resource_id=UUID(int=179),
        rule_fields=list(fields) if mode in ("rule", "both") else [],
    )
    before = req.model_dump(mode="json")
    trace = {}
    response = asyncio.run(interpret(req, CONTEXT, use_model=mode != "rule", trace=trace))
    assert calls == ([] if mode == "rule" else [rid])
    assert response.source == ("rule" if mode == "rule" else "llm")
    assert req.model_dump(mode="json") == before
    verify_imports()
    return {f.field: f for f in response.fields}, trace


def exact(field, value):
    assert (field.status, field.action, field.value) == ("valid", "set", value), field
    assert field.alternatives == []


def withheld(field):
    assert field.action not in ("set", "clear"), field
    assert field.value is None and not field.alternatives, field


def prior_from(response, fields, current, rid, text):
    selected = [response[k] for k in fields]
    return PreviousCandidate(
        request_id=rid,
        text=text,
        fields=[
            FieldIntent.model_validate(f.model_dump(include=set(FieldIntent.model_fields)))
            for f in selected
        ],
        sources={f.field: f.source for f in selected},
        origins={f.field: f.origin_request_id for f in selected},
        baselines={f.field: deepcopy(current[f.field]) for f in selected},
    )


def prove_carry(field, prior, trace):
    old = next(f for f in prior.fields if f.field == field.field)
    exact(field, old.value)
    assert field.intent_kind == "retain_previous"
    assert field.origin_request_id == prior.origins[field.field]
    assert field.source == prior.sources[field.field]
    assert field.evidence == old.evidence
    assert field.correction == old.correction
    assert field.value_source == ("derived" if old.value_source == "derived" else "previous")
    verified = trace["previous_verified"]
    assert verified["baselines"][field.field] == prior.baselines[field.field]
    assert verified["origins"][field.field] == prior.origins[field.field]
    assert verified["sources"][field.field] == prior.sources[field.field]
    proof = next(f for f in verified["fields"] if f["field"] == field.field)
    assert proof["value"] == old.value and proof["evidence"] == old.evidence
    assert proof["correction"] == (
        old.correction.model_dump(mode="json") if old.correction else None
    )


# 양쪽 날짜의 책임, 연동된 변경, 애매한 두 경계, 보류 후 재투영.
PERIOD_CASES = [
    ("end-outside", {"start": day(4), "end": day(18)}, {"start": day(4)}),
    ("start-outside", {"start": day(14), "end": day(27)}, {"end": day(27)}),
    ("end-equals-excluded", {"start": day(4), "end": day(21)}, {"start": day(4)}),
    ("start-equals-excluded", {"start": day(11), "end": day(27)}, {"end": day(27)}),
    ("both-edges-own", {"start": day(14), "end": day(18)}, {}),
    (
        "coupled-valid",
        {"start": day(14), "end": day(27), "excluded": [day(17), day(23)]},
        {"start": day(14), "end": day(27), "excluded": [day(17), day(23)]},
    ),
    (
        "excluded-invalid-start-independent",
        {"start": day(4), "excluded": [day(1)]},
        {"start": day(4)},
    ),
    ("excluded-invalid-end-independent", {"end": day(27), "excluded": [day(30)]}, {"end": day(27)}),
    ("reproject-end-after-invalid-list", {"end": day(18), "excluded": [day(15), day(30)]}, {}),
    ("reproject-start-after-invalid-list", {"start": day(14), "excluded": [day(15), day(30)]}, {}),
]


def period_entries(patch):
    names = {"start": "시작일", "end": "종료일", "excluded": "제외일"}
    entries = []
    for k, value in patch.items():
        vs = value if isinstance(value, list) else [value]
        token = "과 ".join(f"{int(v[5:7])}월 {int(v[-2:])}일" for v in vs)
        text = (
            f"{names[k]}은 {token}로 변경합니다."
            if k != "excluded"
            else f"제외일은 {token}로 지정합니다."
        )
        entries.append(fresh(k, value, text))
    return entries


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("name,requested,expected", PERIOD_CASES, ids=[x[0] for x in PERIOD_CASES])
def test_period_constraint_responsibility(monkeypatch, mode, name, requested, expected):
    entries = period_entries(requested)
    text = " ".join(e.evidence for e in entries)
    fields, trace = run(monkeypatch, "period", text, entries, mode=mode)
    for key in requested:
        if key in expected:
            exact(fields[key], expected[key])
            assert fields[key].origin_request_id == "new"
            assert fields[key].source in ("llm", "rule")
            assert fields[key].evidence in text and fields[key].correction is None
        else:
            withheld(fields[key])
    assert {
        k: f.value for k, f in fields.items() if f.status == "valid" and f.action == "set"
    } == expected
    projected = {**BASES["period"], **expected}
    assert all(projected["start"] < d < projected["end"] for d in projected["excluded"])
    assert trace["previous_verified"] is None


@pytest.mark.parametrize("mode", MODES)
def test_period_reversed_edges_ambiguous(monkeypatch, mode):
    current = {"start": day(2), "end": day(29), "excluded": []}
    entries = period_entries({"start": day(22), "end": day(10)})
    fields, _ = run(
        monkeypatch,
        "period",
        " ".join(e.evidence for e in entries),
        entries,
        mode=mode,
        current=current,
    )
    # 둘 다 실제 기존 상대 경계와는 유효하다. 임의로 하나를 고르지 않는다.
    withheld(fields["start"])
    withheld(fields["end"])


@pytest.mark.parametrize("mode", MODES)
def test_period_coupled_shift(monkeypatch, mode):
    current = {"start": day(2), "end": day(10), "excluded": [day(6)]}
    requested = {"start": day(17), "end": day(27), "excluded": [day(21)]}
    entries = period_entries(requested)
    fields, _ = run(
        monkeypatch,
        "period",
        " ".join(e.evidence for e in entries),
        entries,
        mode=mode,
        current=current,
    )
    for key, value in requested.items():
        exact(fields[key], value)


@pytest.mark.parametrize("mode", MODES)
def test_period_reproject_verified_prior_after_bad_new_end(monkeypatch, mode):
    current = BASES["period"]
    seed = fresh("start", day(5), "시작일은 12월 5일로 정합니다.")
    f1, _ = run(monkeypatch, "period", seed.evidence, [seed], mode=mode, rid="seed")
    exact(f1["start"], day(5))
    prior = prior_from(f1, ["start"], current, "seed", seed.evidence)
    end = fresh("end", day(19), "종료일은 12월 19일로 변경합니다.")
    f2, trace = run(monkeypatch, "period", end.evidence, [end], mode=mode, prior=prior)
    prove_carry(f2["start"], prior, trace)
    withheld(f2["end"])


HISTORY = [
    (
        "quote",
        "days",
        9,
        "공사 기간은 9일로 정합니다.",
        "amount",
        4920000,
        "견적 금액은 492만원으로 정합니다.",
        "소요일은 앞서 제안한 일수 그대로 반영해 주세요.",
        "금액 변경은 철회합니다.",
    ),
    (
        "dates",
        "crew",
        4,
        "작업 인원은 4명으로 정합니다.",
        "days",
        [day(8), day(19)],
        "작업 날짜는 12월 8일과 19일로 정합니다.",
        "인원은 직전에 제안한 인원 그대로 반영해 주세요.",
        "작업 날짜 변경은 철회합니다.",
    ),
    (
        "period",
        "end",
        day(28),
        "종료일은 12월 28일로 정합니다.",
        "start",
        day(4),
        "시작일은 12월 4일로 정합니다.",
        "종료일은 직전 후보 그대로 적용해 주세요.",
        "시작일 변경은 철회합니다.",
    ),
    (
        "extra",
        "title",
        "몰딩 끝부분 정리",
        "항목명은 몰딩 끝부분 정리로 정합니다.",
        "amount",
        284000,
        "추가 비용은 28만 4천 원으로 정합니다.",
        "항목명은 직전 후보 그대로 적용해 주세요.",
        "금액 변경은 철회합니다.",
    ),
]


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize(
    "form,key,value,seed,other,other_value,other_seed,reference,cancel",
    HISTORY,
    ids=[x[0] for x in HISTORY],
)
def test_scoped_cancel_and_verified_prior(
    monkeypatch, mode, form, key, value, seed, other, other_value, other_seed, reference, cancel
):
    current = BASES[form]
    text = seed + " " + other_seed
    f1, _ = run(
        monkeypatch,
        form,
        text,
        [fresh(key, value, seed), fresh(other, other_value, other_seed)],
        mode=mode,
        rid="seed",
    )
    exact(f1[key], value)
    exact(f1[other], other_value)
    prior = prior_from(f1, [key, other], current, "seed", text)
    f2, trace = run(
        monkeypatch,
        form,
        cancel + " " + reference,
        [keep(key, reference, "previous"), keep(other, cancel, "current")],
        mode=mode,
        prior=prior,
    )
    prove_carry(f2[key], prior, trace)
    withheld(f2[other])
    assert f2[other].intent_kind == "retain_current"
    assert f2[other].value_source == "current"
    assert f2[other].origin_request_id == "new"


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize(
    "form,key,value,seed,other,other_value,other_seed,reference,cancel",
    HISTORY,
    ids=[x[0] for x in HISTORY],
)
def test_stale_prior_baseline(
    monkeypatch, mode, form, key, value, seed, other, other_value, other_seed, reference, cancel
):
    current = deepcopy(BASES[form])
    f1, _ = run(monkeypatch, form, seed, [fresh(key, value, seed)], mode=mode, rid="seed")
    exact(f1[key], value)
    prior = prior_from(f1, [key], current, "seed", seed)
    current[key] = value
    f2, trace = run(
        monkeypatch,
        form,
        reference,
        [keep(key, reference, "previous")],
        mode=mode,
        prior=prior,
        current=current,
    )
    withheld(f2[key])
    assert key in trace["previous_rejected"]


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize(
    "suffix,value",
    [
        ("소요일은 앞서 제안한 일수 그대로 반영해 주세요.", 9),
        ("공사 일수는 6일로 변경합니다.", 6),
        ("공사 기간은 6.5일로 변경합니다.", None),
        ("공사 기간은 6~8일로 변경합니다.", None),
        ("공사 기간은 6일 정도로 생각 중입니다.", None),
        ("공사 기간은 6일로 변경할까요?", None),
        ("공사 일수는 직전 후보 그대로 두고 6일로 변경합니다.", None),
    ],
)
def test_duration_reference_and_fresh_risk(monkeypatch, mode, suffix, value):
    seed = fresh("days", 9, "공사 일수는 9일로 정합니다.")
    f1, _ = run(monkeypatch, "quote", seed.evidence, [seed], mode=mode, rid="seed")
    exact(f1["days"], 9)
    prior = prior_from(f1, ["days"], BASES["quote"], "seed", seed.evidence)
    # 위험 문장에도 그럴듯한 모델/규칙의 잘린 숫자 제안을 주입한다.
    entry = keep("days", suffix, "previous") if value == 9 else fresh("days", 6, suffix)
    vat = fresh("vat", "excluded", "부가세는 별도로 합니다.")
    f2, trace = run(
        monkeypatch, "quote", suffix + " " + vat.evidence, [entry, vat], mode=mode, prior=prior
    )
    exact(f2["vat"], "excluded")
    if value is None:
        withheld(f2["days"])
    elif value == 9:
        prove_carry(f2["days"], prior, trace)
    else:
        exact(f2["days"], value)
        assert f2["days"].origin_request_id == "new" and f2["days"].value_source != "previous"


@pytest.mark.parametrize("mode", MODES)
def test_derived_correction_metadata_survives_independent_crew(monkeypatch, mode):
    current = BASES["dates"]
    old = [day(8), day(19)]
    seed = fresh("days", old, "작업일은 12월 8일과 19일로 정합니다.")
    f1, _ = run(monkeypatch, "dates", seed.evidence, [seed], mode=mode, rid="seed")
    exact(f1["days"], old)
    prior = prior_from(f1, ["days"], current, "seed", seed.evidence)
    text = "이전 작업일 중 19일을 22일로 옮겨 주세요."
    f2, trace = run(
        monkeypatch, "dates", text, [hold("days", text)], mode=mode, prior=prior, rid="edit"
    )
    exact(f2["days"], [day(8), day(22)])
    assert f2["days"].value_source == "derived"
    assert f2["days"].correction is not None
    assert f2["days"].correction.rejected_value == old
    assert "19일" in f2["days"].correction.rejected_evidence
    assert "22일" in f2["days"].correction.accepted_evidence
    assert f2["days"].origin_request_id == "edit"
    revised = prior_from(f2, ["days"], current, "edit", seed.evidence + "\n" + text)
    crew = fresh("crew", 5, "작업 인원은 5명으로 정합니다.")
    f3, trace = run(monkeypatch, "dates", crew.evidence, [crew], mode=mode, prior=revised)
    prove_carry(f3["days"], revised, trace)
    exact(f3["crew"], 5)


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("verb", ["변경합니다", "확정합니다"])
@pytest.mark.parametrize(
    "reference,latest,value,expected",
    [
        ("직전 후보", 9, 9, 9),
        ("직전 후보", 9, 6, None),
        ("처음 제안한 일수", 9, 9, 9),
        ("처음 제안한 일수", 7, 7, None),
        ("직전 후보", 7, 7, 7),
    ],
)
def test_linked_retention_assignment_uses_verified_reference(
    monkeypatch, mode, verb, reference, latest, value, expected
):
    current = BASES["quote"]
    seed = fresh("days", 9, "공사 일수는 9일로 정합니다.")
    f1, _ = run(monkeypatch, "quote", seed.evidence, [seed], mode=mode, rid="first")
    exact(f1["days"], 9)
    prior = prior_from(f1, ["days"], current, "first", seed.evidence)
    if latest != 9:
        updated = fresh("days", latest, f"공사 일수는 {latest}일로 변경합니다.")
        f2, _ = run(
            monkeypatch,
            "quote",
            updated.evidence,
            [updated],
            mode=mode,
            prior=prior,
            rid="second",
        )
        exact(f2["days"], latest)
        prior = prior_from(f2, ["days"], current, "second", seed.evidence + "\n" + updated.evidence)
    tail = f"{value}일로 {verb}."
    vat = fresh("vat", "excluded", "부가세는 별도로 합니다.")
    text = f"공사 일수는 {reference} 그대로 두고 {tail} {vat.evidence}"
    fields, trace = run(
        monkeypatch,
        "quote",
        text,
        [fresh("days", value, tail), vat],
        mode=mode,
        prior=prior,
    )
    proof = next(f for f in trace["previous_verified"]["fields"] if f["field"] == "days")
    assert proof["value"] == latest
    assert trace["previous_verified"]["origins"]["days"] == prior.origins["days"]
    exact(fields["vat"], "excluded")
    if expected is None:
        withheld(fields["days"])
    else:
        exact(fields["days"], expected)
