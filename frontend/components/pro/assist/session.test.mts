// 업체 도우미 후보·적용 상태 검증. API는 가짜 응답으로 대체하고 업무 폼은 직접 수정하지 않아요.
import assert from "node:assert/strict";
import { test } from "node:test";
import { AssistSession } from "./session.ts";
import { AssistApiError, interpretAssist } from "./api.ts";
import { parseDates, parseExtra } from "./parse.ts";
import { ruleFields } from "./rules.ts";
import { shouldAutosaveQuote } from "./quoteAutosave.ts";
import { validSetValue, type AssistCurrent, type AssistRequest, type AssistForm } from "./protocol.ts";

const today = "2026-11-23";
const quote = { amount: null, vat: null, days: null, valid: null };
const quoteOptions = { today, daysMax: 40, vatModes: ["included", "excluded", "exempt"] };
type Data = Partial<Record<string, {
  origin_request_id?: string;
  action?: "set" | "clear" | "keep" | "uncertain";
  value?: string | number | string[] | null;
  status?: "valid" | "invalid" | "conflict" | "uncertain";
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
        evidence: "원문 근거", source: "llm", status: detail.status ?? "valid",
        alternatives: detail.alternatives ?? [],
        origin_request_id: detail.origin_request_id,
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

test("서버가 유지한 이전 후보도 수동 수정과 선택 해제를 보존해요", async () => {
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
  assert.equal(second.previous_candidate?.sources?.amount, "llm");
  api.jobs[1].resolve(response(second, {
    amount: { action: "set", value: 4550000, origin_request_id: first.request_id },
    valid: { action: "set", value: "2026-12-20", origin_request_id: second.request_id },
  }));
  await flush();
  assert.equal(session.apply(manual, () => assert.fail("수동 금액 덮어쓰기")), false);
  session.select("amount", false);
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
  assert.equal(session.apply(quote, () => assert.fail("응답 대기 중 반영")), false);
  api.jobs[1].resolve(response(second, { amount: { action: "set", value: 5_550_000 } }));
  await flush();
  const changed: AssistCurrent[] = [];
  assert.equal(session.apply(quote, (value) => changed.push(value)), true);
  assert.deepEqual(changed, [{ amount: 5_550_000, days: 3 }]);
  assert.equal(api.jobs.length, 2);
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

test("서버 401/403/429는 규칙 후보로 우회할 수 없고 화면에 드러나요", async () => {
  for (const code of [401, 403, 429]) {
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
  assert.equal(validSetValue("extra", "amount", 1_000_000_000, quoteOptions), false);
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
      current: { ...quote }, previous_candidate: null, rule_fields: ruleFields("quote", "견적 455만 원", quoteOptions),
    };
    await assert.rejects(interpretAssist(request, new AbortController().signal), (error) =>
      error instanceof AssistApiError && error.status === 403 && error.code === "forbidden");
    assert.equal(received.length, 1);
    assert.equal(received[0].path, "/api/vendor/assist/interpret");
    assert.equal(received[0].init.method, "POST");
    assert.equal(received[0].init.credentials, "same-origin");
    assert.deepEqual(JSON.parse(String(received[0].init.body)), request);
  } finally {
    globalThis.fetch = original;
  }
});
