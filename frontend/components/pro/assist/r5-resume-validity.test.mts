// R5 유효기간 정중 요청과 가능 여부 문의를 실제 AssistSession에서 구분해요.
import assert from "node:assert/strict";
import { test } from "node:test";
import { AssistSession } from "./session.ts";
import { formKeys, type AssistCurrent, type AssistRequest, type CandidateField } from "./protocol.ts";
import { emptyFormHolds, mergeFormHolds } from "./formHolds.ts";

const current = { amount: 2_820_000, days: 5, vat: "excluded", valid: "2026-12-18" };
const options = { today: "2026-11-24", vatModes: ["included", "excluded", "exempt"] };
const target = "2026-12-30";
type Candidates = Record<string, Partial<CandidateField>>;

function fixture() {
  let holds = emptyFormHolds();
  let job: { req: AssistRequest; resolve: (value: unknown) => void; reject: (error: Error) => void } | undefined;
  const session = new AssistSession("quote", "r5-resume", req =>
    new Promise((resolve, reject) => { job = { req, resolve, reject }; }));
  function send(text: string) {
    assert(session.send(text, current, options, issues => { holds = mergeFormHolds("quote", holds, issues); }));
    assert(job);
    assert(holds.pending);
    assert.equal(session.apply(current, () => assert.fail("응답 전 적용")), false);
    return job.req;
  }
  async function resolve(fields: Candidates) {
    assert(job);
    const req = job.req;
    job.resolve({
      request_id: req.request_id, form: req.form, source: "llm", warnings: [],
      fields: formKeys("quote").map(field => ({
        field, status: "valid", source: "llm", action: "keep", value: null, evidence: "",
        origin_request_id: req.request_id, alternatives: [], intent_kind: "no_mention", value_source: null,
        ...fields[field],
      })),
    });
    await Promise.resolve();
    await Promise.resolve();
    assert.equal(session.view.phase, "ready");
    assert.equal(holds.pending, false);
  }
  function apply(expected: AssistCurrent, snapshot: AssistCurrent = current) {
    let patch: AssistCurrent = {};
    assert.equal(session.apply(snapshot, changes => { patch = changes; }), Object.keys(expected).length > 0);
    assert.deepEqual(patch, expected);
  }
  return { session, send, resolve, apply, get holds() { return holds; }, get job() { return job!; } };
}
function set(value: AssistCurrent[string], evidence: string): Partial<CandidateField> {
  return { action: "set", value, evidence, intent_kind: "explicit_change", value_source: "utterance" };
}

for (const [text, expected] of [
  ["발주처 검토가 늦어진다네요. 견적 유효기간은 12월 30일까지로 늘려 주시겠어요?", target],
  ["견적 유효기간은 12월 30일까지로 늘려 주실 수 있을까요?", target],
  ["견적 유효기간은 12월 15일까지로 줄여 주시겠어요?", "2026-12-15"],
  ["견적 유효기간은 12월 15일까지로 줄여 주실 수 있을까요?", "2026-12-15"],
] as const) {
  test("정중한 유효기간 변경 요청: " + text, async () => {
    const f = fixture();
    f.send(text);
    await f.resolve({ valid: set(expected, text) });
    assert.equal(f.session.view.response?.fields.find(x => x.field === "valid")?.value, expected);
    f.apply({ valid: expected });
    assert.deepEqual(f.holds.fields, {});
  });
}

for (const text of [
  "유효기간은 12월 30일까지로 늘릴 수 있는지 알려주시겠어요?",
  "유효기간은 12월 30일까지로 늘릴 수 있을지 확인 부탁드립니다",
  "유효기간은 12월 30일까지로 늘릴 수 없는지 알려주세요",
  "유효기간은 12월 30일까지로 늘릴 수 있을까요?",
  "유효기간은 12월 30일까지로 늘려 주실 수 있는지 알려주시겠어요?",
  "가능하면 유효기간은 12월 30일까지로 늘려 주실 수 있을까요?",
  "유효기간은 12월 30일까지로 늘려 주시겠어요? 변경은 취소할게요",
  "유효기간은 12월 30일까지로 늘려 주시겠어요? 유효기간 변경 요청은 취소할게요",
] as const) {
  test("문의·조건·취소의 서버 오답은 적용하지 않아요: " + text, async () => {
    const f = fixture();
    f.send(text);
    await f.resolve({ valid: set(target, text) });
    f.session.select("valid", true);
    f.apply({});
    assert.ok(f.holds.fields.valid);
  });
}

test("유효기간 문의와 독립된 세금 확정은 세금만 적용해요", async () => {
  const f = fixture();
  const text = "유효기간은 12월 30일까지로 늘릴 수 있는지 알려주시겠어요? 세금은 포함으로 정해 주세요";
  f.send(text);
  await f.resolve({ valid: set(target, "유효기간은 12월 30일까지로 늘릴 수 있는지 알려주시겠어요?"),
    vat: set("included", "세금은 포함으로 정해 주세요") });
  f.apply({ vat: "included" });
  assert.ok(f.holds.fields.valid);
  assert.equal(f.holds.fields.vat, undefined);
});

test("유효기간 문의의 전송 실패는 규칙 후보를 적용하지 않아요", async () => {
  const f = fixture();
  f.send("유효기간은 12월 30일까지로 늘릴 수 있는지 알려주시겠어요?");
  f.job.reject(new Error("offline"));
  await Promise.resolve();
  await Promise.resolve();
  assert.equal(f.session.view.phase, "ready");
  f.apply({});
  assert.ok(f.holds.fields.valid);
});

test("정상 정중 요청도 수동 변경된 현재값을 덮지 않아요", async () => {
  const f = fixture();
  const text = "유효기간은 12월 30일까지로 늘려 주시겠어요?";
  f.send(text);
  await f.resolve({ valid: set(target, text) });
  f.apply({}, { ...current, valid: "2026-12-21" });
});

test("정상 정중 요청을 취소하면 늦은 응답으로 되살아나지 않아요", async () => {
  const f = fixture();
  const text = "유효기간은 12월 30일까지로 늘려 주시겠어요?";
  const req = f.send(text);
  f.session.cancel();
  f.job.resolve({ request_id: req.request_id, form: req.form, source: "llm", warnings: [],
    fields: [{ field: "valid", ...set(target, text), status: "valid", source: "llm", alternatives: [] }] });
  await Promise.resolve();
  await Promise.resolve();
  assert.equal(f.session.view.phase, "idle");
  f.apply({});
});
