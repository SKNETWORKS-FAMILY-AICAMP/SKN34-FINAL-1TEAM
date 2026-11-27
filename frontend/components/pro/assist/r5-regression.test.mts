// R4/R5: 서버 응답과 실제 세션 보류·적용 상태를 함께 확인해요. 외부 호출은 없어요.
import assert from "node:assert/strict";
import { test } from "node:test";
import { AssistSession } from "./session.ts";
import { emptyFormHolds, mergeFormHolds, formWriteBlock, manuallyResolveHolds } from "./formHolds.ts";
import { formKeys, readResponse, type AssistForm, type AssistCurrent, type AssistRequest, type CandidateField, type RuleOptions } from "./protocol.ts";
import { parseExtra } from "./parse.ts";

const opts: RuleOptions = { today: "2026-11-24", baseMonth: "2026-12-01", crewMax: 6 };
const period = { start: "2026-12-02", end: "2026-12-24", excluded: [] };
const dates = { days: ["2026-12-09", "2026-12-16"], crew: 2 };
const quote = { amount: 1_000_000, days: 3, vat: "included", valid: "2026-12-20" };
const extra = { title: "보강", reason: "현장 요청", amount: 1_000_000, vat: "included" };
type Fields = Record<string, Partial<CandidateField>>;

function fixture(form: AssistForm, initial: AssistCurrent, options = opts) {
  let current = initial;
  let holds = emptyFormHolds();
  const manualVersions: Record<string, number> = {};
  let job: { request: AssistRequest; resolve: (result: unknown) => void; reject: (error: Error) => void } | undefined;
  const session = new AssistSession(form, "r5-resource", request =>
    new Promise((resolve, reject) => { job = { request, resolve, reject }; }));
  function send(text: string) {
    assert.equal(session.send(text, current, options,
      issues => { holds = mergeFormHolds(form, holds, issues); },
      () => ({ current, manualVersions })), true);
    assert.ok(job);
    assert.equal(holds.pending, true);
    assert.equal(session.apply(current, () => assert.fail("응답 전 적용")), false);
    return job.request;
  }
  async function resolve(fields: Fields) {
    assert.ok(job);
    const request = job.request;
    job.resolve({
      request_id: request.request_id, form, source: "llm", warnings: [],
      fields: formKeys(form).map(field => ({
        field, action: "keep", value: null, evidence: "", source: "rule", status: "valid",
        alternatives: [], origin_request_id: request.request_id, intent_kind: "no_mention",
        value_source: null, correction: null, reason_code: null,
        ...fields[field],
      })),
    });
    await Promise.resolve();
    await Promise.resolve();
    assert.equal(session.view.phase, "ready");
    assert.equal(holds.pending, false);
  }
  function apply(expected: AssistCurrent) {
    let actual: AssistCurrent = {};
    assert.equal(session.apply(current, changes => { actual = changes; }), Object.keys(expected).length > 0);
    assert.deepEqual(actual, expected);
  }
  function manual(field: string, value: AssistCurrent[string]) {
    current = { ...current, [field]: value };
    manualVersions[field] = (manualVersions[field] ?? 0) + 1;
    holds = manuallyResolveHolds(holds, [field]);
  }
  return { session, send, resolve, apply, manual, get job() { return job!; },
    get holds() { return holds; }, get request() { return job!.request; } };
}
function set(value: AssistCurrent[string], evidence: string): Partial<CandidateField> {
  return { action: "set", value, evidence, intent_kind: "explicit_change", value_source: "utterance", source: "llm" };
}
function keep(evidence: string): Partial<CandidateField> {
  return { action: "keep", evidence, intent_kind: "retain_current", value_source: "current" };
}
function carry(value: AssistCurrent[string], evidence: string, origin: string): Partial<CandidateField> {
  return { action: "set", value, evidence, intent_kind: "retain_previous", value_source: "previous",
    source: "llm", origin_request_id: origin };
}

test("R31L50 period02: 제외일 문의와 시작일 변경에서 종료일은 보류하지 않아요", async () => {
  const f = fixture("period", period);
  f.send("12월 11일은 방문 제외일로 잡을까요? 시작일은 12월 7일로 확정해주세요");
  await f.resolve({
    start: set("2026-12-07", "시작일은 12월 7일로 확정해주세요"),
    excluded: { action: "uncertain", status: "uncertain", evidence: "12월 11일은 방문 제외일로 잡을까요?", intent_kind: "ambiguous", reason_code: "ambiguous_intent" },
  });
  f.apply({ start: "2026-12-07" });
  assert.deepEqual(Object.keys(f.holds.fields), ["excluded"]);
});

test("R31L50 period11: 제외일 변경 철회만 풀고 기간 후보를 유지해요", async () => {
  const f = fixture("period", period);
  const first = f.send("실측은 12월 3일부터 29일까지 가능하게 초안 잡아주세요");
  await f.resolve({ start: set("2026-12-03", "12월 3일부터"), end: set("2026-12-29", "12월 3일부터 29일까지") });
  const periodCandidates = { start: carry("2026-12-03", "12월 3일부터", first.request_id), end: carry("2026-12-29", "12월 3일부터 29일까지", first.request_id) };
  f.send("제외일 12월 11일, 20일도 등록할 후보로 넣어주세요");
  await f.resolve({ ...periodCandidates, excluded: set(["2026-12-11", "2026-12-20"], "제외일 12월 11일, 20일") });
  f.send("제외일 변경은 철회하고 기간은 유지하겠습니다");
  await f.resolve({ ...periodCandidates, excluded: keep("제외일 변경은 철회하고") });
  assert.equal(f.holds.fields.excluded, undefined);
  assert.ok(f.holds.fields.start);
  assert.ok(f.holds.fields.end);
  f.apply({ start: "2026-12-03", end: "2026-12-29" });
  assert.equal(formWriteBlock(f.holds), null);
});

test("R31L50 dates10: 날짜 취소와 인원 유지가 독립적으로 처리돼요", async () => {
  const f = fixture("dates", dates);
  const first = f.send("시공 날짜 12월 11일하고 22일, 작업자 6명 초안 부탁합니다");
  await f.resolve({ days: set(["2026-12-11", "2026-12-22"], "시공 날짜 12월 11일하고 22일"), crew: set(6, "작업자 6명") });
  f.send("공사 날짜 변경 요청은 취소하고 작업자 수는 앞서 제안한 값 유지");
  await f.resolve({ days: keep("공사 날짜 변경 요청은 취소하고"), crew: carry(6, "작업자 6명", first.request_id) });
  assert.equal(f.holds.fields.days, undefined);
  assert.ok(f.holds.fields.crew);
  f.apply({ crew: 6 });
  assert.equal(formWriteBlock(f.holds), null);
});

for (const mode of ["valid", "wrong-old", "wrong-new", "swapped-proof", "wrong-origin", "wrong-source", "stale", "conditional", "cancel", "question", "offline"] as const) {
  test("R31L50 dates09 historical crew: " + mode, async () => {
    const f = fixture("dates", dates);
    const first = f.send("공사일 12월 4일과 21일, 작업인원 3명으로 제안합니다");
    await f.resolve({ days: set(["2026-12-04", "2026-12-21"], "공사일 12월 4일과 21일"), crew: set(3, "작업인원 3명") });
    if (mode === "stale") f.manual("crew", 4);
    const old = "아까 3명이라 했는데", next = "5명으로 고칠게요";
    let phrase = "인원은 " + old + " " + next + ". 날짜는 이전 제안 유지";
    if (mode === "conditional") phrase = "가능하면 " + phrase;
    if (mode === "cancel") phrase += ". 인원 정정은 취소할게요";
    if (mode === "question") phrase = phrase.replace("고칠게요.", "고칠까요?");
    const request = f.send(phrase);
    if (mode === "offline") {
      f.job.reject(new Error("offline"));
      await Promise.resolve();
      await Promise.resolve();
    } else {
      await f.resolve({
        days: carry(["2026-12-04", "2026-12-21"], "공사일 12월 4일과 21일", first.request_id),
        crew: {
          ...set(mode === "wrong-new" ? 4 : 5, next), origin_request_id: mode === "wrong-origin" ? first.request_id : request.request_id,
          source: mode === "wrong-source" ? "rule" : "llm",
          correction: { kind: "replace", rejected_value: mode === "wrong-old" ? 2 : 3,
            rejected_evidence: mode === "swapped-proof" ? next : old,
            accepted_evidence: mode === "swapped-proof" ? old : next },
        },
      });
    }
    const candidate = f.session.view.response?.fields.find(field => field.field === "crew");
    assert.equal(candidate?.status === "valid" && candidate.action === "set", mode === "valid");
    if (mode === "valid") {
      assert.equal(candidate?.value, 5);
      const followup = f.send("인원과 날짜는 앞서 제안한 그대로");
      assert.equal(followup.previous_candidate?.fields.find(field => field.field === "crew")?.value, 5);
      assert.equal(followup.previous_candidate?.baselines?.crew, 2);
      assert.equal(followup.previous_candidate?.origins?.crew, request.request_id);
      await f.resolve({
        days: carry(["2026-12-04", "2026-12-21"], "공사일 12월 4일과 21일", first.request_id),
        crew: carry(5, next, request.request_id),
      });
      f.apply({ days: ["2026-12-04", "2026-12-21"], crew: 5 });
    } else {
      assert.ok(f.holds.fields.crew, mode);
      f.session.select("crew", true);
      assert.equal(f.session.view.choices.crew?.selected, false);
    }
  });
}

test("G100 period04: 쉼표로 나열한 제외일을 경계 요청으로 해석하지 않아요", async () => {
  const f = fixture("period", period);
  const phrase = "실측 제외일은 12월 7일, 15일, 22일이에요";
  f.send(phrase);
  await f.resolve({ excluded: set(["2026-12-07", "2026-12-15", "2026-12-22"], phrase) });
  f.apply({ excluded: ["2026-12-07", "2026-12-15", "2026-12-22"] });
  assert.equal(formWriteBlock(f.holds), null);
});

test("G100 period14: 기존 경계 유지와 제외일 삭제를 분리해요", async () => {
  const f = fixture("period", { ...period, excluded: ["2026-12-07"] });
  f.send("등록한 실측 제외일은 전부 지워 주세요. 시작일하고 종료일은 그대로 둡니다");
  await f.resolve({
    start: keep("시작일하고 종료일은 그대로 둡니다"), end: keep("시작일하고 종료일은 그대로 둡니다"),
    excluded: { ...set(null, "등록한 실측 제외일은 전부 지워 주세요"), action: "clear" },
  });
  f.session.select("excluded", true);
  f.apply({ excluded: null });
  assert.equal(formWriteBlock(f.holds), null);
});

test("G100 extra04: 금액을 말하지 않은 세금 방식은 금액/제목 보류를 만들지 않아요", async () => {
  const f = fixture("extra", extra);
  f.send("이번 추가 비용은 세금 별도 처리로 표시해주세요");
  await f.resolve({ vat: set("excluded", "세금 별도 처리로 표시해주세요") });
  f.apply({ vat: "excluded" });
  assert.equal(formWriteBlock(f.holds), null);
});

test("R3L50 quote02: 열흘 문의와 별도 세금 확정은 서로 독립적이에요", async () => {
  const f = fixture("quote", quote);
  f.send("공사를 열흘 잡으면 괜찮을까요? 부가세는 별도로 정해주세요");
  await f.resolve({ days: { action: "uncertain", status: "uncertain", evidence: "공사를 열흘 잡으면 괜찮을까요?" }, vat: set("excluded", "부가세는 별도로 정해주세요") });
  f.apply({ vat: "excluded" });
  assert.deepEqual(Object.keys(f.holds.fields), ["days"]);
});

for (const [phrase, evidence, release] of [
  ["유효기간은 현재 화면 값으로 되돌려주세요", "유효기간은 현재 화면 값으로 되돌려주세요", true],
  ["유효기간 변경 요청은 취소하고 공사 기간은 직전 제안으로 유지", "유효기간 변경 요청은 취소하고", true],
  ["유효기간은 현재 화면 값으로 되돌릴까요?", "유효기간은 현재 화면 값으로 되돌릴까요?", false],
  ["유효기간 변경 요청은 취소하지 말고 공사 기간은 직전 제안으로 유지", "유효기간 변경 요청은 취소하지 말고", false],
  ["유효기간 변경 요청은 취소하고 공사 기간은 직전 제안으로 유지", "공사 기간은 직전 제안으로 유지", false],
] as const) {
  test("현재 유지 근거 확인: " + phrase + " / " + evidence, async () => {
    const f = fixture("quote", quote);
    const first = f.send("유효기간 12월 29일까지, 공사 5일");
    await f.resolve({ valid: set("2026-12-29", "유효기간 12월 29일까지"), days: set(5, "공사 5일") });
    f.send(phrase);
    await f.resolve({ valid: keep(evidence), days: carry(5, "공사 5일", first.request_id) });
    assert.equal(f.holds.fields.valid === undefined, release);
    assert.ok(f.holds.fields.days);
  });
}

test("문자열 할당 지시는 규칙 후보에 포함하지 않아요", () => {
  assert.equal(parseExtra("항목명은 바닥 이음부 보완으로 지정해주세요", opts.today).title, "바닥 이음부 보완");
  assert.equal(parseExtra("제목은 방음재 접합부 보강으로 조정해주세요", opts.today).title, "방음재 접합부 보강");
  assert.equal(parseExtra("제목은 확정 도면 보강으로 지정해주세요", opts.today).title, "확정 도면 보강");
});

test("유효 대안이 한 개면 충돌 선택을 허용하지 않아요", () => {
  const request: AssistRequest = { request_id: "r", form: "extra", resource_id: "resource", text: "항목명은 보강", current: extra, previous_candidate: null, rule_fields: [] };
  const result = readResponse({ request_id: "r", form: "extra", source: "llm", fields: [{
    field: "title", status: "conflict", action: "uncertain", source: "llm", alternatives: [
      { action: "set", value: "보강", source: "llm", evidence: "보강" },
      { action: "set", value: "", source: "rule", evidence: "" },
    ],
  }] }, request, opts);
  const title = result.fields.find(f => f.field === "title");
  assert.equal(title?.status, "uncertain");
  assert.deepEqual(title?.alternatives, []);
});

test("한 날짜만 시작일로 명시하면 오프라인에서도 종료일을 덮지 않아요", async () => {
  const f = fixture("period", period);
  f.send("첫 방문 가능일 12월 5일로 변경 부탁드립니다");
  f.job.reject(new Error("offline"));
  await Promise.resolve();
  await Promise.resolve();
  const end = f.session.view.response?.fields.find(field => field.field === "end");
  assert.notEqual(end?.action, "set");
  assert.equal(f.holds.fields.end, undefined);
});

test("명시적 현재값을 생략형으로 이어도 정확한 칸만 해제해요", async () => {
  const f = fixture("dates", dates);
  const first = f.send("작업일 12월 12일과 29일, 인원 4명");
  await f.resolve({ days: set(["2026-12-12", "2026-12-29"], "작업일 12월 12일과 29일"), crew: set(4, "인원 4명") });
  f.send("작업일은 앞서 제안한 그대로 유지하고 인원은 현재 입력값으로");
  await f.resolve({ days: carry(["2026-12-12", "2026-12-29"], "작업일 12월 12일과 29일", first.request_id),
    crew: keep("인원은 현재 입력값으로") });
  assert.equal(f.holds.fields.crew, undefined);
  assert.ok(f.holds.fields.days);
  f.apply({ days: ["2026-12-12", "2026-12-29"] });
});

for (const delta of [0, 1]) {
  test("이력 1000자 경계와 초과 시 이전 후보 검증: " + delta, async () => {
    const f = fixture("quote", quote);
    const followup = "세금 별도";
    const prefix = "견적 55만 원. ";
    const firstText = prefix + "가".repeat(1000 - followup.length - 1 - prefix.length + delta);
    const first = f.send(firstText);
    await f.resolve({ amount: set(550_000, "55만 원") });
    const second = f.send(followup);
    assert.equal(second.previous_candidate?.text, delta ? undefined : firstText);
    await f.resolve({ amount: carry(550_000, "55만 원", first.request_id), vat: set("excluded", followup) });
    if (delta) {
      assert.ok(f.session.view.response?.warnings.some(w => /이전.*후보|1000/.test(w)));
      f.apply({ vat: "excluded" });
      assert.ok(f.holds.fields.amount);
    } else {
      f.apply({ amount: 550_000, vat: "excluded" });
      assert.equal(formWriteBlock(f.holds), null);
    }
  });
}

test("이전 후보의 원래 값·근거·출처를 바꿔 반환하면 선택할 수 없어요", async () => {
  for (const mode of ["value", "evidence", "source"] as const) {
    const f = fixture("quote", quote);
    const first = f.send("견적 55만 원");
    await f.resolve({ amount: set(550_000, "55만 원") });
    f.send("세금 별도");
    await f.resolve({
      amount: {
        ...carry(mode === "value" ? 560_000 : 550_000, mode === "evidence" ? "세금 별도" : "55만 원", first.request_id),
        source: mode === "source" ? "rule" : "llm",
      },
      vat: set("excluded", "세금 별도"),
    });
    f.session.select("amount", true);
    f.apply({ vat: "excluded" });
    assert.ok(f.holds.fields.amount, mode);
  }
});

for (const [field, label, value] of [["title", "항목명", "바닥 보수"], ["reason", "사유", "벽면 들뜸"]] as const) {
  for (const mode of ["clean", "suffix", "offline", "quoted", "conflict"] as const) {
    test("문자열 정합니다 지시를 실제 값으로 선택하지 않아요: " + field + " " + mode, async () => {
      const f = fixture("extra", extra);
      const dirty = value + "로 정합니다";
      const phrase = label + "은 " + (mode === "quoted" ? '"' + dirty + '"' : dirty);
      f.send(phrase);
      if (mode === "offline") {
        f.job.reject(new Error("offline"));
        await Promise.resolve();
        await Promise.resolve();
      } else if (mode === "conflict") {
        await f.resolve({ [field]: { action: "uncertain", status: "conflict", evidence: phrase,
          alternatives: [
            { action: "set", value, evidence: phrase, source: "llm" },
            { action: "set", value: dirty, evidence: phrase, source: "rule" },
          ] } });
      } else {
        await f.resolve({ [field]: set(mode === "clean" ? value : dirty, phrase) });
      }
      f.apply(mode === "clean" || mode === "offline" ? { [field]: value } :
        mode === "quoted" ? { [field]: dirty } : {});
    });
  }
}

for (const mode of ["correct", "wrong-value", "wrong-origin", "question", "condition", "competing", "offline"] as const) {
  test("부가세 미포함 확정과 독립적인 유효기간 문의: " + mode, async () => {
    const f = fixture("quote", quote);
    const statement = "세금은 포함하지 않는 방식으로 확정합니다";
    const prefix = mode === "condition" ? "가능하면, " : "유효기한을 12월 29일까지 해도 될까요? ";
    const phrase = mode === "question" ? "세금은 포함하지 않는 방식으로 확정할까요?" : statement;
    f.send(prefix + phrase + (mode === "competing" ? ". 부가세는 포함합니다" : ""));
    if (mode === "offline") {
      f.job.reject(new Error("offline"));
      await Promise.resolve();
      await Promise.resolve();
    } else {
      await f.resolve({ vat: { ...set(mode === "wrong-value" ? "included" : "excluded", phrase),
        ...(mode === "wrong-origin" ? { origin_request_id: "old" } : {}) },
        valid: { action: "uncertain", status: "uncertain", evidence: "유효기한을 12월 29일까지" } });
    }
    f.apply(mode === "correct" ? { vat: "excluded" } : {});
    assert.ok(f.holds.fields.valid || mode === "condition");
  });
}

for (const [form, field, original, pending, text] of [
  ["period", "start", period, "2026-12-08", "시작일은 현재 등록된 값 그대로 두겠습니다"],
  ["dates", "crew", dates, 5, "인원 변경은 철회하고 현재 폼 입력값을 유지합니다"],
] as const) {
  for (const mode of ["correct", "question", "wrong-origin", "wrong-evidence"] as const) {
    test("현재 등록값과 철회 후 현재값 복귀: " + field + " " + mode, async () => {
      const f = fixture(form, original);
      const evidence = form === "period" ? "시작일은 12월 8일" : "인원 5명";
      f.send(evidence);
      await f.resolve({ [field]: set(pending, evidence) });
      const phrase = mode === "question" ? text.replace(/(?:두겠습니다|유지합니다)$/, "유지할까요?") : text;
      f.send(phrase);
      await f.resolve({ [field]: {
        ...keep(mode === "wrong-evidence" ? evidence : phrase),
        ...(mode === "wrong-origin" ? { origin_request_id: "old" } : {}),
      } });
      assert.equal(!!f.holds.fields[field], mode !== "correct");
      f.apply({});
    });
  }
}
