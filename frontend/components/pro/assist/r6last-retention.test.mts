// 실행된 quote-09 및 독립 대조. 실제 세션, 서버 응답만 스텁.
import assert from "node:assert/strict";
import { test } from "node:test";
import { AssistSession } from "./session.ts";
import { formKeys, type AssistForm, type AssistCurrent, type AssistRequest, type AssistValue, type CandidateField } from "./protocol.ts";
import { emptyFormHolds, mergeFormHolds, manuallyResolveHolds } from "./formHolds.ts";

type Proposed = { field: string; value: AssistValue; evidence: string };
type Case = {
  form: AssistForm; current: AssistCurrent; seed: string; other: Proposed; retain: Proposed;
  subject: string; uncertain: string; wrongType: string; changed: AssistValue; replacement: string;
};
type Fields = Record<string, Partial<CandidateField>>;
const cases: Case[] = [
  {
    form: "quote", current: { amount: 4550000, vat: "included", days: 3, valid: "2026-12-17" },
    seed: "일단 견적 금액은 693만 5천 원, 기간은 12일로 제시할게요.",
    other: { field: "amount", value: 6935000, evidence: "견적 금액은 693만 5천 원" },
    retain: { field: "days", value: 12, evidence: "기간은 12일" },
    subject: "기간은", uncertain: "금액 710만원이나 730만원 중에 고민하고 있어요",
    wrongType: "금액", changed: 9, replacement: "기간은 9일로 변경합니다.",
  },
  {
    form: "period", current: { start: "2026-12-02", end: "2026-12-24", excluded: [] },
    seed: "시작일은 12월 5일, 종료일은 12월 22일로 제안합니다.",
    other: { field: "start", value: "2026-12-05", evidence: "시작일은 12월 5일" },
    retain: { field: "end", value: "2026-12-22", evidence: "종료일은 12월 22일" },
    subject: "종료일은", uncertain: "시작일은 12월 3일이나 4일 중에 고민하고 있어요",
    wrongType: "금액", changed: "2026-12-23", replacement: "종료일은 12월 23일로 변경합니다.",
  },
  {
    form: "dates", current: { days: ["2026-12-09", "2026-12-16"], crew: 2 },
    seed: "시공 날짜는 12월 8일과 21일, 인원은 3명으로 제안합니다.",
    other: { field: "crew", value: 3, evidence: "인원은 3명" },
    retain: { field: "days", value: ["2026-12-08", "2026-12-21"], evidence: "시공 날짜는 12월 8일과 21일" },
    subject: "시공 날짜는", uncertain: "인원은 4명이나 5명 중에 고민하고 있어요",
    wrongType: "금액", changed: ["2026-12-11", "2026-12-24"], replacement: "시공 날짜는 12월 11일과 24일로 변경합니다.",
  },
  {
    form: "extra", current: { title: "보강", reason: "현장 추가", amount: 1000000, vat: "included" },
    seed: "추가 사유는 문짝 하단 긁힘, 비용은 28만 6천 원으로 먼저 띄워주세요.",
    other: { field: "amount", value: 286000, evidence: "비용은 28만 6천 원" },
    retain: { field: "reason", value: "문짝 하단 긁힘", evidence: "추가 사유는 문짝 하단 긁힘" },
    subject: "사유는", uncertain: "추가금 31만원이나 35만원 중에 고민하고 있어요",
    wrongType: "인원", changed: "바닥 파손", replacement: "사유는 바닥 파손으로 변경합니다.",
  },
];
const variants = [
  "앞서 잡은 대로 둬주세요",
  "앞서 잡은대로 둬 주세요",
  "앞서 제시한 값 그대로 둬 주세요",
  "앞서 정한 대로 두세요",
  "앞서 잡은 대로 두어 주세요",
  "앞서 제안한 값 그대로 둘게요",
  "앞서 정한 대로 두겠습니다",
];

function fixture(c: Case) {
  let current = { ...c.current }, holds = emptyFormHolds(), writes = 0;
  const manualVersions: Record<string, number> = {};
  const opts = {
    today: "2026-11-25", baseMonth: "2026-12-01", daysMax: 120, crewMax: 6,
    requiredDays: c.form === "dates" ? 2 : null,
    vatModes: c.form === "extra" ? ["included", "excluded"] : ["included", "excluded", "exempt"],
  };
  let job: { request: AssistRequest; resolve: (v: unknown) => void } | undefined;
  const session = new AssistSession(c.form, "r6last-retention", request =>
    new Promise(resolve => { job = { request, resolve }; }));
  function send(text: string) {
    const before = { ...current }, count = writes;
    assert(session.send(text, current, opts, issues => {
      holds = mergeFormHolds(c.form, holds, issues);
    }, () => ({ current, manualVersions })));
    assert(job);
    assert(holds.pending);
    assert.equal(session.apply(current, () => assert.fail("응답 전 적용")), false);
    assert.deepEqual(current, before);
    assert.equal(writes, count);
    return job.request;
  }
  async function resolve(fields: Fields) {
    assert(job);
    const req = job.request, before = { ...current }, count = writes;
    job.resolve({
      form: c.form, request_id: req.request_id, source: "llm", warnings: [],
      fields: formKeys(c.form).map(field => ({
        field, status: "valid", action: "keep", value: null, evidence: "",
        source: "rule", alternatives: [], origin_request_id: req.request_id,
        intent_kind: "no_mention", value_source: null, ...fields[field],
      })),
    });
    await Promise.resolve();
    await Promise.resolve();
    assert.equal(session.view.phase, "ready");
    assert.equal(holds.pending, false);
    assert.deepEqual(current, before);
    assert.equal(writes, count);
  }
  function field(name: string) {
    const candidate = session.view.response?.fields.find(f => f.field === name);
    assert(candidate);
    return candidate;
  }
  function apply(expected: AssistCurrent) {
    let patch: AssistCurrent = {};
    const changed = Object.keys(expected).length > 0, count = writes;
    assert.equal(session.apply(current, values => {
      writes++;
      patch = values;
      current = { ...current, ...values };
    }), changed);
    assert.deepEqual(patch, expected);
    assert.equal(writes, count + Number(changed));
    if (changed) assert.equal(session.apply(current, () => assert.fail("중복 적용")), false);
  }
  function manual(name: string, value: AssistValue) {
    current = { ...current, [name]: value };
    manualVersions[name] = (manualVersions[name] ?? 0) + 1;
    holds = manuallyResolveHolds(holds, [name]);
  }
  return { session, send, resolve, field, apply, manual, get holds() { return holds; }, get current() { return current; }, get writes() { return writes; } };
}

function set(p: Proposed): Partial<CandidateField> {
  return { action: "set", value: p.value, evidence: p.evidence, intent_kind: "explicit_change", value_source: "utterance", source: "llm" };
}
function carry(p: Proposed, origin: string): Partial<CandidateField> {
  return { ...set(p), intent_kind: "retain_previous", value_source: "previous", origin_request_id: origin };
}
function keep(evidence: string): Partial<CandidateField> {
  return { action: "keep", value: null, evidence, intent_kind: "retain_current", value_source: "current" };
}
function uncertain(evidence: string): Partial<CandidateField> {
  return { status: "uncertain", action: "uncertain", value: null, evidence, intent_kind: "ambiguous", source: "llm" };
}
function noOffer(candidate: CandidateField) {
  assert.notEqual(candidate.action, "set");
  assert.notEqual(candidate.action, "clear");
  assert.deepEqual(candidate.alternatives, []);
}
async function seeded(c: Case, missing = false) {
  const f = fixture(c), req = f.send(c.seed);
  await f.resolve({
    [c.other.field]: set(c.other),
    [c.retain.field]: missing ? uncertain(c.retain.evidence) : set(c.retain),
  });
  assert.equal(f.field(c.other.field).status, "valid");
  assert.deepEqual(f.field(c.other.field).value, c.other.value);
  if (!missing) {
    assert.equal(f.field(c.retain.field).status, "valid");
    assert.deepEqual(f.field(c.retain.field).value, c.retain.value);
  }
  assert.ok(f.holds.fields[c.other.field]);
  assert.ok(f.holds.fields[c.retain.field]);
  return { f, req };
}
function textFor(c: Case, phrase: string) {
  return c.uncertain + ". " + c.subject + " " + phrase + ".";
}
function assertHeld(f: ReturnType<typeof fixture>, c: Case) {
  noOffer(f.field(c.other.field));
  assert.ok(f.holds.fields[c.other.field]);
}

for (const c of cases) {
  for (const phrase of variants) {
    test(c.form + ": 독립 불확실성과 이전 후보 유지 " + phrase, async () => {
      const { f, req } = await seeded(c);
      const next = f.send(textFor(c, phrase));
      assert.equal(next.previous_candidate?.origins?.[c.retain.field], req.request_id);
      assert.deepEqual(next.previous_candidate?.baselines?.[c.retain.field], c.current[c.retain.field]);
      await f.resolve({ [c.other.field]: uncertain(c.uncertain), [c.retain.field]: carry(c.retain, req.request_id) });
      assertHeld(f, c);
      assert.equal(f.field(c.retain.field).status, "valid");
      assert.deepEqual(f.field(c.retain.field).value, c.retain.value);
      assert.equal(f.field(c.retain.field).evidence, c.retain.evidence);
      assert.equal(f.field(c.retain.field).origin_request_id, req.request_id);
      assert.ok(f.holds.fields[c.retain.field]);
      // 정확한 quote-09 T2의 gold처럼 먼저 적용하지 않는다.
      assert.equal(f.writes, 0);
      assert.deepEqual(f.current, c.current);
      f.session.select(c.retain.field, false);
      f.apply({});
      f.session.select(c.retain.field, true);
      f.apply({ [c.retain.field]: c.retain.value });
      assert.deepEqual(f.current[c.other.field], c.current[c.other.field]);
      assert.ok(f.holds.fields[c.other.field]);
      assert.equal(f.holds.fields[c.retain.field], undefined);
    });
  }
  test(c.form + ": 현재 입력값 명시 유지, 이전 후보 미복원", async () => {
    const { f } = await seeded(c);
    const evidence = c.subject + " 현재 입력값 그대로 둬 주세요";
    f.send(c.uncertain + ". " + evidence + ".");
    await f.resolve({ [c.other.field]: uncertain(c.uncertain), [c.retain.field]: keep(evidence) });
    noOffer(f.field(c.retain.field));
    assert.equal(f.holds.fields[c.retain.field], undefined);
    assertHeld(f, c);
    f.apply({});
    assert.deepEqual(f.current, c.current);
  });

  for (const mode of [
    "missing", "same-manual", "changed-manual", "wrong-type", "forged-origin", "forged-source",
    "forged-evidence", "forged-value", "manual-during", "current-vs-forged-prior",
  ] as const) {
    test(c.form + ": 잘못된 이전 후보 " + mode, async () => {
      const { f, req } = await seeded(c, mode === "missing");
      let phrase = variants[0];
      if (mode === "same-manual") f.manual(c.retain.field, c.current[c.retain.field]);
      if (mode === "changed-manual") f.manual(c.retain.field, c.changed);
      if (mode === "wrong-type") phrase = "앞서 정한 " + c.wrongType + " 그대로 둬주세요";
      if (mode === "current-vs-forged-prior") phrase = "현재 입력값 그대로 둬 주세요";
      const next = f.send(textFor(c, phrase));
      if (mode === "missing" || mode.endsWith("-manual")) {
        assert.ok(!next.previous_candidate?.fields.some(field => field.field === c.retain.field));
      }
      const proposed = carry(c.retain, req.request_id);
      if (mode === "forged-origin") proposed.origin_request_id = "unrelated";
      if (mode === "forged-source") proposed.source = "rule";
      if (mode === "forged-evidence") proposed.evidence = "원문에 없는 근거";
      if (mode === "forged-value") proposed.value = c.changed;
      if (mode === "manual-during") f.manual(c.retain.field, c.changed);
      const before = { ...f.current };
      await f.resolve({ [c.other.field]: uncertain(c.uncertain), [c.retain.field]: proposed });
      if (mode !== "manual-during") noOffer(f.field(c.retain.field));
      assertHeld(f, c);
      f.apply({});
      assert.deepEqual(f.current, before);
    });
  }
  for (const suffix of [
    "둬도 될까요?", "두지 마세요.", "안 둘게요.", "두면 좋을 것 같아요.",
    "둬주세요. {label} 변경은 취소합니다.", "둬주세요. {replacement}",
    "conditional",
  ]) {
    test(c.form + ": 미확정 또는 경쟁하는 유지 요청 " + suffix, async () => {
      const { f, req } = await seeded(c);
      const clause = suffix === "conditional"
        ? "가능하면 " + c.subject + " 앞서 잡은 대로 둬 주세요."
        : c.subject + " 앞서 잡은 대로 " + suffix.replace("{label}", c.subject.slice(0, -1)).replace("{replacement}", c.replacement);
      f.send(c.uncertain + ". " + clause);
      await f.resolve({ [c.other.field]: uncertain(c.uncertain), [c.retain.field]: carry(c.retain, req.request_id) });
      noOffer(f.field(c.retain.field));
      assertHeld(f, c);
      f.apply({});
      assert.deepEqual(f.current, c.current);
    });
  }
}
