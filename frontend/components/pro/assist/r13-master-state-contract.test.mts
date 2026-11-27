import assert from "node:assert/strict";
import { test } from "node:test";
import { resolve } from "node:path";
import { pathToFileURL } from "node:url";
import type { AssistCurrent, AssistForm, AssistRequest, CandidateField, RuleOptions } from "./protocol.ts";

// The master can run the same cases against the immutable pre-refactor source.
const root = process.env.QA179_R13_SOURCE;
const { AssistSession } = await import(root
  ? pathToFileURL(resolve(root, "frontend/components/pro/assist/session.ts")).href
  : "./session.ts");
const { formKeys } = await import(root
  ? pathToFileURL(resolve(root, "frontend/components/pro/assist/protocol.ts")).href
  : "./protocol.ts");

const opts: RuleOptions = {
  today: "2026-11-25", baseMonth: "2026-12-01", requiredDays: 2,
  daysMax: 120, crewMax: 6, vatModes: ["included", "excluded", "exempt"],
};
const originals: Record<AssistForm, AssistCurrent> = {
  period: { start: "2026-12-02", end: "2026-12-28", excluded: [] },
  dates: { days: ["2026-12-09", "2026-12-16"], crew: 2 },
  quote: { amount: 3200000, days: 4, valid: "2026-12-29", vat: "included" },
  extra: { title: "기존 작업", reason: "기존 사유", amount: 300000, vat: "included" },
};

function field(r: AssistRequest, name: string, detail: Partial<CandidateField> = {}): CandidateField {
  return {
    field: name, action: "keep", value: null, evidence: "", source: "llm", status: "valid",
    alternatives: [], origin_request_id: r.request_id, intent_kind: "no_mention", value_source: null,
    ...detail,
  };
}
function ambiguous(r: AssistRequest, name: string) {
  return field(r, name, { action: "uncertain", status: "uncertain", intent_kind: "ambiguous", evidence: r.text });
}
function keepCurrent(r: AssistRequest, name: string, evidence = r.text) {
  return field(r, name, { intent_kind: "retain_current", value_source: "current", evidence });
}
function change(r: AssistRequest, name: string, value: CandidateField["value"], evidence = r.text) {
  return field(r, name, { action: "set", value, evidence, intent_kind: "explicit_change", value_source: "utterance" });
}
function harness(form: AssistForm) {
  const current = structuredClone(originals[form]);
  const holds: Record<string, string> = {};
  const versions: Record<string, number> = {};
  let pending = false, writes = 0;
  type Job = { request: AssistRequest; resolve: (raw: unknown) => void; reject: (err: unknown) => void };
  let job: Job | undefined;
  const session = new AssistSession(form, "00000000-0000-4000-8000-000000000179",
    (request: AssistRequest) => new Promise((resolve, reject) => { job = { request, resolve, reject }; }));
  function begin(text: string) {
    assert(session.send(text, current, opts, (issues: { fieldHolds?: Record<string, string | null>; pending?: boolean }) => {
      pending = issues.pending ?? pending;
      for (const [name, value] of Object.entries(issues.fieldHolds ?? {})) {
        if (value === null) delete holds[name]; else holds[name] = value;
      }
    }, () => ({ current: structuredClone(current), manualVersions: { ...versions } })));
    assert(pending);
    assert.equal(session.apply(current, () => assert.fail("cannot apply while loading")), false);
    assert(job);
    return job;
  }
  async function respond(j: Job, fields: CandidateField[], complete = true) {
    const previous = structuredClone(current), count = writes;
    j.resolve({
      form, source: "llm", request_id: j.request.request_id, warnings: [],
      fields: complete ? formKeys(form).map((k: string) => fields.find(f => f.field === k) ?? field(j.request, k)) : fields,
    });
    await new Promise<void>(resolve => setImmediate(resolve));
    assert.equal(session.view.phase, "ready");
    assert.equal(pending, false);
    assert.deepEqual(current, previous);
    assert.equal(writes, count);
  }
  async function send(text: string, producer: (r: AssistRequest) => CandidateField[]) {
    const j = begin(text);
    await respond(j, producer(j.request));
    return j.request;
  }
  function apply(expected: AssistCurrent) {
    for (const k of formKeys(form)) session.select(k, Object.hasOwn(expected, k));
    let patch: AssistCurrent = {};
    const before = writes;
    assert.equal(session.apply(current, (changes: AssistCurrent) => {
      writes++; patch = structuredClone(changes); Object.assign(current, changes);
    }), Object.keys(expected).length > 0);
    assert.deepEqual(patch, expected);
    assert.equal(writes, before + Number(Object.keys(expected).length > 0));
  }
  function manual(name: string, value: CandidateField["value"]) {
    current[name] = structuredClone(value);
    versions[name] = (versions[name] ?? 0) + 1;
    delete holds[name];
  }
  return { session, current, holds, begin, respond, send, apply, manual };
}

test("owned end-only operation does not create a fresh start hold", async () => {
  const h = harness("period");
  try {
    await h.send("공사 마치는 날을 12월 26일로 변경해 주세요.", r => [change(r, "end", "2026-12-26")]);
    assert.deepEqual(Object.keys(h.holds).sort(), ["end"]);
    h.apply({ end: "2026-12-26" });
    assert.deepEqual(h.holds, {});
  } finally { h.session.dispose(); }
});

test("same end-only operation cannot erase an actual previously unresolved start", async () => {
  const h = harness("period");
  try {
    await h.send("시작일은 아직 미정입니다.", r => [ambiguous(r, "start")]);
    await h.send("공사 마치는 날을 12월 26일로 변경해 주세요.", r => [change(r, "end", "2026-12-26")]);
    assert(h.holds.start); assert(h.holds.end);
    h.apply({ end: "2026-12-26" });
    assert(h.holds.start); assert(!h.holds.end);
  } finally { h.session.dispose(); }
});

test("owned current retention after inquiry settles only its own field", async () => {
  const h = harness("extra");
  try {
    await h.send("사유는 아직 미정입니다.", r => [ambiguous(r, "reason")]);
    const keep = "금액 수정은 하지 말아 주세요.";
    await h.send("지금 추가 공사 금액을 확인만 하고 싶어요. " + keep, r => [keepCurrent(r, "amount", keep)]);
    assert(!h.holds.amount); assert(h.holds.reason);
    h.apply({});
  } finally { h.session.dispose(); }
});

test("an explicit unchanged candidate retains its independent unresolved neighbour", async () => {
  const h = harness("dates");
  try {
    await h.send("작업일은 아직 미정입니다.", r => [ambiguous(r, "days")]);
    await h.send("인원은 2명으로 확정합니다.", r => [change(r, "crew", 2)]);
    assert(h.holds.days);
    h.apply({ crew: 2 });
    assert(h.holds.days);
  } finally { h.session.dispose(); }
});

for (const [name, text, evidence] of [
  ["negative", "인원 변경은 취소하지 마세요.", "인원 변경은 취소"],
  ["conditional", "승인이 나면 현재 인원을 그대로 유지해 주세요.", "현재 인원을 그대로 유지해 주세요."],
  ["wrong field", "현재 작업일을 그대로 유지해 주세요.", "현재 작업일을 그대로 유지해 주세요."],
  ["missing evidence", "인원은 아직 미정입니다.", "현재 인원을 그대로 유지해 주세요."],
] as const) {
  test("unverified keep cannot erase existing obligation: " + name, async () => {
    const h = harness("dates");
    try {
      await h.send("인원은 아직 미정입니다.", r => [ambiguous(r, "crew")]);
      await h.send(text, r => [keepCurrent(r, "crew", evidence)]);
      assert(h.holds.crew); h.apply({}); assert.equal(h.current.crew, 2);
    } finally { h.session.dispose(); }
  });
}

test("no-mention metadata does not release an explicitly uncertain field", async () => {
  const h = harness("dates");
  try {
    await h.send("작업 인원은 아직 결정하지 못했어요.", () => []);
    assert(h.holds.crew); h.apply({});
  } finally { h.session.dispose(); }
});

test("duplicate response field is not proof of retention", async () => {
  const h = harness("dates");
  try {
    await h.send("작업 인원은 아직 미정입니다.", r => [ambiguous(r, "crew")]);
    const j = h.begin("현재 인원은 그대로 유지해 주세요.");
    await h.respond(j, [keepCurrent(j.request, "crew"), field(j.request, "crew"), field(j.request, "days")], false);
    assert(h.holds.crew); h.apply({});
  } finally { h.session.dispose(); }
});

test("manual version changes during a request cannot be overwritten or reheld", async () => {
  const h = harness("dates");
  try {
    const j = h.begin("작업 인원을 5명으로 변경해 주세요.");
    h.manual("crew", 3);
    await h.respond(j, [change(j.request, "crew", 5)]);
    assert(!h.holds.crew);
    h.apply({}); assert.equal(h.current.crew, 3);
  } finally { h.session.dispose(); }
});

test("server error cannot release actual unresolved obligations", async () => {
  const h = harness("dates");
  try {
    await h.send("작업 인원은 아직 미정입니다.", r => [ambiguous(r, "crew")]);
    const j = h.begin("현재 인원을 그대로 유지해 주세요.");
    j.reject(new Error("offline fixture"));
    await new Promise<void>(resolve => setImmediate(resolve));
    assert(h.holds.crew); h.apply({});
  } finally { h.session.dispose(); }
});
