"""추가 항목/사유의 실제 값 경계와 후보 이력 검증. 외부 호출 없음."""

import asyncio
import json
import os
import sys
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
    CorrectionRelation,
    FieldIntent,
    ModelInterpretation,
    PreviousCandidate,
)
from app.vendor.assist_service import interpret

CURRENT = {"title": "보강", "reason": "현장 추가", "amount": 1000000, "vat": "included"}
OPTIONS = {
    "today": "2026-11-25",
    "baseMonth": "2026-12-01",
    "daysMax": 120,
    "crewMax": 6,
    "vatModes": ["included", "excluded", "exempt"],
}
RESOURCE = UUID("00000000-0000-4000-8000-000000000179")
AMOUNT_CLAUSE = "비용은 34만원으로 변경합니다."
AMOUNT = 340000

# label, exact value, assignment ending, malformed value including a clipped directive
BOUNDARIES = [
    ("title", "추가 항목명은", "창틀 하단 보수", "로 다시 확정합니다", "창틀 하단 보수로 다시"),
    ("title", "작업명은", "벽체 모서리 보강", "으로 우선 정합니다", "벽체 모서리 보강으로 우선"),
    ("title", "항목 이름은", "현관 몰딩 교체", "로 최종 결정합니다", "현관 몰딩 교체로 최종"),
    ("title", "제목은", "문틀 흔들림 보수", "라고 새로 기재합니다", "문틀 흔들림 보수라고 새로"),
    ("reason", "추가 사유는", "내부 결로 발생", "으로 다시 작성합니다", "내부 결로 발생으로 다시"),
    ("reason", "사유는", "기존 타일 들뜸", "으로 먼저 기록해주세요", "기존 타일 들뜸으로 먼저"),
    (
        "reason",
        "이유는",
        "접착제 경화 불량",
        "으로 최종적으로 정합니다",
        "접착제 경화 불량으로 최종적으로",
    ),
    ("reason", "추가 사유는", "외벽 균열 확인", "으로 새로 지정 바랍니다", "외벽 균열 확인으로 새"),
]

# 정상 명사구 및 명시 인용값. 명령형과 필드명도 인용값 내부에서는 리터럴이다.
LITERALS = [
    ("title", "항목명: 확정 도면 기준 보강", "확정 도면 기준 보강"),
    ("title", "항목명은 새로 교체할 몰딩으로 정합니다.", "새로 교체할 몰딩"),
    ("title", "제목은 철거 후 새 마감으로 확정합니다.", "철거 후 새 마감"),
    ("title", "제목은 '몰딩으로 새'로 지정해주세요.", "몰딩으로 새"),
    ("reason", "사유: 기존 도면 확인 누락", "기존 도면 확인 누락"),
    ("reason", "사유는 최종 확정 도면 누락으로 기재합니다.", "최종 확정 도면 누락"),
    ("title", '제목은 "사유는 별도, 부가세는 포함"으로 정합니다.', "사유는 별도, 부가세는 포함"),
    (
        "reason",
        "추가 사유는 ‘벽체 보수로 다시 정합니다’로 적어주세요.",
        "벽체 보수로 다시 정합니다",
    ),
    ("reason", "사유는 “점검 완료. 균열 재발”로 기재합니다.", "점검 완료. 균열 재발"),
    ("title", "항목명은 '보강, 도장'으로 새로 확정합니다.", "보강, 도장"),
]

# 명확한 의도가 없는 원문에 유리한 부분만 제시한 경우.
NONASSIGNMENTS = [
    ("title", "제목은 벽체 도장으로 다시 정할까요?", "벽체 도장"),
    ("title", "가능하면 제목은 벽체 도장으로 정해주세요.", "벽체 도장"),
    ("title", "제목은 벽체 도장으로 정하지 마세요.", "벽체 도장"),
    ("title", "제목은 벽체 도장으로 정합니다. 제목 변경은 취소합니다.", "벽체 도장"),
    ("reason", "사유는 외벽 누수로 할지 검토 중입니다.", "외벽 누수"),
    ("reason", "사유는 외벽 누수로 기록할까요?", "외벽 누수"),
    ("reason", "사유는 외벽 누수로 작성하지 마세요.", "외벽 누수"),
    ("reason", "사유는 외벽 누수로 다시 정", "외벽 누수로 다시"),
    ("title", "제목은 '벽체 도장으로 새로 정합니다.", "벽체 도장"),
    ("reason", "사유는 결로 재발로 정합니다. 제목은 창호 보강으로 정합니다.", "창호 보강"),
    ("title", "제목은 앞서 제안한 값으로 정합니다.", "앞서 제안한 값"),
]


def field(
    name, action="keep", value=None, evidence="", *, intent=None, source=None, correction=None
):
    return FieldIntent(
        field=name,
        action=action,
        value=value,
        evidence=evidence,
        intent_kind=intent,
        value_source=source,
        correction=correction,
    )


def setting(name, value, evidence, correction=None):
    return field(
        name,
        "set",
        value,
        evidence,
        intent="explicit_change",
        source="utterance",
        correction=correction,
    )


def retain(name, evidence, reference="previous"):
    return field(name, evidence=evidence, intent="retain_" + reference, source=reference)


def model_fields(candidates):
    return [
        next((f for f in candidates if f.field == name), field(name, intent="no_mention"))
        for name in FORM_FIELDS["extra"]
    ]


def context():
    return AssistContext(today=date(2026, 11, 25), base_month=date(2026, 12, 1), crew_max=6)


async def service(monkeypatch, request, model, use_model):
    assert not settings.llm_live
    expected_root = os.environ.get("R6NEXTEND_TEST_ROOT")
    if expected_root:
        assert (
            Path(sys.modules["app.vendor.assist_service"].__file__)
            .resolve()
            .is_relative_to(Path(expected_root).resolve())
        )
    calls = []

    async def forbidden(*args, **kwargs):
        pytest.fail("provider must never be called")

    async def stub(*args, **kwargs):
        calls.append(args)
        return ModelInterpretation(fields=model_fields(model))

    monkeypatch.setattr(llm, "structured", forbidden)
    monkeypatch.setattr(vendor_assist, "interpret", stub)
    before = request.model_dump(mode="json")
    trace = {}
    response = await interpret(request, context(), use_model=use_model, trace=trace)
    assert len(calls) == int(use_model)
    assert response.source == ("llm" if use_model else "rule")
    assert request.model_dump(mode="json") == before
    assert response.request_id == request.request_id
    trace["response_warnings"] = response.warnings
    return {f.field: f for f in response.fields}, trace


def run(
    monkeypatch, text, rules, model=(), *, use_model=True, prior=None, current=None, rid="turn"
):
    request = AssistRequest(
        form="extra",
        request_id=rid,
        resource_id=RESOURCE,
        text=text,
        current=CURRENT if current is None else current,
        previous_candidate=prior,
        rule_fields=list(rules),
    )
    return asyncio.run(service(monkeypatch, request, model, use_model))


def exact(f, value):
    assert (f.status, f.action, f.value) == ("valid", "set", value), f
    assert not f.alternatives


def no_offer(f):
    assert f.action not in ("set", "clear") and not f.alternatives, f


def only_values(f, allowed):
    assert f.action != "clear" and all(a.action == "set" for a in f.alternatives), f
    offered = [f.value] if f.status == "valid" and f.action == "set" else []
    offered += [alt.value for alt in f.alternatives if alt.action == "set"]
    assert all(value in allowed for value in offered), f


def with_amount(text):
    return text.rstrip(".") + ("" if text.endswith("?") else ".") + " " + AMOUNT_CLAUSE


def make_prior(text, rid, fields, current=None):
    current = CURRENT if current is None else current
    selected = [f for f in fields.values() if f.status == "valid" and f.action == "set"]
    return PreviousCandidate(
        request_id=rid,
        text=text,
        fields=[field(f.field, "set", f.value, f.evidence) for f in selected],
        sources={f.field: f.source for f in selected},
        origins={f.field: f.origin_request_id for f in selected},
        baselines={f.field: current[f.field] for f in selected},
    )


@pytest.mark.parametrize("name,label,value,ending,clipped", BOUNDARIES)
@pytest.mark.parametrize("mode", ["rule-bad", "model-bad", "both-bad", "rule-good", "model-good"])
def test_complete_value_boundary(monkeypatch, name, label, value, ending, clipped, mode):
    clause = f"{label} {value}{ending}"
    text = with_amount(clause)
    money = setting("amount", AMOUNT, AMOUNT_CLAUSE)
    good = setting(name, value, clause)
    # 잘린 근거도 원래 문장의 실제 경계로 확인해야 한다.
    bad = setting(name, clipped, f"{label} {clipped}")
    if mode == "rule-bad":
        rules, model, use_model = [bad, money], [good, money], True
    elif mode == "model-bad":
        rules, model, use_model = [good, money], [bad, money], True
    elif mode == "both-bad":
        rules, model, use_model = [bad, money], [bad, money], True
    elif mode == "rule-good":
        rules, model, use_model = [good, money], [], False
    else:
        rules, model, use_model = [money], [good, money], True
    fields, _ = run(monkeypatch, text, rules, model, use_model=use_model)
    exact(fields["amount"], AMOUNT)
    if mode == "both-bad":
        no_offer(fields[name])
    else:
        exact(fields[name], value)


@pytest.mark.parametrize("name,label,value,ending,clipped", BOUNDARIES)
@pytest.mark.parametrize("malformed", ["prefix", "directive", "label", "clipped"])
@pytest.mark.parametrize("use_model", [False, True])
def test_incomplete_or_contaminated_text_is_not_a_candidate(
    monkeypatch, name, label, value, ending, clipped, malformed, use_model
):
    clause = f"{label} {value}{ending}"
    wrong = {
        "prefix": value.rsplit(" ", 1)[0],
        "directive": value + ending,
        "label": label + " " + value,
        "clipped": clipped,
    }[malformed]
    bad = setting(name, wrong, clause)
    money = setting("amount", AMOUNT, AMOUNT_CLAUSE)
    fields, _ = run(
        monkeypatch, with_amount(clause), [bad, money], [bad, money], use_model=use_model
    )
    exact(fields["amount"], AMOUNT)
    no_offer(fields[name])


@pytest.mark.parametrize("name,text,value", LITERALS)
@pytest.mark.parametrize("use_model", [False, True])
def test_nominal_and_quoted_value_is_preserved(monkeypatch, name, text, value, use_model):
    good = setting(name, value, text)
    money = setting("amount", AMOUNT, AMOUNT_CLAUSE)
    fields, _ = run(
        monkeypatch, with_amount(text), [good, money], [good, money], use_model=use_model
    )
    exact(fields[name], value)
    exact(fields["amount"], AMOUNT)


@pytest.mark.parametrize("name,text,value", LITERALS)
def test_literal_substring_cannot_be_promoted(monkeypatch, name, text, value):
    if " " not in value:
        pytest.fail("control must contain more than one word")
    bad = setting(name, value.split(" ", 1)[0], text)
    good = setting(name, value, text)
    fields, _ = run(monkeypatch, text, [bad], [good])
    exact(fields[name], value)


@pytest.mark.parametrize("name,text,value", NONASSIGNMENTS)
@pytest.mark.parametrize("use_model", [False, True])
def test_uncertain_foreign_or_incomplete_assignment(monkeypatch, name, text, value, use_model):
    bad = setting(name, value, text)
    money = setting("amount", AMOUNT, AMOUNT_CLAUSE)
    fields, _ = run(
        monkeypatch, AMOUNT_CLAUSE + " " + text, [bad, money], [bad, money], use_model=use_model
    )
    no_offer(fields[name])
    exact(fields["amount"], AMOUNT)


@pytest.mark.parametrize(
    "name,label,old,new",
    [
        ("title", "제목은", "문틀 보강", "창틀 보강"),
        ("reason", "사유는", "결로 재발", "접착 불량"),
    ],
)
def test_competing_values_and_verified_correction(monkeypatch, name, label, old, new):
    # 두 값 모두 실제 원문에 있고 정정 관계를 증명할 수 있는 경우.
    text = f"{label} {old} 말고 {new}로 변경합니다."
    a = setting(name, old, old)
    b = setting(name, new, new)
    fields, _ = run(monkeypatch, text, [a], [b])
    only_values(fields[name], {old, new})
    if fields[name].status == "conflict":
        assert {alt.value for alt in fields[name].alternatives} == {old, new}
    else:
        assert not (fields[name].status == "valid" and fields[name].value == old)
    correction = CorrectionRelation(
        kind="replace", rejected_value=old, rejected_evidence=old, accepted_evidence=new
    )
    fields, _ = run(monkeypatch, text, [a], [setting(name, new, new, correction)])
    exact(fields[name], new)

    uncertain = f"{label} {old} 또는 {new} 중 고민 중입니다."
    fields, _ = run(monkeypatch, uncertain, [a], [b])
    no_offer(fields[name])


def test_actual_grounded_conflict_without_relation_preserves_both_values(monkeypatch):
    old, new = "현관 문틀 보강", "창호 틀 보수"
    text = old + " 말고 " + new
    a, b = setting("title", old, old), setting("title", new, new)
    fields, _ = run(monkeypatch, text, [a], [b])
    f = fields["title"]
    assert f.status == "conflict" and f.action == "uncertain", f
    assert {alt.value for alt in f.alternatives} == {old, new}, f
    assert {alt.source for alt in f.alternatives} == {"rule", "llm"}, f


@pytest.mark.parametrize("name,label,value,ending,clipped", [BOUNDARIES[1], BOUNDARIES[6]])
@pytest.mark.parametrize("use_model", [False, True])
@pytest.mark.parametrize("state", ["verified", "missing", "stale", "canceled", "contaminated"])
def test_history_revalidates_exact_value_and_preserves_amount(
    monkeypatch, name, label, value, ending, clipped, use_model, state
):
    clause = f"{label} {value}{ending}"
    seed = with_amount(clause)
    good = setting(name, value, clause)
    money = setting("amount", AMOUNT, AMOUNT_CLAUSE)
    initial, _ = run(
        monkeypatch, seed, [good, money], [good, money], use_model=use_model, rid="seed"
    )
    exact(initial[name], value)
    exact(initial["amount"], AMOUNT)
    prior = make_prior(seed, "seed", initial)
    current = CURRENT.copy()
    if state == "missing":
        prior = prior.model_copy(
            update={
                "fields": [f for f in prior.fields if f.field != name],
                "origins": {"amount": prior.origins["amount"]},
                "sources": {"amount": prior.sources["amount"]},
                "baselines": {"amount": CURRENT["amount"]},
            }
        )
    elif state == "stale":
        current[name] = "수동으로 편집한 내용"
    elif state == "canceled":
        cancellation = label + " 변경은 취소합니다."
        canceled, _ = run(
            monkeypatch,
            cancellation,
            [],
            [retain(name, cancellation, "current")],
            use_model=use_model,
            prior=prior,
            rid="canceled",
        )
        no_offer(canceled[name])
        exact(canceled["amount"], AMOUNT)
        # 실제로 취소된 이력을 포함한 상태에서 이전 후보를 위조해도 복구하면 안 된다.
        prior = prior.model_copy(update={"text": seed + "\n" + cancellation})
    elif state == "contaminated":
        prior = prior.model_copy(
            update={
                "fields": [
                    field(name, "set", clipped, clause) if f.field == name else f
                    for f in prior.fields
                ]
            }
        )
    text = label + " 앞서 제안한 값으로 해주세요. 비용은 앞서 제안한 값으로 해주세요."
    model = [
        retain(name, label + " 앞서 제안한 값으로 해주세요"),
        retain("amount", "비용은 앞서 제안한 값으로 해주세요"),
    ]
    fields, _ = run(
        monkeypatch, text, [], model, use_model=use_model, prior=prior, current=current, rid="next"
    )
    exact(fields["amount"], AMOUNT)
    assert fields["amount"].origin_request_id == "seed"
    if state == "verified":
        exact(fields[name], value)
        assert fields[name].origin_request_id == "seed"
    else:
        no_offer(fields[name])


# 실행된 extra-09 T1~T3의 원문과 저장 모델 결과만 재현한다.
CALLED = [
    (
        "추가 비용은 91만 3천 원으로 후보만 내주세요.",
        [setting("amount", 913000, "91만 3천 원")],
    ),
    (
        "항목 이름은 앞에서 정했던 걸로 유지 바랍니다.",
        [
            field(
                "title",
                "uncertain",
                evidence="항목 이름은 앞에서 정했던 걸로 유지 바랍니다",
                intent="ambiguous",
            )
        ],
    ),
    (
        "추가 항목명은 선반 하부 보강으로 새로 정합니다. 비용은 앞서 제안한 값으로 해주세요.",
        [
            setting("title", "선반 하부 보강", "추가 항목명은 선반 하부 보강으로 새로 정합니다"),
            retain("amount", "비용은 앞서 제안한 값으로 해주세요"),
        ],
    ),
]


@pytest.mark.parametrize("use_model", [False, True])
def test_executed_extra09_evolving_prior(monkeypatch, use_model):
    prior = None
    history = []
    origin = None
    for i, (text, model) in enumerate(CALLED, 1):
        rid = f"extra09-{i}"
        history.append(text)
        rules = (
            [field("amount", "set", 913000, text)]
            if i == 1
            else [field("title", "set", "선반 하부 보강으로 새", text)]
            if i == 3
            else []
        )
        fields, _ = run(monkeypatch, text, rules, model, use_model=use_model, prior=prior, rid=rid)
        exact(fields["amount"], 913000)
        origin = origin or rid
        assert fields["amount"].origin_request_id == origin
        if i < 3:
            no_offer(fields["title"])
        elif use_model:
            exact(fields["title"], "선반 하부 보강")
        else:
            only_values(fields["title"], {"선반 하부 보강"})
        prior = make_prior("\n".join(history), rid, fields)


async def bridge_run(root, bridge, node):
    """별도 CLI에서 실제 FE AssistSession과 실제 BE interpret를 연결한다."""
    loader = root / "frontend/components/pro/assist/test-loader.mjs"
    proc = await asyncio.create_subprocess_exec(
        str(node),
        "--experimental-strip-types",
        "--no-warnings",
        "--experimental-loader",
        loader.as_uri(),
        str(bridge),
        cwd=root,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
    )
    failures = 0
    total = 0

    async def send(msg):
        proc.stdin.write((json.dumps(msg, ensure_ascii=True) + "\n").encode())
        await proc.stdin.drain()

    async def recv(kind):
        line = await asyncio.wait_for(proc.stdout.readline(), 25)
        assert line, "session bridge stopped"
        msg = json.loads(line)
        assert msg["kind"] == kind, msg
        return msg

    async def scenario(sid, turns, use_model):
        nonlocal failures, total
        await send(
            {
                "form": "extra",
                "initial_current": CURRENT,
                "options": OPTIONS,
                "turns": [{"text": t[0], "apply": t[3]} for t in turns],
            }
        )
        for i, (text, model, expected, apply) in enumerate(turns, 1):
            request = AssistRequest.model_validate((await recv("request"))["request"])
            assert request.text == text
            with pytest.MonkeyPatch.context() as mp:
                fields, trace = await service(mp, request, model, use_model)
            await send(
                {
                    "response": {
                        "form": "extra",
                        "request_id": request.request_id,
                        "source": "llm" if use_model else "rule",
                        "fields": [f.model_dump(mode="json") for f in fields.values()],
                        "warnings": trace["response_warnings"],
                    }
                }
            )
            result = await recv("result")
            total += 1
            errors = []
            for layer, candidates in (
                ("be", [f.model_dump(mode="json") for f in fields.values()]),
                ("fe", result["fields"]),
            ):
                for f in candidates:
                    name = f["field"]
                    offered = (
                        [f["value"]] if f["status"] == "valid" and f["action"] == "set" else []
                    )
                    offered += [a["value"] for a in f["alternatives"] if a["action"] == "set"]
                    if f["action"] == "clear" or any(
                        a["action"] == "clear" for a in f["alternatives"]
                    ):
                        errors.append([layer, name, "unexpected_clear"])
                    if any(v != expected.get(name) for v in offered):
                        errors.append([layer, name, "unexpected_offer", offered])
                    if (
                        name in expected
                        and (use_model or name == "amount")
                        and offered != [expected[name]]
                    ):
                        errors.append([layer, name, "missing", offered, expected[name]])
            if not apply and result["pre"] != result["post"]:
                errors.append(["state", "preview_changed_form"])
            if result["after"]["pending"]:
                errors.append(["state", "pending_not_released"])
            if apply:
                for name, value in result["changes"].items():
                    if expected.get(name) != value:
                        errors.append(["state", name, "bad_apply", value])
                for name, value in expected.items():
                    if (use_model or name == "amount") and result["changes"].get(name) != value:
                        errors.append(["state", name, "missing_apply", value])
            failures += bool(errors)
            print(
                json.dumps(
                    {
                        "scenario": sid,
                        "mode": "stub" if use_model else "rule",
                        "turn": i,
                        "errors": errors,
                        "rule_raw": trace["rule_raw"],
                        "server": [f.model_dump(mode="json") for f in fields.values()],
                        "session": result,
                    },
                    ensure_ascii=True,
                ),
                flush=True,
            )

    try:
        for use_model in (False, True):
            await scenario(
                "executed-extra09",
                [
                    (
                        text,
                        model,
                        {"amount": 913000, **({"title": "선반 하부 보강"} if i == 3 else {})},
                        i == 3,
                    )
                    for i, (text, model) in enumerate(CALLED, 1)
                ],
                use_model,
            )
            for j, (name, label, value, ending, _) in enumerate(BOUNDARIES):
                text = f"{label} {value}{ending}."
                await scenario(
                    f"boundary-{j}",
                    [
                        (
                            with_amount(text),
                            [setting(name, value, text), setting("amount", AMOUNT, AMOUNT_CLAUSE)],
                            {name: value, "amount": AMOUNT},
                            True,
                        )
                    ],
                    use_model,
                )
            for j, (name, text, value) in enumerate(LITERALS):
                await scenario(
                    f"literal-{j}",
                    [
                        (
                            with_amount(text),
                            [setting(name, value, text), setting("amount", AMOUNT, AMOUNT_CLAUSE)],
                            {name: value, "amount": AMOUNT},
                            True,
                        )
                    ],
                    use_model,
                )
            for j, (name, text, value) in enumerate(NONASSIGNMENTS[:9]):
                await scenario(
                    f"nonassignment-{j}",
                    [
                        (
                            AMOUNT_CLAUSE + " " + text,
                            [setting(name, value, text), setting("amount", AMOUNT, AMOUNT_CLAUSE)],
                            {"amount": AMOUNT},
                            True,
                        )
                    ],
                    use_model,
                )
        await send({"stop": True})
        proc.stdin.close()
        assert await asyncio.wait_for(proc.wait(), 20) == 0
    finally:
        if proc.returncode is None:
            proc.terminate()
            await proc.wait()
    print(f"BRIDGE total={total} failed={failures}", flush=True)
    return failures


if __name__ == "__main__":
    assert len(sys.argv) == 4, "root bridge node"
    root, bridge, node = (Path(arg).resolve() for arg in sys.argv[1:])
    assert Path(sys.modules["app.vendor.assist_service"].__file__).resolve().is_relative_to(root)
    assert os.environ.get("LLM_LIVE", "").lower() == "false"
    raise SystemExit(bool(asyncio.run(bridge_run(root, bridge, node))))


# R6nextend 추가 독립 통제: 인용한 제목/사유 안의 금융 표현과 실제 별도 칸의 대입을 구분한다.
# 기존 181개 통제 및 60턴 브리지의 내용은 변경하지 않는다.
SCALAR_LITERAL_CASES = [
    (
        "title",
        "제목은 “현장 기록: 비용 23만원”으로 정합니다.",
        "현장 기록: 비용 23만원",
        (),
        (("amount", 230000, "비용 23만원"),),
    ),
    (
        "reason",
        "사유는 '부가세는 별도'로 기록합니다.",
        "부가세는 별도",
        (),
        (("vat", "excluded", "부가세는 별도"),),
    ),
    (
        "title",
        '항목명은 "부가세는 면세, 추가 비용은 29만원"으로 기재합니다.',
        "부가세는 면세, 추가 비용은 29만원",
        (
            ("amount", 340000, "비용은 34만원으로 변경합니다."),
            ("vat", "excluded", "세금은 별도로 처리합니다."),
        ),
        (
            ("amount", 290000, "추가 비용은 29만원"),
            ("vat", "exempt", "부가세는 면세"),
        ),
    ),
    (
        "reason",
        "추가 사유는 ‘금액은 27만원, 부가세는 포함’으로 작성합니다.",
        "금액은 27만원, 부가세는 포함",
        (
            ("amount", 370000, "추가 비용은 37만원으로 정합니다."),
            ("vat", "excluded", "부가세는 별도로 처리합니다."),
        ),
        (
            ("amount", 270000, "금액은 27만원"),
            ("vat", "included", "부가세는 포함"),
        ),
    ),
    (
        "title",
        "제목은 '금액은 34만원'으로 입력합니다.",
        "금액은 34만원",
        (("amount", 340000, "비용은 34만원으로 확정합니다."),),
        (("amount", 340000, "금액은 34만원"),),
    ),
    (
        "reason",
        '사유는 "부가세는 별도"로 기재합니다.',
        "부가세는 별도",
        (("vat", "excluded", "세금은 별도로 처리합니다."),),
        (("vat", "excluded", "부가세는 별도"),),
    ),
]


def literal_scalar_fixture(case):
    name, clause, value, outside, bad = case
    text = " ".join([clause, *(evidence for _, _, evidence in outside)])
    correct = [setting(name, value, clause), *(setting(*entry) for entry in outside)]
    return text, correct, bad


@pytest.mark.parametrize("case", SCALAR_LITERAL_CASES)
@pytest.mark.parametrize("mode", ["rule", "stub"])
def test_quoted_scalar_content_with_independent_outside_field(monkeypatch, case, mode):
    text, good, _ = literal_scalar_fixture(case)
    fields, _ = run(monkeypatch, text, good, good, use_model=mode == "stub")
    wanted = {f.field: f.value for f in good}
    for name, f in fields.items():
        if name in wanted:
            exact(f, wanted[name])
        else:
            no_offer(f)


@pytest.mark.parametrize("case", SCALAR_LITERAL_CASES)
@pytest.mark.parametrize("mode", ["rule", "stub"])
@pytest.mark.parametrize("evidence_kind", ["inner", "full"])
def test_quoted_scalar_evidence_cannot_change_another_field(monkeypatch, case, mode, evidence_kind):
    text, good, bad = literal_scalar_fixture(case)
    # 전체 원문에도 해당 스칼라가 실제 칸 대입으로 없을 때는 전체 근거 역시 거부한다.
    # 동일 값의 실제 외부 대입이 있을 때는 그 대입을 포함하는 전체 근거를 허용한다.
    wanted = {f.field: f.value for f in good}
    for target, value, evidence in bad:
        if evidence_kind == "full":
            evidence = text
        forged = setting(target, value, evidence)
        independent = [f for f in good if f.field != target]
        fields, _ = run(
            monkeypatch,
            text,
            [*independent, forged],
            [*independent, forged],
            use_model=mode == "stub",
        )
        for f in independent:
            exact(fields[f.field], f.value)
        if evidence_kind == "full" and wanted.get(target) == value:
            exact(fields[target], value)
        else:
            no_offer(fields[target])


@pytest.mark.parametrize("case", SCALAR_LITERAL_CASES[:4])
@pytest.mark.parametrize("mode", ["rule", "stub"])
def test_quoted_scalar_forged_previous_candidate_is_not_retained(monkeypatch, case, mode):
    text, good, bad = literal_scalar_fixture(case)
    target, wrong_value, wrong_evidence = bad[0]
    independent = [f for f in good if f.field != target]
    fields, _ = run(
        monkeypatch, text, independent, independent, use_model=mode == "stub", rid="quoted-seed"
    )
    for f in independent:
        exact(fields[f.field], f.value)
    prior = make_prior(text, "quoted-seed", fields)
    prior = prior.model_copy(
        update={
            "fields": [*prior.fields, field(target, "set", wrong_value, wrong_evidence)],
            "origins": {**prior.origins, target: "quoted-seed"},
            "sources": {**prior.sources, target: "rule"},
            "baselines": {**prior.baselines, target: CURRENT[target]},
        }
    )
    label = "비용은" if target == "amount" else "부가세는"
    follow = label + " 앞서 제안한 값으로 해주세요."
    fields, _ = run(
        monkeypatch,
        follow,
        [],
        [retain(target, follow)],
        use_model=mode == "stub",
        prior=prior,
        rid="quoted-next",
    )
    no_offer(fields[target])
    for f in independent:
        exact(fields[f.field], f.value)
        assert fields[f.field].origin_request_id == "quoted-seed"


@pytest.mark.parametrize("use_model", [False, True])
def test_quote_around_actual_scalar_is_not_a_text_field_literal(monkeypatch, use_model):
    text = '비용은 "34만원"으로 정합니다.'
    good = setting("amount", 340000, "34만원")
    fields, _ = run(monkeypatch, text, [good], [good], use_model=use_model)
    exact(fields["amount"], 340000)


async def scalar_literal_bridge_run(root, bridge, node):
    """추가 통제를 실제 규칙 파서/세션 및 백엔드 응답에 대조한다."""
    proc = await asyncio.create_subprocess_exec(
        str(node),
        "--experimental-strip-types",
        "--no-warnings",
        "--experimental-loader",
        (root / "frontend/components/pro/assist/test-loader.mjs").as_uri(),
        str(bridge),
        cwd=root,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
    )

    async def send(msg):
        proc.stdin.write((json.dumps(msg, ensure_ascii=True) + "\n").encode())
        await proc.stdin.drain()

    async def recv(kind):
        line = await asyncio.wait_for(proc.stdout.readline(), 25)
        assert line, "session bridge stopped"
        msg = json.loads(line)
        assert msg["kind"] == kind, msg
        return msg

    failures = 0
    total = 0
    try:
        for index, case in enumerate(SCALAR_LITERAL_CASES):
            text, good, bad = literal_scalar_fixture(case)
            expected = {f.field: f.value for f in good}
            for mode in ("rule", "stub", "forged-stub"):
                use_model = mode != "rule"
                wrong_targets = set()
                model = good
                if mode == "forged-stub":
                    wrong_targets = {entry[0] for entry in bad}
                    model = [
                        *(f for f in good if f.field not in wrong_targets),
                        *(setting(*entry) for entry in bad),
                    ]
                await send(
                    {
                        "form": "extra",
                        "initial_current": CURRENT,
                        "options": OPTIONS,
                        "turns": [{"text": text, "apply": True}],
                    }
                )
                request = AssistRequest.model_validate((await recv("request"))["request"])
                assert request.text == text
                with pytest.MonkeyPatch.context() as mp:
                    fields, trace = await service(mp, request, model, use_model)
                server = [f.model_dump(mode="json") for f in fields.values()]
                await send(
                    {
                        "response": {
                            "form": "extra",
                            "request_id": request.request_id,
                            "source": "llm" if use_model else "rule",
                            "fields": server,
                            "warnings": trace["response_warnings"],
                        }
                    }
                )
                result = await recv("result")
                errors = []
                required = set(expected) - wrong_targets
                for layer, candidates in (("be", server), ("fe", result["fields"])):
                    for f in candidates:
                        name = f["field"]
                        offered = (
                            [f["value"]] if f["status"] == "valid" and f["action"] == "set" else []
                        )
                        offered += [a["value"] for a in f["alternatives"] if a["action"] == "set"]
                        if f["action"] == "clear" or any(
                            a["action"] == "clear" for a in f["alternatives"]
                        ):
                            errors.append([layer, name, "unexpected_clear"])
                        if any(name not in expected or v != expected[name] for v in offered):
                            errors.append([layer, name, "unexpected_offer", offered])
                        if (
                            name in required
                            and (mode != "rule" or name in ("amount", "vat"))
                            and offered != [expected[name]]
                        ):
                            errors.append([layer, name, "missing", offered, expected[name]])
                for name, value in result["changes"].items():
                    if name not in expected or value != expected[name]:
                        errors.append(["state", name, "bad_apply", value])
                for name in required:
                    if (mode != "rule" or name in ("amount", "vat")) and result["changes"].get(
                        name
                    ) != expected[name]:
                        errors.append(["state", name, "missing_apply", expected[name]])
                if result["after"]["pending"]:
                    errors.append(["state", "pending_not_released"])
                total += 1
                failures += bool(errors)
                print(
                    json.dumps(
                        {
                            "scenario": f"scalar-literal-{index}",
                            "mode": mode,
                            "errors": errors,
                            "rule_raw": trace["rule_raw"],
                            "server": server,
                            "session": result,
                        },
                        ensure_ascii=True,
                    ),
                    flush=True,
                )
        await send({"stop": True})
        proc.stdin.close()
        assert await asyncio.wait_for(proc.wait(), 20) == 0
    finally:
        if proc.returncode is None:
            proc.terminate()
            await proc.wait()
    print(f"SCALAR_BRIDGE total={total} failed={failures}", flush=True)
    return failures


@pytest.mark.parametrize("use_model", [False, True])
@pytest.mark.parametrize(
    ("name", "text", "value", "clipped"),
    [
        ("title", "제목은 '외벽 보수.'로 다시 정합니다.", "외벽 보수.", "외벽 보수"),
        ("reason", "사유는 “누수 확인!”으로 기재합니다.", "누수 확인!", "누수 확인"),
    ],
)
@pytest.mark.parametrize("exact_value", [True, False])
def test_quoted_terminal_punctuation_is_part_of_value(
    monkeypatch, use_model, name, text, value, clipped, exact_value
):
    chosen = value if exact_value else clipped
    candidates = [setting(name, chosen, text), setting("amount", AMOUNT, AMOUNT_CLAUSE)]
    fields, _ = run(
        monkeypatch, text + " " + AMOUNT_CLAUSE, candidates, candidates, use_model=use_model
    )
    if exact_value:
        exact(fields[name], value)
    else:
        no_offer(fields[name])
    exact(fields["amount"], AMOUNT)
