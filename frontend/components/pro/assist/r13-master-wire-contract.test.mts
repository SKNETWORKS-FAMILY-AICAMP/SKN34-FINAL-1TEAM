import assert from "node:assert/strict";
import { test } from "node:test";
import {
  formKeys, readResponse, wireOperation,
  type AssistRequest, type CandidateField, type RuleOptions,
} from "./protocol.ts";

const opts: RuleOptions = { today: "2026-11-25", crewMax: 6, requiredDays: 2 };
const request: AssistRequest = {
  form: "dates", request_id: "fresh-request", resource_id: "00000000-0000-4000-8000-000000000179",
  text: "작업 인원을 4명으로 변경해 주세요.", current: { days: ["2026-12-09", "2026-12-16"], crew: 2 },
  previous_candidate: null, rule_fields: [],
};
function fresh(r: AssistRequest, detail: Partial<CandidateField> = {}): CandidateField {
  return {
    field: "crew", source: "llm", status: "valid", action: "set", value: 4,
    evidence: r.text, alternatives: [], origin_request_id: r.request_id,
    intent_kind: "explicit_change", value_source: "utterance", ...detail,
  };
}
function envelope(r: AssistRequest, field: CandidateField) {
  return {
    source: "llm", form: r.form, request_id: r.request_id, warnings: [],
    fields: formKeys(r.form).map(name => name === field.field ? field : {
      field: name, status: "valid", source: "rule", action: "keep", value: null, evidence: "",
      alternatives: [], origin_request_id: r.request_id, intent_kind: "no_mention", value_source: null,
    }),
  };
}
function classify(raw: unknown, r = request, name = "crew") {
  const candidate = readResponse(raw, r, opts).fields.find(field => field.field === name)!;
  return wireOperation(raw, r, candidate);
}

test("strict fresh identity works without altering public scalar metadata", () => {
  const raw = envelope(request, fresh(request));
  const candidate = readResponse(raw, request, opts).fields.find(f => f.field === "crew")!;
  assert.equal(candidate.intent_kind, undefined);
  assert.equal(candidate.value_source, undefined);
  assert.deepEqual(wireOperation(raw, request, candidate), { kind: "explicit_change", valueSource: "utterance" });
});
for (const [name, details] of [
  ["unknown source", { source: "other" }],
  ["stale origin", { origin_request_id: "other" }],
  ["missing origin", { origin_request_id: null }],
  ["foreign evidence", { evidence: "다른 문장" }],
  ["missing evidence", { evidence: "" }],
  ["wrong action", { action: "keep", value: null }],
  ["unbound prior", { intent_kind: "retain_previous", value_source: "previous" }],
  ["unexpected alternative", { alternatives: [{ action: "set", value: 5, evidence: "5명", source: "llm" }] }],
] as const) {
  test("cannot enter canonical route with " + name, () => {
    const raw = envelope(request, { ...fresh(request), ...details } as CandidateField);
    assert.equal(classify(raw), null);
  });
}
test("duplicates and parsed candidate changes do not become canonical", () => {
  const raw = envelope(request, fresh(request));
  const candidate = readResponse(raw, request, opts).fields.find(f => f.field === "crew")!;
  assert.equal(wireOperation(raw, request, { ...candidate, value: 3 }), null);
  assert.equal(wireOperation({ ...raw, fields: [...raw.fields, fresh(request)] }, request, candidate), null);
  assert.equal(wireOperation({ ...raw, request_id: "other" }, request, candidate), null);
});
test("current retention and no-mention remain distinct operations", () => {
  const r = { ...request, text: "기존 인원을 그대로 유지해 주세요." };
  const keep = fresh(r, { action: "keep", value: null, intent_kind: "retain_current", value_source: "current" });
  assert.deepEqual(classify(envelope(r, keep), r), { kind: "retain_current", valueSource: "current" });
  const absent = fresh(r, { action: "keep", value: null, evidence: "", intent_kind: "no_mention", value_source: null });
  assert.deepEqual(classify(envelope(r, absent), r), { kind: "no_mention", valueSource: null });
  assert.equal(classify(envelope(r, { ...absent, evidence: r.text }), r), null);
});
test("eligible prior identity is bound to its original baseline and source", () => {
  const old = "인원은 4명으로 지정합니다.";
  const r: AssistRequest = {
    ...request, text: "인원은 이전 후보 그대로입니다.",
    previous_candidate: {
      request_id: "old", text: old, fields: [{ field: "crew", action: "set", value: 4, evidence: old }],
      sources: { crew: "llm" }, origins: { crew: "old" }, baselines: { crew: 2 },
    },
  };
  const candidate = fresh(r, { origin_request_id: "old", evidence: old, intent_kind: "retain_previous", value_source: "previous" });
  assert.deepEqual(classify(envelope(r, candidate), r), { kind: "retain_previous", valueSource: "previous" });
  for (const bad of [
    { ...candidate, value: 5 }, { ...candidate, source: "rule" as const },
    { ...candidate, origin_request_id: "wrong" }, { ...candidate, evidence: r.text },
  ]) assert.equal(classify(envelope(r, bad), r), null);
  const stale = { ...r, current: { ...r.current, crew: 3 } };
  assert.equal(classify(envelope(stale, candidate), stale), null);
});
test("derived list identity includes the exact retained correction relation", () => {
  const r = { ...request, text: "12월 9일 대신 12월 11일로 변경해 주세요." };
  const relation = { kind: "replace" as const, rejected_value: "2026-12-09", rejected_evidence: "12월 9일", accepted_evidence: "12월 11일" };
  const f = fresh(r, { field: "days", value: ["2026-12-11", "2026-12-16"], value_source: "derived", correction: relation });
  const raw = envelope(r, f);
  assert.deepEqual(classify(raw, r, "days"), { kind: "explicit_change", valueSource: "derived" });
  const parsed = readResponse(raw, r, opts).fields.find(c => c.field === "days")!;
  assert.equal(wireOperation(raw, r, { ...parsed, correction: { ...relation, rejected_value: "2026-12-10" } }), null);
});
