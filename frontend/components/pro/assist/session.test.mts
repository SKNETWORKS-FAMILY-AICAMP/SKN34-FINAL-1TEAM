// 업체 도우미 후보·적용 상태 검증. API는 가짜 응답으로 대체하고 업무 폼은 직접 수정하지 않아요.
import assert from "node:assert/strict";
import { test } from "node:test";
import { AssistSession } from "./session.ts";
import { AssistApiError, interpretAssist } from "./api.ts";
import { parseDates, parseExtra } from "./parse.ts";
import { ruleFields } from "./rules.ts";
import { emptyFormHolds, mergeFormHolds, manuallyResolveHolds, formWriteBlock } from "./formHolds.ts";
import { shouldAutosaveQuote } from "./quoteAutosave.ts";
import { periodIssueScope, resolvePeriodHolds, type PeriodHolds } from "./periodHold.ts";
import {
  freshPrevious, previousCandidate, readResponse, validSetValue,
  type AssistCurrent, type AssistRequest, type AssistForm, type CandidateField,
} from "./protocol.ts";

const today = "2026-11-23";
const quote = { amount: null, vat: null, days: null, valid: null };
const quoteOptions = { today, daysMax: 40, vatModes: ["included", "excluded", "exempt"] };

test("복합 금액은 전체 값 확인 전 저장을 막고 서버가 돌려준 끝자리 후보를 제외해요", async () => {
  const api = pending();
  const session = new AssistSession("quote", "visit-id", api.requester);
  const current = { ...quote, amount: 1_000_000 };
  let blocked = false;
  session.send("견적 1천500만 원, 부가세 별도", current, quoteOptions, (issues) => { blocked = !!issues.amount; });
  assert.equal(blocked, true);
  const job = api.jobs[0];
  job.resolve(response(job.request, { amount: { action: "set", value: 5_000_000 }, vat: { action: "set", value: "excluded" } }));
  await flush();
  let changes: AssistCurrent = {};
  session.select("amount", true);
  session.apply(current, (selected) => { changes = selected; });
  assert.deepEqual(changes, { vat: "excluded" });
});

test("인원 범위를 말하면 API가 한쪽 숫자를 반환해도 인원 후보로 적용하지 않아요", async () => {
  const api = pending();
  const session = new AssistSession("dates", "deal-id", api.requester);
  const current = { crew: 2, days: ["2026-12-08"] };
  let held = false;
  session.send("작업 인원은 2~3명", current, { today, crewMax: 6 }, (issues) => { held = !!issues.crew; });
  assert.equal(held, true);
  const job = api.jobs[0];
  job.resolve(response(job.request, { crew: { action: "set", value: 3 } }));
  await flush();
  session.select("crew", true);
  assert.equal(session.apply(current, () => assert.fail("인원 적용")), false);
});

test("검증된 인원 정정은 새 값만 적용하며 적용 전에는 기존 인원 발송을 보류해요", async () => {
  const api = pending();
  const session = new AssistSession("dates", "deal-id", api.requester);
  const current = { crew: 3, days: ["2026-12-08"] };
  const opts = { today, crewMax: 6, requiredDays: 1 };
  session.send("인원 5명", current, opts);
  api.jobs[0].resolve(response(api.jobs[0].request, { crew: { action: "set", value: 5 } }));
  await flush();
  let held = false;
  session.send("인원만 다섯에서 네 명으로 정정할게요", current, opts, (issues) => { held = !!issues.crew; });
  assert.equal(held, true);
  assert.equal(api.jobs[1].request.previous_candidate?.fields.find((field) => field.field === "crew")?.value, 5);
  assert.equal(session.apply(current, () => assert.fail("검증 전 적용")), false);
  api.jobs[1].resolve(response(api.jobs[1].request, { crew: {
    action: "set", value: 4, origin_request_id: api.jobs[1].request.request_id,
    correction: { kind: "replace", rejected_value: 5, rejected_evidence: "다섯", accepted_evidence: "네 명" },
  } }));
  await flush();
  assert.equal(held, true);
  assert.equal(session.apply({ ...current, crew: 2 }, () => assert.fail("직접 수정값 덮기")), false);
  assert.equal(session.apply(current, (changes) => {
    assert.deepEqual(changes, { crew: 4 });
    if ("crew" in changes) held = false;
  }), true);
  assert.equal(held, false);
});

test("잘못된 정정 후보·이전 값 불일치·범위 초과·서버 실패는 인원 발송 보류를 유지해요", async () => {
  const original = { crew: 3, days: ["2026-12-08"] };
  const opts = { today, crewMax: 6, requiredDays: 1 };
  const cases = [
    { text: "인원만 다섯에서 네 명으로 정정할게요", next: 5 },
    { text: "인원만 다섯에서 네 명으로 정정할게요", next: 4, wrongOrigin: true },
    { text: "인원만 다섯에서 네 명으로 정정할게요", next: 4, wrongOld: true },
    { text: "인원만 다섯에서 네 명으로 정정할게요", next: 4, missingProof: true },
    { text: "인원만 다섯에서 네 명으로 정정할게요", next: 4, stale: true },
    { text: "인원만 다섯에서 네 명으로 정정할게요", next: 4, offline: true },
    { text: "인원만 여섯에서 네 명으로 정정할게요", next: 4 },
    { text: "인원만 다섯에서 일곱 명으로 정정할게요", next: 7 },
    { text: "인원은 4~5명", next: 4 },
    { text: "인원은 5에서 4명으로 정정할까요?", next: 4 },
  ];
  for (const c of cases) {
    const api = pending();
    const session = new AssistSession("dates", "deal-id", api.requester);
    session.send("인원 5명", original, opts);
    api.jobs[0].resolve(response(api.jobs[0].request, { crew: { action: "set", value: 5 } }));
    await flush();
    const current = c.stale ? { ...original, crew: 2 } : original;
    let held = false;
    session.send(c.text, current, opts, (issues) => { held = !!issues.crew; });
    assert.equal(held, true, c.text);
    if (c.offline) api.jobs[1].reject(new Error("offline"));
    else api.jobs[1].resolve(response(api.jobs[1].request, { crew: {
      action: "set", value: c.next,
      origin_request_id: c.wrongOrigin ? api.jobs[0].request.request_id : api.jobs[1].request.request_id,
      correction: c.missingProof ? undefined : {
        kind: "replace", rejected_value: c.wrongOld ? 6 : 5,
        rejected_evidence: "다섯", accepted_evidence: "네 명",
      },
    } }));
    await flush();
    session.select("crew", true);
    assert.equal(session.apply(current, () => assert.fail("미확정 인원 적용")), false, c.text);
    assert.equal(held, true, c.text);
  }
});

test("인원 범위와 잘린 수는 명시적 날짜가 유효해도 서버·오프라인 후보에서 발송 보류해요", async () => {
  const days = ["2027-01-05", "2027-01-06"];
  const current = { crew: 2, days: [] };
  const opts = { today, crewMax: 6, requiredDays: 2, noWeekend: true };
  for (const phrase of ["2〜3명", "2—3명", "2－3명", "2인〜3인", "두세 인", "1,003명", "1,03명"]) {
    for (const offline of [false, true]) {
      const api = pending();
      const session = new AssistSession("dates", "deal-id", api.requester);
      let held = false;
      session.send("2027년 1월 5일부터 6일까지, " + phrase, current, opts, (issues) => { held = !!issues.crew; });
      assert.equal(held, true, phrase);
      const job = api.jobs[0];
      if (offline) job.reject(new Error("offline"));
      else job.resolve(response(job.request, {
        days: { action: "set", value: days },
        crew: { action: "set", value: 3 },
      }));
      await flush();
      let changes: AssistCurrent = {};
      session.select("crew", true);
      assert.equal(session.apply(current, (selected) => { changes = selected; }), true, phrase);
      assert.deepEqual(changes, { days }, phrase);
    }
  }
});
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

test("이전 금액의 정정은 신선한 같은 칸의 후보와 서버 검증을 모두 요구해요", async () => {
  const cases = [
    { form: "quote" as const, text: "앞서 455만 원이라고 적었는데 계산을 틀리게 했어요. 487만 2천 원으로 수정해 주세요." },
    { form: "extra" as const, text: "부가세는 그대로, 455만 원이라고 말했던 추가 비용만 487만 2천 원으로 바꿀게요." },
  ];
  for (const { form, text } of cases) {
    for (const mode of ["valid", "stale", "wrong-old", "wrong-new", "missing-relation", "wrong-origin", "offline", "conditional", "cancelled", "question"]) {
      const api = pending();
      const session = new AssistSession(form, "resource", api.requester);
      const current = form === "quote"
        ? { ...quote, amount: 6_000_000 }
        : { title: null, reason: null, vat: null, amount: 6_000_000 };
      session.send("금액 455만 원", current, quoteOptions);
      const first = api.jobs[0];
      first.resolve(response(first.request, { amount: { action: "set", value: mode === "wrong-old" ? 4_560_000 : 4_550_000 } }));
      await flush();
      const latest = mode === "stale" ? { ...current, amount: 6_100_000 } : current;
      const phrase = mode === "conditional" ? "가능하면, " + text
        : mode === "cancelled" ? text + " 금액 정정 취소할게요."
        : mode === "question" ? text.replace(/\.$/, "?") : text;
      let held = false;
      session.send(phrase, latest, quoteOptions, (issues) => { held = !!issues.amount; });
      assert.equal(held, true, form + " " + mode);
      assert.equal(session.apply(latest, () => assert.fail("응답 전 적용")), false);
      const job = api.jobs[1];
      if (mode === "offline") job.reject(new AssistApiError(503, "unavailable", "연결 실패"));
      else job.resolve(response(job.request, {
        amount: {
          action: "set", value: mode === "wrong-new" ? 4_870_000 : 4_872_000,
          origin_request_id: mode === "wrong-origin" ? first.request.request_id : job.request.request_id,
          correction: mode === "missing-relation" ? undefined : {
            kind: "replace", rejected_value: 4_550_000,
            rejected_evidence: "455만 원", accepted_evidence: "487만 2천 원",
          },
        },
      }));
      await flush();
      let applied: AssistCurrent | undefined;
      session.select("amount", true);
      session.apply(latest, (changes) => { applied = changes; });
      assert.equal(applied?.amount, mode === "valid" ? 4_872_000 : undefined, form + " " + mode);
    }
  }
});

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

test("#179 추가금: 서버 연결 실패에도 공사 제목만 올바른 규칙 후보로 남아요", async () => {
  const current = {
    title: "걸레받이 보수", reason: "철거 중 파손 발견", amount: 180_000, vat: "excluded",
  };
  const api = pending();
  const session = new AssistSession("extra", "deal-id", api.requester);
  const text = "사유 말고 공사 제목을 몰딩 재시공으로 바꿔 주세요.";
  assert.ok(session.send(text, current, { today }));
  const request = api.jobs[0].request;
  assert.deepEqual(request.rule_fields.map((item) => [item.field, item.action, item.value]), [
    ["title", "set", "몰딩 재시공"],
    ["reason", "keep", null],
    ["amount", "keep", null],
    ["vat", "keep", null],
  ]);
  api.jobs[0].reject(new AssistApiError(0, "network", "서버 연결 실패"));
  await flush();
  assert.equal(session.view.phase, "ready");
  assert.equal(session.view.response?.source, "rule");
  assert.equal(session.view.response?.fields.find((field) => field.field === "title")?.value, "몰딩 재시공");
  const changes: AssistCurrent[] = [];
  assert.equal(session.apply(current, (value) => changes.push(value)), true);
  assert.deepEqual(changes, [{ title: "몰딩 재시공" }]);
  assert.deepEqual(current, {
    title: "걸레받이 보수", reason: "철거 중 파손 발견", amount: 180_000, vat: "excluded",
  });
});

test("#179 추가금: 제목 후보를 유지하면서 직접 고친 금액은 덮어쓰지 않아요", async () => {
  const original = {
    title: "걸레받이 보수", reason: "철거 중 파손 발견", amount: 180_000, vat: "excluded",
  };
  const api = pending();
  const session = new AssistSession("extra", "deal-id", api.requester);
  const firstText = "제목은 문틀 보강, 금액은 30만 원으로 변경해 주세요.";
  assert.ok(session.send(firstText, original, { today }));
  const first = api.jobs[0].request;
  assert.equal(first.rule_fields.find((item) => item.field === "title")?.value, "문틀 보강");
  assert.equal(first.rule_fields.find((item) => item.field === "amount")?.value, 300_000);
  assert.equal(first.rule_fields.find((item) => item.field === "reason")?.action, "keep");
  api.jobs[0].resolve(response(first, {
    title: {
      action: "set", value: "문틀 보강", evidence: "제목은 문틀 보강",
      source: "rule", origin_request_id: first.request_id,
    },
    amount: {
      action: "set", value: 300_000, evidence: "금액은 30만 원으로 변경",
      origin_request_id: first.request_id,
    },
  }));
  await flush();
  const manual = { ...original, amount: 280_000 };
  const followText = "제가 직접 입력한 금액은 그대로 두고 부가세 포함으로 바꿔 주세요. 아까 제목 변경도 유지해 주세요.";
  assert.ok(session.send(followText, manual, { today }));
  const next = api.jobs[1].request;
  assert.deepEqual(next.previous_candidate?.fields.map((item) => item.field), ["title"]);
  assert.deepEqual(next.previous_candidate?.baselines, { title: original.title });
  assert.equal(next.previous_candidate?.sources?.title, "rule");
  assert.equal(next.previous_candidate?.origins?.title, first.request_id);
  assert.equal(next.rule_fields.find((item) => item.field === "title")?.action, "keep");
  assert.equal(next.rule_fields.find((item) => item.field === "amount")?.action, "keep");
  assert.equal(next.rule_fields.find((item) => item.field === "vat")?.value, "included");
  api.jobs[1].resolve(response(next, {
    title: {
      action: "set", value: "문틀 보강", evidence: "제목은 문틀 보강",
      source: "rule", origin_request_id: first.request_id,
    },
    vat: { action: "set", value: "included", origin_request_id: next.request_id },
  }));
  await flush();
  const changes: AssistCurrent[] = [];
  assert.equal(session.apply(manual, (value) => changes.push(value)), true);
  assert.deepEqual(changes, [{ title: "문틀 보강", vat: "included" }]);
  assert.equal(manual.amount, 280_000);
  assert.equal(manual.reason, "철거 중 파손 발견");
});

test("#179 추가금: 경쟁 제목이나 부정 요청은 규칙 제목 후보로 승격하지 않아요", () => {
  for (const text of [
    "항목은 누수 보수, 제목은 몰딩 재시공",
    "제목은 누수 보수 말고 몰딩 재시공",
    "제목은 문틀 보강으로 바꾸지 마세요",
    "제목은 문틀 보강으로 변경할까요?",
    "사유는 기존 벽체 손상이라고 적어 주세요",
  ]) {
    const title = ruleFields("extra", text, { today }).find((item) => item.field === "title");
    assert.equal(title?.action, "keep", text);
  }
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

test("제외일만 옮기는 후보의 적용은 해당 보류만 풀고 미확정 기간 경계는 보존해요", async () => {
  const current = { start: "2026-12-02", end: "2026-12-24", excluded: ["2026-12-09", "2026-12-16"] };
  const phrase = "제외일 중 9일 쉬는 걸 11일 쉬는 걸로 옮겨주세요.";
  for (const mode of ["only-exclusion", "prior-boundary", "unresolved-boundary"] as const) {
    const api = pending();
    const session = new AssistSession("period", "visit-id", api.requester);
    let holds: PeriodHolds = mode === "prior-boundary" ? { boundary: "이전 기간 보류" } : {};
    session.send(phrase, current, { today, baseMonth: "2026-12" }, (issues) => {
      assert.ok(issues.period);
      assert.equal(issues.periodScope, "excluded");
      holds = { ...holds, [issues.periodScope!]: issues.period };
    });
    const { request } = api.jobs[0];
    api.jobs[0].resolve(response(request, {
      ...(mode === "unresolved-boundary" ? { end: { action: "uncertain", status: "uncertain" } } : {}),
      excluded: {
        action: "set", value: ["2026-12-11", "2026-12-16"], value_source: "derived",
        correction: { kind: "replace", rejected_value: ["2026-12-09"], rejected_evidence: "9일", accepted_evidence: "11일" },
      },
    }));
    await flush();
    assert.ok(holds.excluded, "후보만 도착하면 보류 유지");
    session.select("excluded", true);
    assert.equal(session.apply(current, (changes) => {
      assert.deepEqual(changes, { excluded: ["2026-12-11", "2026-12-16"] });
      holds = resolvePeriodHolds(holds, changes, session.view.response!);
    }), true);
    if (mode === "only-exclusion") assert.deepEqual(holds, {});
    else if (mode === "prior-boundary") assert.deepEqual(holds, { boundary: "이전 기간 보류" });
    else assert.ok(holds.excluded);
  }
});

test("기간 경계 언급 또는 범위 요청이 있으면 제외일 보류로 축소하지 않아요", () => {
  for (const phrase of [
    "종료일은 미정이고 제외일 9일을 11일로 옮겨 주세요.",
    "12월 9일부터 11일까지 제외해 주세요.",
    "제외일 9일을 옮겨 주세요. 11일은 가능할까요?",
  ]) assert.equal(periodIssueScope(phrase), "boundary", phrase);
  assert.equal(periodIssueScope("마감 날짜는 제가 방금 직접 수정했으니까 그 값 유지해주시고, 휴무 중 11일만 12월 12일로 옮겨주세요."), "excluded");
});

test("제외일 전체 해제는 선택과 적용 후에만 기존 제외일을 비워요", async () => {
  const api = pending();
  const session = new AssistSession("period", "visit-id", api.requester);
  const current = { start: "2026-12-02", end: "2026-12-24", excluded: ["2026-12-09"] };
  session.send("등록된 제외일은 모두 해제해주세요.", current, { today });
  const { request } = api.jobs[0];
  api.jobs[0].resolve(response(request, { excluded: { action: "clear", value: null } }));
  await flush();
  assert.equal(session.apply(current, () => assert.fail("선택 전 적용")), false);
  session.select("excluded", true);
  assert.equal(session.apply(current, (changes) => {
    assert.deepEqual(changes, { excluded: null });
    assert.deepEqual(resolvePeriodHolds({ excluded: "이전 제외일 보류" }, changes, session.view.response!), {});
    assert.deepEqual(current.excluded, ["2026-12-09"]);
  }), true);
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

test("실제 HTTP JSON 3턴에서 미확정 금액·부가세의 원 출처와 최초 기준값을 누락하지 않아요", async () => {
  const original = globalThis.fetch;
  const sent: AssistRequest[] = [];
  const current = { amount: 3_200_000, vat: null, days: 2, valid: null };
  const messages = [
    "금액은 590만 원, 부가세는 별도로 제안해 주세요.",
    "금액은 방금 제안한 대로 유지하고 부가세 조건만 포함으로 정정해 주세요.",
    "견적 금액과 부가세는 앞서 정리한 안으로 두고, 공사 기간은 3일로 조정해 주세요.",
  ];
  const session = new AssistSession("quote", "visit-id");
  globalThis.fetch = (async (path: RequestInfo | URL, init?: RequestInit) => {
    assert.equal(String(path), "/api/vendor/assist/interpret");
    assert.equal(init?.method, "POST");
    const wire = JSON.parse(String(init?.body)) as AssistRequest;
    sent.push(wire);
    const firstId = sent[0].request_id;
    const secondId = sent[1]?.request_id;
    const amount = {
      action: "set" as const, value: 5_900_000, evidence: "590만 원",
      origin_request_id: firstId, source: "llm" as const,
    };
    const vat = sent.length === 1 ? {
      action: "set" as const, value: "excluded", evidence: "부가세는 별도로",
      origin_request_id: firstId, source: "llm" as const,
    } : {
      action: "set" as const, value: "included", evidence: "부가세 조건만 포함으로",
      origin_request_id: secondId, source: "rule" as const,
    };
    const days = sent.length >= 3 ? {
      action: "set" as const, value: 3, evidence: "공사 기간은 3일",
      origin_request_id: wire.request_id, source: "llm" as const,
    } : undefined;
    return {
      ok: true, status: 200,
      json: async () => response(wire, { amount, vat, ...(days ? { days } : {}) }),
    } as Response;
  }) as typeof fetch;
  try {
    for (const text of messages) {
      assert.equal(session.send(text, current, quoteOptions), true);
      await flush();
      assert.equal(session.view.phase, "ready");
    }
    assert.equal(sent.length, 3);
    assert.equal(sent[0].previous_candidate, null);
    assert.deepEqual(sent[1].previous_candidate?.fields.map((field) => field.field), ["amount", "vat"]);
    assert.deepEqual(sent[1].previous_candidate?.baselines, { amount: 3_200_000, vat: null });
    assert.deepEqual(sent[2].previous_candidate?.fields.map((field) => field.field), ["amount", "vat"]);
    assert.deepEqual(sent[2].previous_candidate?.baselines, { amount: 3_200_000, vat: null });
    assert.deepEqual(sent[2].previous_candidate?.sources, { amount: "llm", vat: "rule" });
    assert.deepEqual(sent[2].previous_candidate?.origins, {
      amount: sent[0].request_id,
      vat: sent[1].request_id,
    });
    assert.equal(sent[2].previous_candidate?.text, messages[0] + "\n" + messages[1]);
    assert.deepEqual(sent[2].current, current);
    assert.equal(sent[2].rule_fields.length, 4);
    const actual: AssistCurrent[] = [];
    assert.equal(session.apply(current, (changes) => actual.push(changes)), true);
    assert.deepEqual(actual, [{ amount: 5_900_000, vat: "included", days: 3 }]);
  } finally {
    session.dispose();
    globalThis.fetch = original;
  }
});

test("실제 HTTP JSON: 부정된 제외일 삭제에는 이전 후보를 전송하고 수동 변경한 제외일만 제외해요", async () => {
  const original = globalThis.fetch;
  const sent: AssistRequest[] = [];
  const current = {
    start: "2026-12-05", end: "2026-12-22",
    excluded: ["2026-12-09", "2026-12-16"],
  };
  const changed = { ...current, excluded: ["2026-12-11"] };
  const options = { today };
  const texts = [
    "제외할 날을 12월 10일과 17일로 변경해 주세요.",
    "제외 날짜를 삭제하라는 뜻이 아니에요. 바로 전 제외일 변경안은 그대로 두세요.",
    "기간 종료일만 12월 24일로 바꿔 주세요. 제외일 제안은 그대로요.",
    "종료일을 12월 26일로 바꿔 주세요.",
  ];
  const session = new AssistSession("period", "period-id");
  globalThis.fetch = (async (path: RequestInfo | URL, init?: RequestInit) => {
    assert.equal(String(path), "/api/vendor/assist/interpret");
    const wire = JSON.parse(String(init?.body)) as AssistRequest;
    sent.push(wire);
    const excluded = sent.length < 4 ? {
      action: "set" as const, value: ["2026-12-10", "2026-12-17"],
      evidence: texts[0], origin_request_id: sent[0].request_id, source: "llm" as const,
    } : undefined;
    const end = sent.length >= 3 ? {
      action: "set" as const, value: sent.length === 3 ? "2026-12-24" : "2026-12-26",
      evidence: texts[sent.length - 1], origin_request_id: wire.request_id,
      source: "rule" as const,
    } : undefined;
    return {
      ok: true, status: 200,
      json: async () => response(wire, { ...(excluded ? { excluded } : {}), ...(end ? { end } : {}) }),
    } as Response;
  }) as typeof fetch;
  try {
    for (let i = 0; i < 3; i++) {
      assert.equal(session.send(texts[i], current, options), true);
      await flush();
      assert.equal(session.view.phase, "ready");
    }
    assert.equal(sent.length, 3);
    assert.equal(sent[1].rule_fields.find((field) => field.field === "excluded")?.action, "keep");
    assert.deepEqual(sent[1].previous_candidate?.baselines, {
      excluded: ["2026-12-09", "2026-12-16"],
    });
    assert.deepEqual(sent[2].previous_candidate?.fields.map((field) => field.field), ["excluded"]);
    assert.deepEqual(sent[2].previous_candidate?.origins, { excluded: sent[0].request_id });
    assert.deepEqual(sent[2].previous_candidate?.sources, { excluded: "llm" });
    assert.deepEqual(sent[2].previous_candidate?.baselines, {
      excluded: ["2026-12-09", "2026-12-16"],
    });
    assert.equal(sent[2].previous_candidate?.text, texts[0] + "\n" + texts[1]);
    assert.equal(session.send(texts[3], changed, options), true);
    const fourth = sent[3];
    assert.deepEqual(fourth.current.excluded, ["2026-12-11"]);
    assert.deepEqual(fourth.previous_candidate?.fields.map((field) => field.field), ["end"]);
    assert.deepEqual(fourth.previous_candidate?.baselines, { end: "2026-12-22" });
    assert.deepEqual(fourth.previous_candidate?.origins, { end: sent[2].request_id });
    assert.equal(session.view.response?.fields.find((field) => field.field === "excluded")?.action, "keep");
    await flush();
    assert.equal(session.apply(changed, (changes) =>
      assert.deepEqual(changes, { end: "2026-12-26" })), true);
  } finally {
    session.dispose();
    globalThis.fetch = original;
  }
});

test("한글 미확정 금액·추측한 복합 금액은 서버 응답과 연결 실패 모두에서 기존 금액 저장을 막아요", async () => {
  for (const form of ["quote", "extra"] as const) {
    const current: AssistCurrent = form === "quote"
      ? { ...quote, amount: 1_000_000, vat: "included" }
      : { amount: 1_000_000, vat: "included", title: "추가 보강", reason: "현장 요청" };
    for (const [phrase, candidate] of [
      ["견적 이천만으로", 20_000_000], ["견적 오백만으로", 5_000_000],
      ["견적 천 만으로", 10_000_000], ["견적 천만요", 10_000_000],
      ["견적 1천500만 원으로 할까?", 15_000_000],
    ] as const) {
      for (const failure of [true, false]) {
        const label = `${form}: ${phrase}, 503=${failure}`;
        const api = pending();
        const session = new AssistSession(form, "resource-id", api.requester);
        let held = false;
        assert.equal(session.send(phrase + ", 부가세 별도", current, quoteOptions, (issues) => { held = !!issues.amount; }), true, label);
        assert.equal(held, true, label);
        const job = api.jobs[0];
        assert.equal(job.request.rule_fields.find((field) => field.field === "amount")?.action, "keep", label);
        if (failure) job.reject(new AssistApiError(503, "unavailable", "연결 실패"));
        else job.resolve(response(job.request, {
          amount: { action: "set", value: candidate }, vat: { action: "set", value: "excluded" },
        }));
        await flush();
        session.select("amount", true);
        assert.equal(session.apply(current, (changes) => {
          assert.deepEqual(changes, { vat: "excluded" }, label);
        }), true, label);
      }
    }
  }
});

test("문장을 나눈 견적 정정은 새 금액 확인 전 저장을 막고 서버의 정확한 새 값만 적용해요", async () => {
  const original = "아까 총액 320만원이라고 제가 잘못 말씀드렸네요. 정확히는 294만 8천 원이에요. 그 금액으로 정정 부탁해요.";
  const current = { amount: 3_200_000, vat: "included", days: 4, valid: "2026-12-20" };
  for (const text of [original, original.replace("총액", "견적")]) for (const answer of [2_948_000, 3_200_000, 2_940_000, null]) {
    const api = pending();
    const session = new AssistSession("quote", "visit-id", api.requester);
    let held = false;
    session.send(text, current, quoteOptions, (issues) => { held = !!issues.amount; });
    assert.equal(held, true);
    assert.equal(session.apply(current, () => assert.fail("확인 전 적용")), false);
    const job = api.jobs[0];
    if (text.includes("견적")) assert.equal(job.request.rule_fields.find((f) => f.field === "amount")?.value, 3_200_000);
    if (answer === null) job.reject(new AssistApiError(503, "unavailable", "연결 실패"));
    else job.resolve(response(job.request, { amount: { action: "set", value: answer, evidence: "정확히는 294만 8천 원이에요" } }));
    await flush();
    const candidate = session.view.response?.fields.find((field) => field.field === "amount");
    if (answer === 2_948_000) {
      assert.equal(candidate?.status, "valid");
      assert.equal(session.apply(current, (changes) => assert.deepEqual(changes, { amount: 2_948_000 })), true);
    } else {
      session.select("amount", true);
      assert.equal(session.apply(current, () => assert.fail("옛 금액·잘린 금액 적용")), false);
    }
  }
});

test("문장을 나눈 정정에서 질문·취소·조건·다른 필드·제3 금액은 옛 규칙 금액으로 적용되지 않아요", async () => {
  const text = "아까 견적 320만원이라고 제가 잘못 말씀드렸네요. 정확히는 294만 8천 원이에요. 그 금액으로 정정 부탁해요.";
  const current = { amount: 3_200_000, vat: "included", days: 4, valid: "2026-12-20" };
  for (const phrase of [
    text.replace("잘못 말씀드렸네요", "말씀드렸네요"),
    text.replace("원이에요.", "원이에요?"),
    text.replace("정정 부탁해요", "정정하지 마세요"),
    text.replace("그 금액으로", "그 기간으로"),
    text.replace("그 금액으로", "가능하면 그 금액으로"),
    text.replace("그 금액으로", "300만원 대신 그 금액으로"),
    text + " 금액 정정은 취소할게요.",
  ]) {
    for (const offline of [false, true]) {
      const api = pending();
      const session = new AssistSession("quote", "visit-id", api.requester);
      let held = false;
      session.send(phrase, current, quoteOptions, (issues) => { held = !!issues.amount; });
      assert.equal(held, true, phrase);
      const job = api.jobs[0];
      if (offline) job.reject(new AssistApiError(503, "unavailable", "연결 실패"));
      else job.resolve(response(job.request, { amount: { action: "set", value: 2_948_000 } }));
      await flush();
      session.select("amount", true);
      assert.equal(session.apply(current, () => assert.fail(phrase)), false, phrase);
    }
  }
});

test("한글 견적 금액은 정확한 서버 후보를 명시적으로 적용할 때만 변경해요", async () => {
  const text = "현장 추가 물량까지 다시 잡았어요. 이번 견적 금액은 천이백육십만 원으로 넣어 주세요.";
  const evidence = "이번 견적 금액은 천이백육십만 원으로 넣어 주세요";
  for (const form of ["quote", "extra"] as const) {
    const current = form === "quote"
      ? { amount: 2_820_000, days: 5, vat: "excluded", valid: "2026-12-18" }
      : { amount: 2_820_000, title: "보강", reason: "현장 추가", vat: "excluded" };
    for (const answer of [12_600_000, 2_600_000, 12_000_000, null]) {
      const api = pending();
      const session = new AssistSession(form, "resource-id", api.requester);
      let held = false;
      assert.equal(session.send(text, current, quoteOptions, (issues) => { held = !!issues.amount; }), true);
      assert.equal(held, true);
      assert.equal(session.apply(current, () => assert.fail("확인 전 적용")), false);
      const job = api.jobs[0];
      assert.equal(job.request.rule_fields.find((field) => field.field === "amount")?.action, "keep");
      if (answer === null) job.reject(new Error("offline"));
      else job.resolve(response(job.request, { amount: { action: "set", value: answer, evidence } }));
      await flush();
      const candidate = session.view.response?.fields.find((field) => field.field === "amount");
      if (answer === 12_600_000) {
        assert.equal(candidate?.status, "valid");
        session.select("amount", true);
        let applied: AssistCurrent = {};
        assert.equal(session.apply(current, (changes) => { applied = changes; }), true);
        assert.equal(applied.amount, 12_600_000);
      } else {
        assert.notEqual(candidate?.action, "set");
        session.select("amount", true);
        session.apply(current, (changes) => assert.equal(changes.amount, undefined));
      }
    }
  }
});

test("잘린 한글 금액 및 혼합 정정은 서버가 숫자를 보내도 적용하지 않아요", async () => {
  for (const text of [
    "견적 삼백만 500원", "견적 삼백만원~사백만원", "견적 삼백만원 내외",
    "아까 삼백만원이라고 했는데 400만원으로 고칠게요",
    "아까 300만원이라고 했는데 사백만원으로 고칠게요",
  ]) {
    const api = pending();
    const session = new AssistSession("quote", "visit-id", api.requester);
    let held = false;
    session.send(text, { ...quote, amount: 3_000_000 }, quoteOptions, (issues) => { held = !!issues.amount; });
    assert.equal(held, true, text);
    api.jobs[0].resolve(response(api.jobs[0].request, { amount: { action: "set", value: 4_000_000 } }));
    await flush();
    session.select("amount", true);
    assert.equal(session.apply({ ...quote, amount: 3_000_000 }, () => assert.fail(text)), false);
  }
});

test("추가금 정정의 보고 문장이 제목 보류를 만들지 않아요", async () => {
  const api = pending();
  const session = new AssistSession("extra", "resource-id", api.requester);
  const current = { amount: 1_000_000, title: "보강", reason: "현장 요청", vat: "included" };
  let holds = emptyFormHolds();
  const issues = (value: Parameters<typeof mergeFormHolds>[2]) => { holds = mergeFormHolds("extra", holds, value); };
  session.send("금액은 287만 원으로 잡아 주세요.", current, quoteOptions, issues);
  api.jobs[0].resolve(response(api.jobs[0].request, { amount: { action: "set", value: 2_870_000 } }));
  await flush();
  session.send("아까 287만 원이라고 했는데 제가 계산을 잘못했네요. 293만 4천 원으로 정정 부탁해요.", current, quoteOptions, issues);
  const job = api.jobs[1];
  assert.equal(job.request.rule_fields.find(f => f.field === "title")?.action, "keep");
  assert.equal(holds.fields.title, undefined);
  const raw = response(job.request, { amount: {
    action: "set", value: 2_934_000, origin_request_id: job.request.request_id,
    correction: { kind: "replace", rejected_value: 2_870_000, rejected_evidence: "287만 원", accepted_evidence: "293만 4천 원" },
  } });
  job.resolve({ ...raw, fields: raw.fields.filter(f => f.field === "amount") });
  await flush();
  assert.equal(session.apply(current, changes => assert.deepEqual(changes, { amount: 2_934_000 })), true);
  assert.equal(formWriteBlock(holds), null);
});

test("수동으로 해결한 보류는 다음 서버의 새 후보를 보류하는 데 방해되지 않아요", async () => {
  const api = pending();
  const session = new AssistSession("extra", "resource-id", api.requester);
  let current = { amount: 1_000_000, title: "보강", reason: "현장 요청", vat: "included" };
  const manualVersions: Record<string, number> = {};
  let holds = emptyFormHolds();
  const issues = (value: Parameters<typeof mergeFormHolds>[2]) => { holds = mergeFormHolds("extra", holds, value); };
  const live = () => ({ current, manualVersions });
  session.send("금액 30만 원", current, quoteOptions, issues, live);
  api.jobs[0].resolve(response(api.jobs[0].request, { amount: { action: "set", value: 300_000 } }));
  await flush();
  assert.ok(holds.fields.amount);
  current = { ...current, amount: 400_000 };
  manualVersions.amount = 1;
  holds = manuallyResolveHolds(holds, ["amount"]);
  assert.equal(formWriteBlock(holds), null);
  session.send("추가 작업 조건을 다시 읽어 주세요.", current, quoteOptions, issues, live);
  const job = api.jobs[1];
  job.resolve(response(job.request, { amount: { action: "set", value: 500_000 } }));
  await flush();
  assert.ok(holds.fields.amount);
});
