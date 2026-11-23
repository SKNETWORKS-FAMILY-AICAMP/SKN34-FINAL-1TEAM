// 업체 도우미 후보·적용 상태 검증. API는 가짜 응답으로 대체하고 업무 폼은 직접 수정하지 않아요.
import assert from "node:assert/strict";
import { test } from "node:test";
import { AssistSession } from "./session.ts";
import { AssistApiError, interpretAssist } from "./api.ts";
import { parseDates, parseExtra } from "./parse.ts";
import { ruleFields } from "./rules.ts";
import { shouldAutosaveQuote } from "./quoteAutosave.ts";
import {
  freshPrevious, previousCandidate, readResponse, validSetValue,
  type AssistCurrent, type AssistRequest, type AssistForm, type CandidateField,
} from "./protocol.ts";

const today = "2026-11-23";
const quote = { amount: null, vat: null, days: null, valid: null };
const quoteOptions = { today, daysMax: 40, vatModes: ["included", "excluded", "exempt"] };
type Data = Partial<Record<string, {
  origin_request_id?: string;
  evidence?: string;
  source?: "rule" | "llm";
  action?: "set" | "clear" | "keep" | "uncertain";
  value?: string | number | string[] | null;
  status?: "valid" | "invalid" | "conflict" | "uncertain";
  intent_kind?: CandidateField["intent_kind"];
  value_source?: CandidateField["value_source"];
  correction?: CandidateField["correction"];
  alternatives?: { action: "set" | "clear"; value: string | number | string[] | null; evidence: string; source: "rule" | "llm" }[];
}>>;

function response(req: AssistRequest, data: Data = {}) {
  const names: Record<AssistForm, string[]> = {
    period: ["start", "end", "excluded"],
    quote: ["amount", "vat", "days", "valid"],
    dates: ["days", "crew"],
    extra: ["title", "reason", "amount", "vat"],
  };
  return {
    request_id: req.request_id, form: req.form, source: "llm", warnings: [],
    fields: names[req.form].map((field) => {
      const detail = data[field] ?? {};
      return {
        field, action: detail.action ?? "keep", value: detail.value ?? null,
        evidence: detail.evidence ?? "원문 근거", source: detail.source ?? "llm", status: detail.status ?? "valid",
        alternatives: detail.alternatives ?? [],
        origin_request_id: detail.origin_request_id,
        ...(detail.intent_kind === undefined ? {} : { intent_kind: detail.intent_kind }),
        ...(detail.value_source === undefined ? {} : { value_source: detail.value_source }),
        ...(detail.correction === undefined ? {} : { correction: detail.correction }),
      };
    }),
  };
}

function pending() {
  const jobs: {
    request: AssistRequest; signal: AbortSignal;
    resolve: (value: unknown) => void; reject: (error: unknown) => void;
  }[] = [];
  return {
    jobs,
    requester: (request: AssistRequest, signal: AbortSignal) => new Promise<unknown>((resolve, reject) => {
      jobs.push({ request, signal, resolve, reject });
    }),
  };
}

const flush = () => new Promise<void>((resolve) => setImmediate(resolve));

test("기간·제외일·공사 날짜의 한도를 실제 업무 규칙과 맞춰요", () => {
  const days = Array.from({ length: 70 }, (_, index) =>
    new Date(Date.UTC(2026, 11, 1 + index)).toISOString().slice(0, 10));
  assert.equal(validSetValue("dates", "days", days, { today, requiredDays: 70 }), true);
  assert.equal(validSetValue("dates", "days", ["2026-12-01", "2027-12-01"], { today }), false);
  assert.equal(validSetValue("period", "excluded", days.slice(0, 32), { today }), false);
  assert.equal(validSetValue("period", "start", today, { today }), false);
  assert.equal(validSetValue("quote", "valid", today, { today }), true);
});

test("기준값이 없으면 과거 set/clear는 이어 쓰지 않아요", () => {
  const req: AssistRequest = {
    request_id: "origin", form: "quote", resource_id: "visit-id",
    text: "견적 455만 원", current: { ...quote }, previous_candidate: null,
    rule_fields: ruleFields("quote", "견적 455만 원", quoteOptions),
  };
  const parsed = readResponse(response(req, { amount: { action: "set", value: 4_550_000 } }), req, quoteOptions);
  assert.equal(freshPrevious(parsed, null, quote), null);
  assert.equal(freshPrevious(parsed, {}, quote), null);
  assert.equal(freshPrevious(parsed, { amount: null }, quote)?.fields.length, 1);
  const wire = previousCandidate(parsed, req.text, {});
  assert.deepEqual(wire.fields, []);
  assert.deepEqual(wire.baselines, {});
  assert.deepEqual(wire.sources, {});
  assert.deepEqual(wire.origins, {});
});

test("수동 수정한 이전 금액은 다음 요청·미리보기에서 빼고 다른 필드만 적용해요", async () => {
  const api = pending();
  const session = new AssistSession("quote", "visit-id", api.requester);
  session.send("견적 455만 원", quote, quoteOptions);
  const first = api.jobs[0].request;
  api.jobs[0].resolve(response(first, {
    amount: { action: "set", value: 4550000, origin_request_id: first.request_id },
  }));
  await flush();
  const manual = { ...quote, amount: 6000000 };
  session.send("유효기간만 12/20까지", manual, quoteOptions);
  const second = api.jobs[1].request;
  assert.equal(second.current.amount, 6_000_000);
  assert.equal(second.previous_candidate, null);
  assert.equal(session.view.response?.fields.some((field) => field.field === "amount" && field.action === "set"), false);
  api.jobs[1].resolve(response(second, {
    valid: { action: "set", value: "2026-12-20", origin_request_id: second.request_id },
  }));
  await flush();
  const changes: AssistCurrent[] = [];
  assert.equal(session.apply(manual, (change) => changes.push(change)), true);
  assert.deepEqual(changes, [{ valid: "2026-12-20" }]);
});

test("기존 채팅 전송은 규칙+API 1회, 결과 확인 전 setter 0회, 부분 적용만 한 번", async () => {
  const api = pending();
  const session = new AssistSession("quote", "visit-id", api.requester);
  const changed: AssistCurrent[] = [];
  assert.equal(session.send("견적 455만 원, 공사 3일", quote, quoteOptions), true);
  assert.equal(api.jobs.length, 1);
  assert.equal(api.jobs[0].request.resource_id, "visit-id");
  assert.equal(api.jobs[0].request.previous_candidate, null);
  assert.equal(api.jobs[0].request.rule_fields.length, 4);
  assert.equal(api.jobs[0].request.rule_fields[0].value, 4_550_000);
  assert.equal(session.send("같은 글 중복", quote, quoteOptions), false);
  assert.equal(session.apply(quote, (value) => changed.push(value)), false);
  assert.equal(changed.length, 0);
  api.jobs[0].resolve(response(api.jobs[0].request, {
    amount: { action: "set", value: 4_550_000 },
    vat: { action: "set", value: "included" },
    days: { action: "set", value: 3 },
    valid: { action: "set", value: "2026-12-18" },
  }));
  await flush();
  assert.equal(session.view.phase, "ready");
  assert.equal(changed.length, 0);
  session.select("vat", false);
  session.select("valid", false);
  assert.equal(session.apply(quote, (value) => changed.push(value)), true);
  assert.deepEqual(changed, [{ amount: 4_550_000, days: 3 }]);
  assert.equal(session.view.phase, "applied");
  assert.equal(session.apply(quote, (value) => changed.push(value)), false);
  assert.equal(changed.length, 1);
});

test("직전 후보를 보내며 다음 keep은 이전 미확정 후보를 유지해요", async () => {
  const api = pending();
  const session = new AssistSession("quote", "visit-id", api.requester);
  const first = "견적 455만 원, 공사 3일";
  assert.ok(session.send(first, quote, quoteOptions));
  api.jobs[0].resolve(response(api.jobs[0].request, {
    amount: { action: "set", value: 4_550_000 },
    days: { action: "set", value: 3 },
  }));
  await flush();
  assert.ok(session.send("아니 555만 원으로 바꿔 주세요, 기간은 그대로", quote, quoteOptions));
  const second = api.jobs[1].request;
  assert.equal(second.previous_candidate?.text, first);
  assert.equal(second.previous_candidate?.fields.find((field) => field.field === "amount")?.value, 4_550_000);
  assert.equal(second.previous_candidate?.fields.find((field) => field.field === "days")?.value, 3);
  assert.deepEqual(second.previous_candidate?.baselines, { amount: null, days: null });
  assert.equal(second.previous_candidate?.sources?.amount, "llm");
  assert.equal(second.previous_candidate?.origins?.amount, api.jobs[0].request.request_id);
  assert.equal(session.view.response?.fields.find((field) => field.field === "days")?.origin_request_id, api.jobs[0].request.request_id);
  const serialized = JSON.parse(JSON.stringify(second));
  assert.deepEqual(serialized.previous_candidate.baselines, { amount: null, days: null });
  assert.deepEqual(serialized.previous_candidate.fields.map((field: { field: string }) => field.field), ["amount", "days"]);
  assert.equal(session.apply(quote, () => assert.fail("응답 대기 중 반영")), false);
  api.jobs[1].resolve(response(second, {
    amount: { action: "set", value: 5_550_000, origin_request_id: second.request_id },
    days: { action: "set", value: 3, origin_request_id: api.jobs[0].request.request_id },
  }));
  await flush();
  const changed: AssistCurrent[] = [];
  assert.equal(session.apply(quote, (value) => changed.push(value)), true);
  assert.deepEqual(changed, [{ amount: 5_550_000, days: 3 }]);
  assert.equal(api.jobs.length, 2);
});

test("서버가 keep으로 제외한 과거 후보를 성공 응답 뒤 다시 채우지 않아요", async () => {
  const api = pending();
  const session = new AssistSession("quote", "visit-id", api.requester);
  session.send("견적 455만 원", quote, quoteOptions);
  api.jobs[0].resolve(response(api.jobs[0].request, {
    amount: { action: "set", value: 4_550_000 },
  }));
  await flush();
  session.send("공사 3일", quote, quoteOptions);
  assert.deepEqual(api.jobs[1].request.previous_candidate?.baselines, { amount: null });
  // API 대기 중 표시한 규칙 미리보기에는 이전 후보를 이어 쓸 수 있어요.
  assert.equal(session.view.response?.fields.find((field) => field.field === "amount")?.value, 4_550_000);
  api.jobs[1].resolve(response(api.jobs[1].request, { days: { action: "set", value: 3 } }));
  await flush();
  // 서버가 최종적으로 keep을 택했다면 오래된 미리보기를 되살리지 않아요.
  assert.equal(session.view.response?.fields.find((field) => field.field === "amount")?.action, "keep");
  assert.equal(session.apply(quote, (changes) =>
    assert.deepEqual(changes, { days: 3 })), true);
});

test("수동으로 바꾼 사유는 이전 clear에서 빠지고 새 금액만 반영돼요", async () => {
  const api = pending();
  const current = { title: null, reason: "기존 이유", amount: null, vat: null };
  const session = new AssistSession("extra", "deal-id", api.requester);
  session.send("이유 지워 주세요", current, { today });
  const first = api.jobs[0].request;
  api.jobs[0].resolve(response(first, {
    reason: { action: "clear", value: null, origin_request_id: first.request_id },
  }));
  await flush();
  const edited = { ...current, reason: "직접 새로 쓴 이유" };
  session.send("추가 금액 12만 원", edited, { today });
  const second = api.jobs[1].request;
  assert.equal(second.current.reason, "직접 새로 쓴 이유");
  assert.equal(second.previous_candidate, null);
  assert.equal(session.view.response?.fields.some((field) =>
    field.field === "reason" && field.action === "clear"), false);
  api.jobs[1].resolve(response(second, { amount: { action: "set", value: 120_000 } }));
  await flush();
  assert.equal(session.apply(edited, (changes) =>
    assert.deepEqual(changes, { amount: 120_000 })), true);
});

test("이전 clear가 유지되는 동안 최초 사유를 baseline으로 전송하고 선택은 명시적으로 해요", async () => {
  const api = pending();
  const current = { title: null, reason: "원래 이유", amount: null, vat: null };
  const session = new AssistSession("extra", "deal-id", api.requester);
  session.send("원래 이유를 지워 주세요", current, { today });
  const first = api.jobs[0].request;
  api.jobs[0].resolve(response(first, {
    reason: { action: "clear", value: null, origin_request_id: first.request_id },
  }));
  await flush();
  session.send("금액 12만 원만 추가", current, { today });
  const second = api.jobs[1].request;
  assert.deepEqual(second.previous_candidate?.baselines, { reason: "원래 이유" });
  assert.deepEqual(second.previous_candidate?.fields, [{
    field: "reason", action: "clear", value: null, evidence: "원문 근거",
  }]);
  assert.equal(second.previous_candidate?.sources?.reason, "llm");
  assert.equal(second.previous_candidate?.origins?.reason, first.request_id);
  api.jobs[1].resolve(response(second, {
    reason: { action: "clear", value: null, origin_request_id: first.request_id },
    amount: { action: "set", value: 120_000, origin_request_id: second.request_id },
  }));
  await flush();
  const applied: AssistCurrent[] = [];
  assert.equal(session.apply(current, (changes) => applied.push(changes)), true);
  assert.deepEqual(applied, [{ amount: 120_000 }]); // 삭제는 기본 미선택
});

test("3회 연속 메시지에서도 물려받은 기준값·원래 요청 ID·출처가 바뀌지 않아요", async () => {
  const api = pending();
  const current = { ...quote, amount: 1_000_000 };
  const session = new AssistSession("quote", "visit-id", api.requester);
  session.send("견적 455만 원", current, quoteOptions);
  const first = api.jobs[0].request;
  api.jobs[0].resolve(response(first, {
    amount: { action: "set", value: 4_550_000, origin_request_id: first.request_id },
  }));
  await flush();
  session.send("공사 3일", current, quoteOptions);
  const second = api.jobs[1].request;
  assert.deepEqual(second.previous_candidate?.baselines, { amount: 1_000_000 });
  api.jobs[1].resolve(response(second, {
    amount: { action: "set", value: 4_550_000, origin_request_id: first.request_id },
    days: { action: "set", value: 3, origin_request_id: second.request_id },
  }));
  await flush();
  session.send("부가세 포함", current, quoteOptions);
  const third = api.jobs[2].request;
  assert.deepEqual(third.previous_candidate?.baselines, { amount: 1_000_000, days: null });
  assert.equal(third.previous_candidate?.origins?.amount, first.request_id);
  assert.equal(third.previous_candidate?.origins?.days, second.request_id);
  assert.equal(third.previous_candidate?.sources?.amount, "llm");
  assert.equal(third.previous_candidate?.sources?.days, "llm");
  assert.equal(third.previous_candidate?.text, "견적 455만 원\n공사 3일");
  api.jobs[2].resolve(response(third, {
    amount: { action: "set", value: 4_550_000, origin_request_id: first.request_id },
    days: { action: "set", value: 3, origin_request_id: second.request_id },
    vat: { action: "set", value: "included", origin_request_id: third.request_id },
  }));
  await flush();
  assert.equal(session.apply(current, (changes) =>
    assert.deepEqual(changes, { amount: 4_550_000, days: 3, vat: "included" })), true);
});

test("파생 날짜 목록은 3턴째에도 근거·최초 기준값·출처를 보존해 다시 검증받아요", async () => {
  const api = pending();
  const originalDays = ["2026-12-08", "2026-12-09", "2026-12-10"];
  const derivedDays = ["2026-12-08", "2026-12-10", "2026-12-11"];
  const current = { days: originalDays, crew: 2 };
  const opts = { today, requiredDays: 3, daysMax: 30, crewMax: 6 };
  const correction = {
    kind: "replace" as const,
    rejected_value: ["2026-12-09"],
    rejected_evidence: "9일",
    accepted_evidence: "11일",
  };
  const session = new AssistSession("dates", "deal-id", api.requester);

  session.send("9일 대신 11일, 나머지는 그대로", current, opts);
  const first = api.jobs[0].request;
  api.jobs[0].resolve(response(first, {
    days: {
      action: "set", value: derivedDays, evidence: "9일 대신 11일",
      intent_kind: "explicit_change", value_source: "derived",
      correction, origin_request_id: first.request_id, source: "llm",
    },
  }));
  await flush();
  const firstDays = session.view.response?.fields.find((field) => field.field === "days");
  assert.equal(firstDays?.status, "valid");
  assert.equal(firstDays?.value_source, "derived");
  assert.deepEqual(firstDays?.correction, correction);

  session.send("투입 인원 3명", current, opts);
  const second = api.jobs[1].request;
  const priorDays = second.previous_candidate?.fields.find((field) => field.field === "days");
  assert.equal(priorDays?.value_source, "derived");
  assert.equal(priorDays?.intent_kind, "explicit_change");
  assert.deepEqual(priorDays?.correction, correction);
  assert.deepEqual(second.previous_candidate?.baselines, { days: originalDays });
  assert.equal(second.previous_candidate?.origins?.days, first.request_id);
  assert.equal(second.previous_candidate?.sources?.days, "llm");
  const secondWire = JSON.parse(JSON.stringify(second));
  assert.equal(secondWire.previous_candidate.fields[0].value_source, "derived");
  assert.deepEqual(secondWire.previous_candidate.fields[0].correction, correction);

  api.jobs[1].resolve(response(second, {
    days: {
      action: "set", value: derivedDays, evidence: "9일 대신 11일",
      intent_kind: "explicit_change", value_source: "derived",
      correction, origin_request_id: first.request_id, source: "llm",
    },
    crew: {
      action: "set", value: 3, evidence: "투입 인원 3명",
      origin_request_id: second.request_id, source: "rule",
    },
  }));
  await flush();

  session.send("나머지 조건은 그대로", current, opts);
  const third = api.jobs[2].request;
  const thirdDays = third.previous_candidate?.fields.find((field) => field.field === "days");
  assert.deepEqual(thirdDays?.value, derivedDays);
  assert.equal(thirdDays?.value_source, "derived");
  assert.deepEqual(thirdDays?.correction, correction);
  assert.deepEqual(third.previous_candidate?.baselines, { days: originalDays, crew: 2 });
  assert.equal(third.previous_candidate?.origins?.days, first.request_id);
  assert.equal(third.previous_candidate?.origins?.crew, second.request_id);
  assert.equal(third.previous_candidate?.sources?.days, "llm");
  assert.equal(third.previous_candidate?.sources?.crew, "rule");
  assert.equal(third.previous_candidate?.text, "9일 대신 11일, 나머지는 그대로\n투입 인원 3명");
  assert.equal(third.previous_candidate?.fields.find((field) => field.field === "crew")?.correction, undefined);
  assert.equal(session.apply(current, () => assert.fail("3턴째 응답 전 폼 수정")), false);
});

test("날짜 제외 목록도 검증된 derived 정정 근거만 다음 요청으로 복사해요", () => {
  const period = { start: "2026-12-08", end: "2026-12-12", excluded: ["2026-12-09"] };
  const req: AssistRequest = {
    request_id: "first-period", resource_id: "visit-id", form: "period",
    text: "9일 대신 11일을 제외해 주세요", current: period, rule_fields: [],
    previous_candidate: null,
  };
  const correction = {
    kind: "replace" as const, rejected_value: ["2026-12-09"],
    rejected_evidence: "9일", accepted_evidence: "11일",
  };
  const raw = response(req, {
    excluded: {
      action: "set", value: ["2026-12-11"], evidence: req.text,
      value_source: "derived", correction, intent_kind: "explicit_change",
      origin_request_id: "origin-period",
    },
  });
  const parsed = readResponse(raw, req, { today });
  const excluded = parsed.fields.find((field) => field.field === "excluded");
  assert.equal(excluded?.value_source, "derived");
  assert.deepEqual(excluded?.correction, correction);
  const next = previousCandidate(parsed, req.text, period);
  assert.deepEqual(next.fields, [{
    field: "excluded", action: "set", value: ["2026-12-11"], evidence: req.text,
    intent_kind: "explicit_change", value_source: "derived", correction,
  }]);
  assert.deepEqual(next.baselines, { excluded: ["2026-12-09"] });
  assert.deepEqual(next.origins, { excluded: "origin-period" });
});

test("파생 목록 정정 근거의 날짜 배열은 외부 변경과 직렬화 변경으로 변하지 않아요", () => {
  const req: AssistRequest = {
    request_id: "derived-copy", resource_id: "deal-id", form: "dates",
    text: "9일 대신 11일", current: { days: ["2026-12-08", "2026-12-09"], crew: 2 },
    previous_candidate: null, rule_fields: [],
  };
  const oldDays = ["2026-12-08", "2026-12-09"];
  const correction = {
    kind: "replace" as const, rejected_value: oldDays,
    rejected_evidence: "9일", accepted_evidence: "11일",
  };
  const raw = response(req, {
    days: {
      action: "set", value: ["2026-12-08", "2026-12-11"], evidence: req.text,
      value_source: "derived", correction,
    },
  });
  const parsed = readResponse(raw, req, { today, requiredDays: 2 });
  oldDays.push("2026-12-10");
  const received = parsed.fields.find((item) => item.field === "days");
  assert.deepEqual(received?.correction?.rejected_value, ["2026-12-08", "2026-12-09"]);
  const forwarded = previousCandidate(parsed, req.text, req.current);
  const original = forwarded.fields.find((item) => item.field === "days");
  assert.deepEqual(original?.correction?.rejected_value, ["2026-12-08", "2026-12-09"]);
  const carried = original?.correction?.rejected_value;
  if (Array.isArray(carried)) carried.push("2026-12-12");
  assert.deepEqual(received?.correction?.rejected_value, ["2026-12-08", "2026-12-09"]);
});

test("신뢰할 수 없는 날짜 정정 메타데이터는 계승하지 않아요", () => {
  const originalDays = ["2026-12-08", "2026-12-09"];
  const req: AssistRequest = {
    request_id: "unsafe", resource_id: "deal-id", form: "dates",
    text: "9일 대신 11일", current: { days: originalDays, crew: 2 },
    previous_candidate: null, rule_fields: [],
  };
  const legitimate = response(req, {
    days: {
      action: "set", value: ["2026-12-08", "2026-12-11"],
      value_source: "derived", evidence: req.text,
    },
  });
  const attacks: unknown[] = [
    { kind: "replace", rejected_value: ["2026-02-31"], rejected_evidence: "9일", accepted_evidence: "11일" },
    { kind: "replace", rejected_value: ["2026-12-09", "2026-12-09"], rejected_evidence: "9일", accepted_evidence: "11일" },
    { kind: "ambiguous", rejected_value: ["2026-12-09"], rejected_evidence: "9일", accepted_evidence: "11일" },
    { kind: "replace", rejected_value: ["2026-12-09"], rejected_evidence: "", accepted_evidence: "11일" },
    { kind: "replace", rejected_value: ["2026-12-09"], rejected_evidence: "9일", accepted_evidence: "x".repeat(1001) },
    { kind: "replace", rejected_value: Array.from({ length: 121 }, (_, n) =>
      new Date(Date.UTC(2026, 11, 1 + n)).toISOString().slice(0, 10)),
    rejected_evidence: "9일", accepted_evidence: "11일" },
    { kind: "replace", rejected_value: { forged: true }, rejected_evidence: "9일", accepted_evidence: "11일" },
  ];
  for (const correction of attacks) {
    const raw = {
      ...legitimate,
      fields: legitimate.fields.map((field) =>
        field.field === "days" ? { ...field, correction } : field),
    };
    const parsed = readResponse(raw, req, { today, requiredDays: 2 });
    const candidate = parsed.fields.find((field) => field.field === "days");
    assert.equal(candidate?.status, "valid");
    assert.equal(candidate?.value_source, "derived");
    assert.equal(candidate?.correction, undefined);
    assert.equal(previousCandidate(parsed, req.text, req.current).fields[0].correction, undefined);
  }

  const notDerived = {
    ...legitimate,
    fields: legitimate.fields.map((field) =>
      field.field === "days" ? {
        ...field, value_source: "utterance",
        correction: { kind: "replace", rejected_value: ["2026-12-09"], rejected_evidence: "9일", accepted_evidence: "11일" },
      } : field),
  };
  const parsed = readResponse(notDerived, req, { today, requiredDays: 2 });
  assert.equal(parsed.fields.find((field) => field.field === "days")?.value_source, undefined);
  assert.equal(parsed.fields.find((field) => field.field === "days")?.correction, undefined);
  const forged = readResponse(response({
    ...req, form: "extra", current: { title: null, reason: null, amount: null, vat: null },
  }, {
    title: {
      action: "set", value: "누수 보수", value_source: "derived",
      correction: { kind: "replace", rejected_value: "도배", rejected_evidence: "도배", accepted_evidence: "누수" },
    },
  }), { ...req, form: "extra" }, { today });
  assert.equal(forged.fields.find((field) => field.field === "title")?.value_source, undefined);
  assert.equal(forged.fields.find((field) => field.field === "title")?.correction, undefined);

  const emptyEvidence = {
    ...legitimate,
    fields: legitimate.fields.map((field) =>
      field.field === "days" ? { ...field, evidence: "", value_source: "derived" } : field),
  };
  const noEvidence = readResponse(emptyEvidence, req, { today, requiredDays: 2 });
  assert.equal(noEvidence.fields.find((field) => field.field === "days")?.value_source, undefined);
  assert.equal(previousCandidate(noEvidence, req.text, req.current).fields[0].value_source, undefined);
});

test("API 대기 중 수동 수정해도 과거 응답을 적용할 수 없고 다음 Send에서 제외돼요", async () => {
  const api = pending();
  const session = new AssistSession("quote", "visit-id", api.requester);
  session.send("견적 455만 원", quote, quoteOptions);
  const first = api.jobs[0].request;
  const manual = { ...quote, amount: 6_000_000 };
  assert.equal(session.apply(manual, () => assert.fail("대기 중 setter 실행")), false);
  assert.equal(session.send("공사 3일", manual, quoteOptions), false);
  assert.equal(api.jobs.length, 1);
  api.jobs[0].resolve(response(first, {
    amount: { action: "set", value: 4_550_000, origin_request_id: first.request_id },
  }));
  await flush();
  assert.equal(session.apply(manual, () => assert.fail("수동 수정 덮어쓰기")), false);
  session.send("공사 3일", manual, quoteOptions);
  assert.equal(api.jobs[1].request.previous_candidate, null);
  api.jobs[1].resolve(response(api.jobs[1].request, { days: { action: "set", value: 3 } }));
  await flush();
  assert.equal(session.apply(manual, (changes) =>
    assert.deepEqual(changes, { days: 3 })), true);
});

test("오래된 후보가 빠져도 현재 메시지에서 추출한 새 금액은 적용할 수 있어요", async () => {
  const api = pending();
  const session = new AssistSession("quote", "visit-id", api.requester);
  session.send("견적 455만 원", quote, quoteOptions);
  api.jobs[0].resolve(response(api.jobs[0].request, {
    amount: { action: "set", value: 4_550_000 },
  }));
  await flush();
  const manual = { ...quote, amount: 6_000_000 };
  session.send("견적 650만 원으로 고쳐 주세요", manual, quoteOptions);
  const second = api.jobs[1].request;
  assert.equal(second.previous_candidate, null);
  assert.equal(second.rule_fields.find((field) => field.field === "amount")?.value, 6_500_000);
  api.jobs[1].resolve(response(second, {
    amount: { action: "set", value: 6_500_000, origin_request_id: second.request_id },
  }));
  await flush();
  assert.equal(session.apply(manual, (changes) =>
    assert.deepEqual(changes, { amount: 6_500_000 })), true);
});

test("충돌은 선택 전 적용하지 않으며 특정 대안을 고른 뒤만 반영해요", async () => {
  const api = pending();
  const session = new AssistSession("quote", "visit-id", api.requester);
  session.send("견적 455만 원 아니고 555만 원", quote, quoteOptions);
  api.jobs[0].resolve(response(api.jobs[0].request, {
    amount: { action: "uncertain", status: "conflict", alternatives: [
      { action: "set", value: 4_550_000, source: "rule", evidence: "455만" },
      { action: "set", value: 5_550_000, source: "llm", evidence: "555만" },
    ] },
  }));
  await flush();
  let calls = 0;
  assert.equal(session.apply(quote, () => ++calls), false);
  session.chooseAlternative("amount", 1);
  assert.equal(session.apply(quote, (changes) => {
    assert.deepEqual(changes, { amount: 5_550_000 });
    calls++;
  }), true);
  assert.equal(calls, 1);
});

test("수동 수정 이후 오래된 후보와 부분 기간 충돌을 거부해요", async () => {
  const api = pending();
  const quoteSession = new AssistSession("quote", "visit-id", api.requester);
  quoteSession.send("견적 455만 원", quote, quoteOptions);
  api.jobs[0].resolve(response(api.jobs[0].request, { amount: { action: "set", value: 4_550_000 } }));
  await flush();
  assert.equal(quoteSession.apply({ ...quote, amount: 3_000_000 }, () => assert.fail("오래된 값")), false);
  assert.match(quoteSession.view.error ?? "", /직접 고친/);

  const period = { start: "2026-12-02", end: "2026-12-05", excluded: ["2026-12-04"] };
  const calendar = new AssistSession("period", "period-id", api.requester);
  calendar.send("12/6부터 12/8까지 변경", period, { today });
  api.jobs[1].resolve(response(api.jobs[1].request, {
    start: { action: "set", value: "2026-12-06" },
    end: { action: "set", value: "2026-12-08" },
    excluded: { action: "clear", value: null },
  }));
  await flush();
  calendar.select("end", false);
  assert.equal(calendar.apply(period, () => assert.fail("시작>종료")), false);
  calendar.select("end", true);
  assert.equal(calendar.apply(period, () => assert.fail("제외일이 범위 밖")), false);
  calendar.select("excluded", true);
  assert.equal(calendar.apply(period, (changes) =>
    assert.deepEqual(changes, { start: "2026-12-06", end: "2026-12-08", excluded: null })), true);
});

test("취소·교체 후 늦은 응답은 이전 후보를 되살리지 않아요", async () => {
  const api = pending();
  const session = new AssistSession("quote", "visit-id", api.requester);
  session.send("455만 원", quote, quoteOptions);
  session.cancel();
  assert.equal(api.jobs[0].signal.aborted, true);
  api.jobs[0].resolve(response(api.jobs[0].request, { amount: { action: "set", value: 4_550_000 } }));
  await flush();
  assert.equal(session.view.phase, "idle");
  session.send("555만 원", quote, quoteOptions);
  api.jobs[1].resolve(response(api.jobs[1].request, { amount: { action: "set", value: 5_550_000 } }));
  await flush();
  assert.equal(session.view.response?.fields.find((field) => field.field === "amount")?.value, 5_550_000);
  session.dispose();
  assert.equal(session.send("다음 글", quote, quoteOptions), false);
});

test("React StrictMode처럼 구독 해제·dispose 후 같은 세션 재구독이 가능해요", async () => {
  const api = pending();
  const session = new AssistSession("quote", "visit-id", api.requester);
  const first = session.subscribe(() => {});
  first();
  session.dispose();
  const seen: string[] = [];
  const unsubscribe = session.subscribe((view) => seen.push(view.phase));
  assert.equal(session.send("견적 455만 원", quote, quoteOptions), true);
  assert.equal(api.jobs.length, 1);
  api.jobs[0].resolve(response(api.jobs[0].request, { amount: { action: "set", value: 4_550_000 } }));
  await flush();
  assert.deepEqual(seen, ["idle", "loading", "ready"]);
  unsubscribe();
  session.dispose();
});

test("견적 폼 최초 마운트·StrictMode 재실행은 임시 저장하지 않고 실제 변경만 저장해요", () => {
  const draft = { amount: "4550000", vat: "included" };
  assert.equal(shouldAutosaveQuote(draft, draft), false);
  assert.equal(shouldAutosaveQuote(draft, { ...draft }), false);
  assert.equal(shouldAutosaveQuote(draft, { amount: "5550000", vat: "included" }), true);
  assert.equal(shouldAutosaveQuote(draft, { amount: "4550000", vat: "excluded" }), true);
  assert.equal(shouldAutosaveQuote(draft, { amount: "", vat: "included" }), false);
  assert.equal(shouldAutosaveQuote(draft, { amount: "4550000", vat: null }), false);
});

test("서버의 요청·권한·업무 상태 오류는 규칙 후보로 우회할 수 없어요", async () => {
  for (const code of [400, 401, 403, 404, 409, 413, 422, 429]) {
    const api = pending();
    const session = new AssistSession("quote", "visit-id", api.requester);
    session.send("견적 455만 원", quote, quoteOptions);
    api.jobs[0].reject(new AssistApiError(code, "denied", "권한 또는 요청 횟수 오류 " + code));
    await flush();
    assert.equal(session.view.phase, "denied");
    assert.equal(session.view.response, null);
    assert.match(session.view.error ?? "", new RegExp(String(code)));
    assert.equal(session.apply(quote, () => assert.fail("권한 차단")), false);
  }
});

test("네트워크 장애는 규칙 미리보기만 남기고 명시적 확인을 기다려요", async () => {
  const api = pending();
  const session = new AssistSession("quote", "visit-id", api.requester);
  session.send("견적 455만 원, 공사 3일", quote, quoteOptions);
  api.jobs[0].reject(new AssistApiError(0, "network", "서버 연결 실패"));
  await flush();
  assert.equal(session.view.phase, "ready");
  assert.equal(session.view.response?.source, "rule");
  assert.match(session.view.response?.warnings.join("") ?? "", /연결 실패/);
  assert.equal(session.view.response?.fields.find((field) => field.field === "amount")?.value, 4_550_000);
  assert.equal(session.apply(quote, (changes) => {
    assert.deepEqual(changes, { amount: 4_550_000, days: 3 });
  }), true);
});

test("이전 후보가 있을 때 5xx·네트워크·잘못된 응답이면 새 규칙 후보만 남겨요", async () => {
  for (const failure of [
    new AssistApiError(0, "network", "연결 실패"),
    new AssistApiError(503, "unavailable", "서버 응답 실패"),
    new Error("응답 구조 오류"),
  ]) {
    const api = pending();
    const session = new AssistSession("quote", "visit-id", api.requester);
    session.send("견적 500만 원", quote, quoteOptions);
    api.jobs[0].resolve(response(api.jobs[0].request, {
      amount: { action: "set", value: 5_000_000 },
    }));
    await flush();
    const text = "금액은 600만 정도였나? 아니면 700만 원인지 모르겠어요";
    session.send(text, quote, quoteOptions);
    const next = api.jobs[1].request;
    assert.equal(next.rule_fields.find((field) => field.field === "amount")?.action, "keep");
    assert.equal(next.previous_candidate?.fields.find((field) => field.field === "amount")?.value, 5_000_000);
    assert.equal(session.view.phase, "loading");
    assert.equal(session.apply(quote, () => assert.fail("대기 중 적용")), false);
    api.jobs[1].reject(failure);
    await flush();
    assert.equal(session.view.response?.source, "rule");
    assert.equal(session.view.response?.fields.find((field) => field.field === "amount")?.action, "keep");
    assert.notEqual(session.view.response?.fields.find((field) => field.field === "amount")?.value, 5_000_000);
    assert.match(session.view.response?.warnings.join(" ") ?? "", /이전 후보는 서버에서 다시 확인하지 못해/);
    assert.equal(session.apply(quote, () => assert.fail("과거 금액을 적용")), false);
    session.send("공사 3일", quote, quoteOptions);
    assert.equal(api.jobs[2].request.previous_candidate, null);
  }
});

test("이전 500만 원과 모호한 현재 600만 원 표현을 서버 실패에서 합쳐 승인하지 않아요", async () => {
  const api = pending();
  const session = new AssistSession("quote", "visit-id", api.requester);
  session.send("견적 500만 원", quote, quoteOptions);
  api.jobs[0].resolve(response(api.jobs[0].request, { amount: { action: "set", value: 5_000_000 } }));
  await flush();
  session.send("금액은 600만 정도였나?", quote, quoteOptions);
  api.jobs[1].reject(new AssistApiError(0, "network", "서버 연결 실패"));
  await flush();
  assert.notEqual(session.view.response?.fields.find((field) => field.field === "amount")?.value, 5_000_000);
  assert.equal(session.view.response?.source, "rule");
  // 규칙이 이번 글에서 새 숫자를 읽었다면 반드시 사용자가 명시적으로 확인해야 적용돼요.
  assert.equal(session.view.phase, "ready");
});

test("날짜 배열을 처음 보낸 뒤 원본을 수정해도 전송 기준값이 바뀌지 않아요", async () => {
  const api = pending();
  const workDays = ["2026-12-02", "2026-12-03"];
  const current = { days: workDays, crew: 2 };
  const session = new AssistSession("dates", "deal-id", api.requester);
  session.send("12/8부터 12/9까지 공사", current, { today, requiredDays: 2 });
  const first = api.jobs[0].request;
  assert.deepEqual(first.current.days, ["2026-12-02", "2026-12-03"]);
  api.jobs[0].resolve(response(first, {
    days: { action: "set", value: ["2026-12-08", "2026-12-09"] },
  }));
  await flush();
  workDays.push("2026-12-04");
  assert.deepEqual(first.current.days, ["2026-12-02", "2026-12-03"]);
  session.send("인원 3명", current, { today, requiredDays: 2 });
  assert.equal(api.jobs[1].request.previous_candidate, null);
});

test("허용 범위 밖 금액·정책 위반 부가세·잘못된 날짜는 후보로 적용하지 않아요", async () => {
  const api = pending();
  const session = new AssistSession("quote", "visit-id", api.requester);
  session.send("견적 9999999999원", quote, { ...quoteOptions, vatModes: ["included"], immutableFields: ["vat"] });
  api.jobs[0].resolve(response(api.jobs[0].request, {
    amount: { action: "set", value: 9_999_999_999 },
    vat: { action: "set", value: "exempt" },
    valid: { action: "set", value: "2026-02-31" },
  }));
  await flush();
  assert.equal(session.view.response?.fields.find((field) => field.field === "amount")?.status, "invalid");
  assert.equal(session.view.response?.fields.find((field) => field.field === "vat")?.status, "invalid");
  assert.equal(session.view.response?.fields.find((field) => field.field === "valid")?.status, "invalid");
  assert.equal(session.apply(quote, () => assert.fail("유효하지 않은 후보")), false);
  assert.equal(validSetValue("quote", "amount", 1_000_000_000, quoteOptions), true);
  assert.equal(validSetValue("extra", "amount", 1_000_000_000, quoteOptions), true);
  assert.equal(validSetValue("extra", "amount", 1_000_000_001, quoteOptions), false);
});

test("#176 날짜·추가 금액 파서를 기존 기간·견적 규칙에 이어 사용해요", () => {
  assert.deepEqual(parseDates("12/8부터 12/10까지, 2명", today, null, ["2026-12-09"], false), {
    days: ["2026-12-08", "2026-12-10"], crew: 2,
  });
  const extra = parseExtra("곰팡이 방지 처리 12만 원, 부가세 포함, 벽 안쪽까지 곰팡이가 퍼져서", today);
  assert.equal(extra.amount, 120_000);
  assert.equal(extra.vat, "included");
  assert.equal(extra.title, "곰팡이 방지 처리");
  assert.equal(extra.reason, "벽 안쪽까지 곰팡이가 퍼져서");
  assert.equal(ruleFields("dates", "2명", { today, crewMax: 6 }).find((field) => field.field === "days")?.action, "keep");
});

test("브라우저 API는 정확히 1개 POST 요청을 보내고 인증 오류를 그대로 전달해요", async () => {
  const original = globalThis.fetch;
  const received: { path: string; init: RequestInit }[] = [];
  globalThis.fetch = (async (path: RequestInfo | URL, init?: RequestInit) => {
    received.push({ path: String(path), init: init ?? {} });
    return { ok: false, status: 403, json: async () => ({ error: { code: "forbidden", message: "접근이 거부됐어요." } }) } as Response;
  }) as typeof fetch;
  try {
    const request: AssistRequest = {
      request_id: "test", form: "quote", resource_id: "visit-id", text: "견적 455만 원",
      current: { ...quote }, rule_fields: ruleFields("quote", "견적 455만 원", quoteOptions),
      previous_candidate: {
        request_id: "prev", text: "견적 500만 원",
        fields: [{ field: "amount", action: "set", value: 5_000_000, evidence: "500만 원" }],
        sources: { amount: "llm" }, origins: { amount: "origin-prev" },
        baselines: { amount: null },
      },
    };
    await assert.rejects(interpretAssist(request, new AbortController().signal), (error) =>
      error instanceof AssistApiError && error.status === 403 && error.code === "forbidden");
    assert.equal(received.length, 1);
    assert.equal(received[0].path, "/api/vendor/assist/interpret");
    assert.equal(received[0].init.method, "POST");
    assert.equal(received[0].init.credentials, "same-origin");
    const serialized = JSON.parse(String(received[0].init.body));
    assert.deepEqual(serialized, request);
    assert.deepEqual(serialized.previous_candidate.baselines, { amount: null });
    assert.deepEqual(serialized.previous_candidate.sources, { amount: "llm" });
    assert.deepEqual(serialized.previous_candidate.origins, { amount: "origin-prev" });
  } finally {
    globalThis.fetch = original;
  }
});
