// 업무 폼의 필드별 보류·부분 적용·저장 경합을 시험 전용 HTTP 서버에서 검증한다.
import { expect, test, type APIRequestContext, type Page } from "@playwright/test";
import { formKeys, type AssistForm, type AssistValue, type CandidateField } from "../../components/pro/assist/protocol";

const API = "http://127.0.0.1:8188";
const VISIT = "00000000-0000-4000-8000-000000001742";
const QUOTE = "00000000-0000-4000-8000-000000000009";
const DATES = "00000000-0000-4000-8000-00000000d101";
const EXTRA = "00000000-0000-4000-8000-00000000d102";
const QUOTE_PATH = "/vendor/visits/" + QUOTE + "/quote";
const ASSIST_PATH = "/vendor/assist/interpret";
type Write = { method: string; path: string; body: Record<string, unknown> | null };

async function writes(request: APIRequestContext): Promise<Write[]> {
  const result = await request.get(API + "/__assist-test");
  expect(result.ok()).toBeTruthy();
  const data = await result.json() as { requests: Write[] };
  return data.requests.filter((r) => r.method === "PUT" || r.method === "PATCH" ||
    r.method === "POST" && r.path !== ASSIST_PATH);
}

function candidate(field: string, value: AssistValue, evidence: string): CandidateField {
  return { field, action: "set", value, evidence, source: "llm", status: "valid",
    intent_kind: "explicit_change", value_source: "utterance", alternatives: [] };
}

function unresolved(field: string, evidence: string): CandidateField {
  return { field, action: "uncertain", value: null, evidence, source: "llm", status: "uncertain",
    intent_kind: "ambiguous", reason_code: "ambiguous_intent", alternatives: [] };
}

async function queue(request: APIRequestContext, form: AssistForm, fields: CandidateField[], delay?: number) {
  const result = await request.post(API + "/__assist-test", { data: {
    bindCurrentOrigin: true, delay,
    fields: formKeys(form).map((field) => fields.find((f) => f.field === field) ??
      { field, action: "keep", value: null, evidence: "", source: "llm", status: "valid",
        intent_kind: "no_mention", alternatives: [] }),
  } });
  expect(result.ok()).toBeTruthy();
}

async function open(page: Page, url: string) {
  await page.goto(url);
  const box = page.locator('section[aria-label="업체 도우미"]');
  await expect(box).toBeVisible();
  return box;
}

async function say(page: Page, text: string) {
  const box = page.locator('section[aria-label="업체 도우미"]');
  await box.getByRole("textbox", { name: "업체 도우미에게 보낼 말" }).fill(text);
  await box.getByRole("button", { name: "보내기", exact: true }).click();
  return box;
}

async function ready(box: ReturnType<Page["locator"]>) {
  await expect(box.getByText("변경할 칸을 고른 뒤 폼에 적용해 주세요.", { exact: true })).toBeVisible();
}

const submit = (page: Page) => page.getByRole("button", { name: "고객에게 보내기", exact: true });
const apply = (box: ReturnType<Page["locator"]>) => box.getByRole("button", { name: "폼에 적용" });
const periodSubmit = (page: Page) => page.getByRole("button", { name: /\d.*보내기$/ }).first();
const iso = (day: Date) => day.toISOString().slice(0, 10);
const koreanDate = (day: string) => {
  const [year, month, date] = day.split("-").map(Number);
  return year + "년 " + month + "월 " + date + "일";
};

function futureMonth() {
  const day = new Date();
  day.setUTCDate(1);
  day.setUTCMonth(day.getUTCMonth() + 3);
  return iso(day).slice(0, 7);
}

function workdays() {
  const day = new Date(futureMonth() + "-08T00:00:00Z");
  while (day.getUTCDay() !== 1) day.setUTCDate(day.getUTCDate() + 1);
  const days = [iso(day)];
  day.setUTCDate(day.getUTCDate() + 1);
  days.push(iso(day));
  day.setUTCDate(day.getUTCDate() + 1);
  days.push(iso(day));
  return days;
}

test.beforeEach(async ({ page, request }) => {
  expect((await request.delete(API + "/__assist-test")).ok()).toBeTruthy();
  await page.context().addCookies([{ name: "mj_sid", value: "assist-test",
    url: "http://localhost:3188", httpOnly: true, sameSite: "Lax" }]);
});

test("QA D 견적: 해석 중과 금액 미확정 상태에서는 다른 칸 적용 후에도 PUT·발송을 막아요", async ({ page, request }) => {
  await open(page, "/vendor/requests/" + QUOTE + "/quote");
  const amount = page.getByLabel("견적 금액", { exact: true });
  const save = page.getByRole("button", { name: "임시 저장", exact: true });
  await page.getByLabel("유효기간", { exact: true }).fill(futureMonth() + "-28");
  await amount.fill("4550000");
  await page.getByRole("radio", { name: "포함", exact: true }).check();
  await expect.poll(async () => (await writes(request)).map((w) => w.body?.entered_amount)).toEqual([4_550_000]);
  const baseline = (await writes(request)).length;
  const text = "견적 550만 원은 아직 미정입니다. 부가세 별도";
  await queue(request, "quote", [
    unresolved("amount", "550만 원은 아직 미정입니다"), candidate("vat", "excluded", "부가세 별도"),
  ], 1500);
  const box = await say(page, text);
  await expect(apply(box)).toBeDisabled();
  await save.click();
  await submit(page).click();
  expect(await writes(request)).toHaveLength(baseline);
  await ready(box);
  await apply(box).click();
  await expect(page.getByRole("radio", { name: "빼고", exact: true })).toBeChecked();
  await expect(amount).toHaveValue("4,550,000");
  await page.waitForTimeout(1000);
  await save.click();
  await submit(page).click();
  expect(await writes(request)).toHaveLength(baseline);
  await amount.fill("5600000");
  await expect.poll(async () => (await writes(request)).length).toBe(baseline + 1);
  expect((await writes(request)).at(-1)?.body?.entered_amount).toBe(5_600_000);
  await submit(page).click();
  await expect.poll(async () => (await writes(request)).filter((w) => w.path === QUOTE_PATH + "/send").length).toBe(1);
  expect((await writes(request)).slice(baseline).filter((w) => w.method === "PUT")
    .every((w) => w.body?.entered_amount === 5_600_000)).toBe(true);
});

test("QA D 견적: 진행 중 PUT 뒤에 기다리던 저장·발송은 새 미확정 요청으로 차단해요", async ({ page, request }) => {
  await open(page, "/vendor/requests/" + QUOTE + "/quote");
  await page.getByLabel("유효기간", { exact: true }).fill(futureMonth() + "-28");
  let release!: () => void;
  const gate = new Promise<void>((resolve) => { release = resolve; });
  let started = 0;
  await page.route("**/api" + QUOTE_PATH, async (route) => {
    if (route.request().method() === "PUT") {
      started += 1;
      if (started === 1) await gate;
    }
    await route.continue();
  });
  try {
    await page.getByLabel("견적 금액", { exact: true }).fill("4550000");
    await expect.poll(() => started).toBe(1);
    await submit(page).click(); // 자동 PUT이 끝날 때까지 발송이 기다려요.
    await queue(request, "quote", [unresolved("amount", "550만 원은 아직 미정")], 1000);
    const box = await say(page, "견적 550만 원은 아직 미정이에요");
    release();
    await ready(box);
    await page.waitForTimeout(1000);
    expect(started).toBe(1);
    expect(await writes(request)).toMatchObject([{ method: "PUT", path: QUOTE_PATH, body: { entered_amount: 4_550_000 } }]);
  } finally {
    release();
  }
});

test("QA D 추가금: 미확정 금액을 다른 칸 적용이나 직접 수정으로 해제하지 않아요", async ({ page, request }) => {
  await open(page, "/vendor/jobs/" + EXTRA + "?step=extra");
  const amount = page.getByLabel("금액", { exact: true });
  await page.getByLabel("항목", { exact: true }).fill("도배");
  await page.getByLabel("이유", { exact: true }).fill("벽 손상");
  await amount.fill("120000");
  await page.getByRole("radio", { name: "포함", exact: true }).check();
  await queue(request, "extra", [
    unresolved("amount", "30만 원은 미정"), candidate("title", "보수", "항목은 보수"),
  ]);
  const box = await say(page, "추가 금액 30만 원은 미정이고, 항목은 보수로 바꿔 주세요");
  await ready(box);
  await apply(box).click();
  await expect(page.getByLabel("항목", { exact: true })).toHaveValue("보수");
  await page.getByLabel("이유", { exact: true }).fill("벽면 보수");
  await submit(page).click();
  expect(await writes(request)).toHaveLength(0);
  await amount.fill("310000");
  await submit(page).click();
  await expect.poll(async () => (await writes(request)).length).toBe(1);
  expect((await writes(request))[0]).toMatchObject({
    method: "POST", path: "/vendor/jobs/" + EXTRA + "/extras",
    body: { title: "보수", reason: "벽면 보수", entered_amount: 310000 },
  });
});

test("QA D 공사 날짜: 필수 일수와 다른 제안이 보류되면 옛 날짜로 보내지 않아요", async ({ page, request }) => {
  await open(page, "/vendor/jobs/" + DATES);
  const [first, second, third] = workdays();
  const initial = koreanDate(first) + ", " + koreanDate(second) + " 공사할게요";
  await queue(request, "dates", [candidate("days", [first, second], initial)]);
  let box = await say(page, initial);
  await ready(box);
  await apply(box).click();
  const change = "공사 날짜는 " + koreanDate(third) + "로 바꾸고 인원은 3명으로 해 주세요";
  // 원문을 읽었어도 실제 필요 일수 2일을 만족하지 않는 후보예요.
  await queue(request, "dates", [candidate("days", [third], koreanDate(third)), candidate("crew", 3, "인원은 3명")]);
  box = await say(page, change);
  await ready(box);
  await apply(box).click();
  await expect(page.getByLabel("인원", { exact: true })).toHaveValue("3");
  await page.getByLabel("인원", { exact: true }).selectOption("4");
  await submit(page).click();
  expect(await writes(request)).toHaveLength(0);
  const corrected = koreanDate(second) + ", " + koreanDate(third) + " 공사할게요";
  await queue(request, "dates", [candidate("days", [second, third], corrected)]);
  box = await say(page, corrected);
  await ready(box);
  await apply(box).click();
  await submit(page).click();
  await expect.poll(async () => (await writes(request)).length).toBe(1);
  expect((await writes(request))[0]).toMatchObject({
    method: "POST", path: "/vendor/jobs/" + DATES + "/dates", body: { days: [second, third], crew: 4 },
  });
});

test("QA D 기간: 경계만 적용해도 미확정 제외일은 남고 하루 휴무를 적용해야 발송해요", async ({ page, request }) => {
  await open(page, "/vendor/requests/" + VISIT);
  const month = futureMonth();
  const start = month + "-08", end = month + "-16", newEnd = month + "-20";
  const oldExcluded = month + "-10", newExcluded = month + "-12";
  const initial = koreanDate(start) + "부터 " + koreanDate(end) + "까지 가능해요, " + koreanDate(oldExcluded) + "은 못 가요";
  await queue(request, "period", [
    candidate("start", start, koreanDate(start)), candidate("end", end, koreanDate(end)),
    candidate("excluded", [oldExcluded], koreanDate(oldExcluded) + "은 못 가요"),
  ]);
  let box = await say(page, initial);
  await ready(box);
  await apply(box).click();
  const exclusion = koreanDate(newExcluded) + " 또는 13일 중 미정이에요";
  const mixed = "종료일은 " + koreanDate(newEnd) + "로 정정해 주세요. 제외일은 " + exclusion;
  await queue(request, "period", [candidate("end", newEnd, koreanDate(newEnd)), unresolved("excluded", exclusion)]);
  box = await say(page, mixed);
  await ready(box);
  await apply(box).click();
  await periodSubmit(page).click();
  expect(await writes(request)).toHaveLength(0);
  const exact = koreanDate(newExcluded) + "은 못 가요";
  await queue(request, "period", [candidate("excluded", [newExcluded], exact)]);
  box = await say(page, exact);
  await ready(box);
  await apply(box).click();
  // 기존 시험 서버는 window POST를 기록한 뒤 404를 돌려준다. 이 검사는 발송 본문을 확인한다.
  await periodSubmit(page).click();
  await expect.poll(async () => (await writes(request)).length).toBe(1);
  expect((await writes(request))[0]).toMatchObject({
    method: "POST", path: "/vendor/visits/" + VISIT + "/window",
    body: { start, end: newEnd, excluded: [newExcluded] },
  });
});

test("QA D 기간: 오늘 시작일이 무효이면 제외일만 적용해도 옛 기간은 보내지 못해요", async ({ page, request }) => {
  await open(page, "/vendor/requests/" + VISIT);
  const month = futureMonth();
  const start = month + "-08", end = month + "-16", excluded = month + "-12";
  const initial = koreanDate(start) + "부터 " + koreanDate(end) + "까지 가능해요";
  await queue(request, "period", [
    candidate("start", start, koreanDate(start)), candidate("end", end, koreanDate(end)),
  ]);
  let box = await say(page, initial);
  await ready(box);
  await apply(box).click();
  const today = iso(new Date(Date.now() + 9 * 3_600_000));
  await queue(request, "period", [candidate("start", today, koreanDate(today))]);
  box = await say(page, "시작일을 오늘 " + koreanDate(today) + "로 바꿔 주세요");
  await ready(box);
  await expect(apply(box)).toBeDisabled();
  await periodSubmit(page).click();
  expect(await writes(request)).toHaveLength(0);
  const dayOff = koreanDate(excluded) + "은 못 가요";
  await queue(request, "period", [candidate("excluded", [excluded], dayOff)]);
  box = await say(page, dayOff);
  await ready(box);
  await apply(box).click();
  await periodSubmit(page).click();
  expect(await writes(request)).toHaveLength(0);
  const fixedStart = month + "-09";
  await queue(request, "period", [candidate("start", fixedStart, koreanDate(fixedStart))]);
  box = await say(page, "시작일을 " + koreanDate(fixedStart) + "로 바꿔 주세요");
  await ready(box);
  await apply(box).click();
  await periodSubmit(page).click();
  await expect.poll(async () => (await writes(request)).length).toBe(1);
  expect((await writes(request))[0]).toMatchObject({
    method: "POST", path: "/vendor/visits/" + VISIT + "/window",
    body: { start: fixedStart, end, excluded: [excluded] },
  });
});
