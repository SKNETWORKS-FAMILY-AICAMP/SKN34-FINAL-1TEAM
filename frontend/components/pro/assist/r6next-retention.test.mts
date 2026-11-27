// R6 실행 응답과 독립 대조: 실제 세션, 서버 응답만 스텁.
import assert from "node:assert/strict";
import { test } from "node:test";
import { AssistSession } from "./session.ts";
import { formKeys, type AssistForm, type AssistCurrent, type AssistRequest, type AssistValue, type CandidateField } from "./protocol.ts";
import { emptyFormHolds, mergeFormHolds, manuallyResolveHolds } from "./formHolds.ts";

type Proposed = { field: string; value: AssistValue; evidence: string; label: string; noun?: string };
type Case = { form: AssistForm; current: AssistCurrent; seed: string; drop: Proposed; keep: Proposed; changed: AssistValue };
type Fields = Record<string, Partial<CandidateField>>;
const cases: Case[] = [
  { form: "period", current: { start: "2026-12-02", end: "2026-12-24", excluded: [] },
    seed: "시작일은 12월 5일, 종료일은 12월 22일로 제안합니다.",
    drop: { field: "start", value: "2026-12-05", evidence: "시작일은 12월 5일", label: "시작일" },
    keep: { field: "end", value: "2026-12-22", evidence: "종료일은 12월 22일", label: "종료일", noun: "날짜로" },
    changed: "2026-12-26" },
  { form: "quote", current: { amount: 4550000, days: 3, vat: "included", valid: "2026-12-17" },
    seed: "공사 기간은 6일, 견적 금액은 526만원으로 제안합니다.",
    drop: { field: "days", value: 6, evidence: "공사 기간은 6일", label: "공사 기간" },
    keep: { field: "amount", value: 5260000, evidence: "견적 금액은 526만원", label: "견적 금액", noun: "금액으로" },
    changed: 4700000 },
  { form: "dates", current: { days: ["2026-12-09", "2026-12-16"], crew: 2 },
    seed: "시공 날짜는 12월 8일과 21일, 인원은 3명으로 제안합니다.",
    drop: { field: "crew", value: 3, evidence: "인원은 3명", label: "인원" },
    keep: { field: "days", value: ["2026-12-08", "2026-12-21"], evidence: "시공 날짜는 12월 8일과 21일", label: "시공 날짜", noun: "날짜로" },
    changed: ["2026-12-10", "2026-12-17"] },
  { form: "extra", current: { title: "보강", reason: "현장 추가", amount: 1000000, vat: "included" },
    seed: "추가 사유는 문짝 하단 긁힘, 비용은 28만 6천 원으로 먼저 띄워주세요.",
    drop: { field: "reason", value: "문짝 하단 긁힘", evidence: "추가 사유는 문짝 하단 긁힘", label: "사유" },
    keep: { field: "amount", value: 286000, evidence: "비용은 28만 6천 원", label: "추가금", noun: "금액으로" },
    changed: 900000 },
];

function fixture(form: AssistForm, initial: AssistCurrent) {
  let current = { ...initial }, holds = emptyFormHolds(), writes = 0;
  const manualVersions: Record<string, number> = {};
  const opts = {
    today: "2026-11-25", baseMonth: "2026-12-01", daysMax: 120, crewMax: 6,
    requiredDays: form === "dates" ? 2 : null,
    vatModes: form === "extra" ? ["included", "excluded"] : ["included", "excluded", "exempt"],
  };
  let job: { request: AssistRequest; resolve: (v: unknown) => void } | undefined;
  const session = new AssistSession(form, "r6next-retention", request =>
    new Promise(resolve => { job = { request, resolve }; }));
  function send(text: string) {
    const before = { ...current }, count = writes;
    assert(session.send(text, current, opts, issues => {
      holds = mergeFormHolds(form, holds, issues);
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
    job.resolve({ form, request_id: req.request_id, source: "llm", warnings: [],
      fields: formKeys(form).map(field => ({
        field, status: "valid", action: "keep", value: null, evidence: "",
        source: "rule", alternatives: [], origin_request_id: req.request_id,
        intent_kind: "no_mention", value_source: null, ...fields[field],
      })) });
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
  return { session, send, resolve, field, apply, manual, get holds() { return holds; }, get current() { return current; } };
}

function set(value: AssistValue, evidence: string): Partial<CandidateField> {
  return { action: "set", value, evidence, intent_kind: "explicit_change", value_source: "utterance", source: "llm" };
}
function keep(evidence: string): Partial<CandidateField> {
  return { action: "keep", value: null, evidence, intent_kind: "retain_current", value_source: "current" };
}
function carry(p: Proposed, origin: string): Partial<CandidateField> {
  return { ...set(p.value, p.evidence), intent_kind: "retain_previous", value_source: "previous", origin_request_id: origin };
}
function uncertain(evidence: string): Partial<CandidateField> {
  return { status: "uncertain", action: "uncertain", value: null, evidence, intent_kind: "ambiguous", source: "llm" };
}
function noOffer(candidate: CandidateField) {
  assert.notEqual(candidate.action, "set");
  assert.notEqual(candidate.action, "clear");
  assert.deepEqual(candidate.alternatives, []);
}
async function seeded(c: Case) {
  const f = fixture(c.form, c.current), req = f.send(c.seed);
  await f.resolve({
    [c.drop.field]: set(c.drop.value, c.drop.evidence),
    [c.keep.field]: set(c.keep.value, c.keep.evidence),
  });
  for (const p of [c.drop, c.keep]) {
    assert.equal(f.field(p.field).status, "valid");
    assert.deepEqual(f.field(p.field).value, p.value);
    assert.ok(f.holds.fields[p.field]);
  }
  return { f, req };
}
function clauses(c: Case) {
  const cancel = c.drop.label + " 바꾸는 건 없던 걸로";
  const retain = c.keep.label + "은 아까 " + c.keep.noun + " 반영";
  return { cancel, retain, text: cancel + " 하고 " + retain + " 바랍니다." };
}

for (const c of cases) {
  test(c.form + ": 연결된 철회 및 타입이 맞는 이전 후보 유지", async () => {
    const { f, req } = await seeded(c);
    const { cancel, text } = clauses(c);
    const followup = f.send(text);
    assert.equal(followup.previous_candidate?.origins?.[c.keep.field], req.request_id);
    await f.resolve({ [c.drop.field]: keep(cancel), [c.keep.field]: carry(c.keep, req.request_id) });
    assert.deepEqual(f.field(c.keep.field).value, c.keep.value);
    assert.equal(f.field(c.keep.field).origin_request_id, req.request_id);
    assert.equal(f.field(c.keep.field).evidence, c.keep.evidence);
    assert.equal(f.holds.fields[c.drop.field], undefined);
    assert.ok(f.holds.fields[c.keep.field]);
    f.session.select(c.keep.field, false);
    f.apply({});
    f.session.select(c.keep.field, true);
    f.apply({ [c.keep.field]: c.keep.value });
    assert.deepEqual(f.current[c.drop.field], c.current[c.drop.field]);
    assert.deepEqual(f.holds.fields, {});
  });

  for (const mode of ["same-manual", "changed-manual", "missing", "wrong-type", "forged-origin", "forged-source", "forged-evidence", "forged-value"] as const) {
    test(c.form + ": 이전 후보의 " + mode + " 차단", async () => {
      const { f, req } = mode === "missing"
        ? { f: fixture(c.form, c.current), req: { request_id: "missing" } }
        : await seeded(c);
      const { cancel, text } = clauses(c);
      if (mode === "same-manual") f.manual(c.keep.field, c.current[c.keep.field]);
      if (mode === "changed-manual") f.manual(c.keep.field, c.changed);
      const wrong = c.keep.noun === "금액으로" ? "인원으로" : "금액으로";
      const followup = f.send(mode === "wrong-type" ? text.replace(c.keep.noun!, wrong) : text);
      if (mode === "missing" || mode.endsWith("manual")) {
        assert.ok(!followup.previous_candidate?.fields.some(field => field.field === c.keep.field));
      }
      const proposed = carry(c.keep, req.request_id);
      if (mode === "forged-origin") proposed.origin_request_id = "unrelated";
      if (mode === "forged-source") proposed.source = "rule";
      if (mode === "forged-evidence") proposed.evidence = "원문에 없는 근거";
      if (mode === "forged-value") proposed.value = c.changed;
      const before = { ...f.current };
      await f.resolve({ [c.drop.field]: keep(cancel), [c.keep.field]: proposed });
      noOffer(f.field(c.keep.field));
      f.apply({});
      assert.deepEqual(f.current, before);
      assert.ok(f.holds.fields[c.keep.field]);
    });
  }

  for (const suffix of ["반영해도 될까요?", "가능하면 반영 바랍니다.", "반영하지 마세요.", "반영 바랍니다. {label} 변경은 취소합니다."]) {
    test(c.form + ": 불확실하거나 경쟁하는 이전 참조 " + suffix, async () => {
      const { f, req } = await seeded(c);
      const { cancel, retain } = clauses(c);
      f.send(cancel + " 하고 " + retain.replace(/반영$/, suffix.replace("{label}", c.keep.label)));
      // 스텁이 잘못된 유효 후보를 반환해도 적용되면 안 된다.
      await f.resolve({ [c.drop.field]: keep(cancel), [c.keep.field]: carry(c.keep, req.request_id) });
      noOffer(f.field(c.keep.field));
      f.apply({});
      assert.deepEqual(f.current, c.current);
    });
  }
}

for (const cancel of [
  "사유 바꾸는 건 없던 걸로 할까요?", "사유 바꾸는 건 없던 걸로 하지 마세요.",
  "가능하면 사유 바꾸는 건 없던 걸로 해주세요.", "사유 바꾸는 건 없던 걸로.",
  "사유 바꾸는 건 없던 걸로 하고 사유는 바닥 파손으로 바꾸겠습니다.",
]) {
  test("extra: 불완전하거나 경쟁하는 철회 " + cancel, async () => {
    const c = cases[3], { f, req } = await seeded(c);
    f.send(cancel + " 추가금은 아까 금액으로 반영 바랍니다.");
    await f.resolve({ reason: keep(cancel), amount: carry(c.keep, req.request_id) });
    assert.ok(f.holds.fields.reason);
    f.apply({ amount: 286000 });
    assert.equal(f.current.reason, c.current.reason);
    assert.ok(f.holds.fields.reason);
  });
}

test("period-08 실행: 잠정 제안은 보류, 수동 재입력 뒤 독립 마감일 적용", async () => {
  const c = cases[0], f = fixture(c.form, c.current);
  f.send("실측 시작 날짜를 12월 7일로 잠정 제시합니다.");
  await f.resolve({ start: uncertain("실측 시작 날짜를 12월 7일로 잠정 제시") });
  noOffer(f.field("start"));
  assert.ok(f.holds.fields.start);
  f.apply({});
  f.manual("start", c.current.start);
  const req = f.send("첫 실측일은 아까 후보를 쓰고 접수 마감은 12월 30일로 잡아주세요.");
  assert.ok(!req.previous_candidate?.fields.some(field => field.field === "start"));
  await f.resolve({
    start: uncertain("첫 실측일은 아까 후보를 쓰고"),
    end: set("2026-12-30", "접수 마감은 12월 30일로 잡아주세요"),
  });
  noOffer(f.field("start"));
  assert.deepEqual(f.field("end").value, "2026-12-30");
  f.apply({ end: "2026-12-30" });
  assert.equal(f.current.start, c.current.start);
  assert.ok(f.holds.fields.start);
  assert.equal(f.holds.fields.end, undefined);
});
