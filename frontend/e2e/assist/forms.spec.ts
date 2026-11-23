// 재재 업체 도우미의 실제 폼 연결을 확인한다. 네트워크만 시험 전용 API로 대체한다.
import { expect, test, type APIRequestContext, type Page } from "@playwright/test";

const API = "http://127.0.0.1:8188";
const SANGGYE = "00000000-0000-4000-8000-000000001742";
const VISITED = "00000000-0000-4000-8000-000000000009";
const DATES = "00000000-0000-4000-8000-00000000d101";
const WORKING = "00000000-0000-4000-8000-00000000d102";
const ASSIST_PATH = "/vendor/assist/interpret";
const QUOTE_PATH = `/vendor/visits/${VISITED}/quote`;

type FormName = "period" | "quote" | "dates" | "extra";
type Value = string | number | string[] | null;
type Candidate = {
  field: string;
  action: "set" | "clear";
  value: Value;
  evidence: string;
  source: "llm";
  status: "valid";
  alternatives: [];
};
type RecordedRequest = { method: string; path: string; body: Record<string, unknown> | null };
type TestState = { requests: RecordedRequest[]; pending: number };
type NextAnswer = { fields?: Candidate[]; delay?: number; status?: number; message?: string };

function set(field: string, value: Exclude<Value, null>, evidence: string): Candidate {
  return { field, action: "set", value, evidence, source: "llm", status: "valid", alternatives: [] };
}

function clear(field: string, evidence: string): Candidate {
  return { field, action: "clear", value: null, evidence, source: "llm", status: "valid", alternatives: [] };
}

async function queueAnswer(request: APIRequestContext, answer: NextAnswer): Promise<void> {
  const response = await request.post(`${API}/__assist-test`, { data: answer });
  expect(response.ok()).toBeTruthy();
}

async function recorded(request: APIRequestContext): Promise<RecordedRequest[]> {
  const response = await request.get(`${API}/__assist-test`);
  expect(response.ok()).toBeTruthy();
  const state = (await response.json()) as TestState;
  return state.requests;
}

const interpreted = (requests: RecordedRequest[]) =>
  requests.filter((entry) => entry.method === "POST" && entry.path === ASSIST_PATH);

// PUT·PATCH·업무 POST만 저장으로 본다. 미리 보기 GET과 해석 POST는 저장이 아니다.
const writes = (requests: RecordedRequest[]) =>
  requests.filter((entry) => entry.method === "PUT" || entry.method === "PATCH" ||
    (entry.method === "POST" && entry.path !== ASSIST_PATH));

async function open(page: Page, route: string): Promise<ReturnType<Page["locator"]>> {
  await page.goto(route);
  const assistant = page.locator('section[aria-label="업체 도우미"]');
  await expect(assistant).toBeVisible();
  await expect(assistant.getByRole("textbox", { name: "업체 도우미에게 보낼 말" })).toBeVisible();
  return assistant;
}

async function send(page: Page, message: string): Promise<void> {
  const assistant = page.locator('section[aria-label="업체 도우미"]');
  await assistant.getByRole("textbox", { name: "업체 도우미에게 보낼 말" }).fill(message);
  await assistant.getByRole("button", { name: "보내기", exact: true }).click();
}

async function expectInterpretCount(request: APIRequestContext, count: number): Promise<void> {
  await expect.poll(async () => interpreted(await recorded(request)).length).toBe(count);
}

test.beforeEach(async ({ page, request }) => {
  const reset = await request.delete(`${API}/__assist-test`);
  expect(reset.ok()).toBeTruthy();
  // 서버의 /me가 승인 업체를 돌려주므로 이 쿠키만으로 실제 페이지를 연다.
  await page.context().addCookies([{
    name: "mj_sid", value: "assist-test", url: "http://localhost:3188", httpOnly: true, sameSite: "Lax",
  }]);
});

type Scenario = {
  form: FormName;
  route: string;
  resourceId: string;
  text: string;
  fields: Candidate[];
  firstChoice: string;
};

const scenarios: Scenario[] = [
  {
    form: "period", route: `/vendor/requests/${SANGGYE}`, resourceId: SANGGYE,
    text: "2027-12-02부터 2027-12-05까지 실측 가능해요",
    fields: [set("start", "2027-12-02", "2027-12-02부터"), set("end", "2027-12-05", "2027-12-05까지")],
    firstChoice: "시작일 적용 선택",
  },
  {
    form: "quote", route: `/vendor/requests/${VISITED}/quote`, resourceId: VISITED,
    text: "공사 기간을 3일로 바꿔 주세요",
    fields: [set("days", 3, "3일")], firstChoice: "공사할 날 적용 선택",
  },
  {
    form: "dates", route: `/vendor/jobs/${DATES}`, resourceId: DATES,
    text: "공사 인원을 3명으로 변경해 주세요",
    fields: [set("crew", 3, "3명")], firstChoice: "인원 적용 선택",
  },
  {
    form: "extra", route: `/vendor/jobs/${WORKING}?step=extra`, resourceId: WORKING,
    text: "추가 항목은 곰팡이 방지 처리로 해 주세요",
    fields: [set("title", "곰팡이 방지 처리", "곰팡이 방지 처리")], firstChoice: "항목 적용 선택",
  },
];

async function expectOriginal(page: Page, form: FormName): Promise<void> {
  if (form === "period") {
    await expect(page.getByRole("button", { name: "기간 보내기" }).first()).toBeVisible();
  } else if (form === "quote") {
    await expect(page.getByLabel("공사 기간", { exact: true })).toHaveValue("2");
  } else if (form === "dates") {
    await expect(page.getByLabel("인원", { exact: true })).toHaveValue("2");
  } else {
    await expect(page.getByLabel("항목", { exact: true })).toHaveValue("");
  }
}

async function expectApplied(page: Page, form: FormName): Promise<void> {
  if (form === "period") {
    await expect(page.getByRole("button", { name: "12.2~12.5 보내기" }).first()).toBeVisible();
  } else if (form === "quote") {
    await expect(page.getByLabel("공사 기간", { exact: true })).toHaveValue("3");
  } else if (form === "dates") {
    await expect(page.getByLabel("인원", { exact: true })).toHaveValue("3");
  } else {
    await expect(page.getByLabel("항목", { exact: true })).toHaveValue("곰팡이 방지 처리");
  }
}

for (const scenario of scenarios) {
  test(`${scenario.form}: 전송 시 한 번 해석하고 선택한 뒤에만 실제 폼이 바뀐다`, async ({ page, request }) => {
    const assistant = await open(page, scenario.route);
    await expectOriginal(page, scenario.form);
    await assistant.getByRole("textbox", { name: "업체 도우미에게 보낼 말" }).fill(scenario.text);

    // 타이핑 직후뿐 아니라 지연 시간 동안에도 해석 요청이나 폼 저장이 없어야 한다.
    await page.waitForTimeout(900);
    expect(interpreted(await recorded(request))).toHaveLength(0);
    expect(writes(await recorded(request))).toHaveLength(0);
    await expectOriginal(page, scenario.form);

    await queueAnswer(request, { fields: scenario.fields });
    await assistant.getByRole("button", { name: "보내기", exact: true }).click();
    await expectInterpretCount(request, 1);
    const [called] = interpreted(await recorded(request));
    expect(called.body).toMatchObject({
      form: scenario.form, resource_id: scenario.resourceId, text: scenario.text,
    });
    expect(Array.isArray(called.body?.rule_fields)).toBe(true);
    await expect(assistant.getByRole("checkbox", { name: scenario.firstChoice })).toBeEnabled();
    await expect(assistant.locator('[aria-label="변경 후보"]')).toBeVisible();
    await expectOriginal(page, scenario.form);
    expect(writes(await recorded(request))).toHaveLength(0);

    await assistant.getByRole("button", { name: "폼에 적용" }).click();
    await expectApplied(page, scenario.form);
    expect(writes(await recorded(request))).toHaveLength(0);
    await expectInterpretCount(request, 1);
  });
}

test("Enter는 한 번 전송하고 Shift+Enter는 줄만 바꾼다", async ({ page, request }) => {
  const assistant = await open(page, `/vendor/requests/${VISITED}/quote`);
  const input = assistant.getByRole("textbox", { name: "업체 도우미에게 보낼 말" });
  await queueAnswer(request, { fields: [set("days", 3, "3일")] });
  await input.fill("공사 기간 3일로 바꿔 주세요");
  await input.press("Shift+Enter");
  expect(interpreted(await recorded(request))).toHaveLength(0);
  await input.press("Enter");
  await expectInterpretCount(request, 1);
  await expect(assistant.getByRole("checkbox", { name: "공사할 날 적용 선택" })).toBeEnabled();
  await expect(page.getByLabel("공사 기간", { exact: true })).toHaveValue("2");
  expect(writes(await recorded(request))).toHaveLength(0);
});

test("견적: 800ms가 지나도 후보만으로 PUT이 없고 부분 적용 후에만 자동 저장한다", async ({ page, request }) => {
  const assistant = await open(page, `/vendor/requests/${VISITED}/quote`);
  const amount = page.getByLabel("견적 금액", { exact: true });
  const days = page.getByLabel("공사 기간", { exact: true });
  await expect(amount).toHaveValue("2,970,000");
  await expect(days).toHaveValue("2");

  await queueAnswer(request, { fields: [
    set("amount", 4_550_000, "455만 원"),
    set("days", 3, "공사 3일"),
  ] });
  await send(page, "견적 455만 원으로 바꾸고 공사 3일로 고쳐 주세요");
  await expectInterpretCount(request, 1);
  await expect(assistant.getByRole("checkbox", { name: "금액 적용 선택" })).toBeEnabled();
  await expect(assistant.getByRole("checkbox", { name: "공사할 날 적용 선택" })).toBeChecked();
  await page.waitForTimeout(1_050);
  await expect(amount).toHaveValue("2,970,000");
  await expect(days).toHaveValue("2");
  expect((await recorded(request)).filter((entry) => entry.method === "PUT" && entry.path === QUOTE_PATH)).toHaveLength(0);

  await assistant.getByRole("checkbox", { name: "공사할 날 적용 선택" }).uncheck();
  await assistant.getByRole("button", { name: "폼에 적용" }).click();
  await expect(amount).toHaveValue("4,550,000");
  await expect(days).toHaveValue("2");
  await expect.poll(async () => (await recorded(request)).filter((entry) =>
    entry.method === "PUT" && entry.path === QUOTE_PATH).length).toBe(1);
  const [saved] = (await recorded(request)).filter((entry) => entry.method === "PUT" && entry.path === QUOTE_PATH);
  expect(saved.body).toMatchObject({ entered_amount: 4_550_000, days: 2, vat_mode: "excluded" });
  await expectInterpretCount(request, 1);
});

test("견적: 후보 대기 중 직접 고친 칸에는 오래된 값을 적용하지 않는다", async ({ page, request }) => {
  const assistant = await open(page, `/vendor/requests/${VISITED}/quote`);
  await queueAnswer(request, { fields: [set("days", 3, "3일")] });
  await send(page, "공사 기간을 3일로 바꿔 주세요");
  await expect(assistant.getByRole("checkbox", { name: "공사할 날 적용 선택" })).toBeEnabled();

  const days = page.getByLabel("공사 기간", { exact: true });
  await days.fill("4");
  await assistant.getByRole("button", { name: "폼에 적용" }).click();
  await expect(assistant.getByRole("alert")).toContainText("직접 고친 뒤 값이 달라졌어요");
  await expect(days).toHaveValue("4");
  expect(writes(await recorded(request))).toHaveLength(0);
});

test("견적: 명시적 clear는 자동 선택되지 않고 확인 후 해당 칸만 비운다", async ({ page, request }) => {
  const assistant = await open(page, `/vendor/requests/${VISITED}/quote`);
  const valid = page.getByLabel("유효기간", { exact: true });
  const days = page.getByLabel("공사 기간", { exact: true });
  await expect(valid).toHaveValue("2026-12-31");
  await queueAnswer(request, { fields: [clear("valid", "유효기간을 지워 주세요")] });
  await send(page, "유효기간을 지워 주세요");

  const selected = assistant.getByRole("checkbox", { name: "유효기간 적용 선택" });
  await expect(selected).toBeEnabled();
  await expect(selected).not.toBeChecked();
  await expect(valid).toHaveValue("2026-12-31");
  await selected.check();
  await assistant.getByRole("button", { name: "폼에 적용" }).click();
  await expect(valid).toHaveValue("");
  await expect(days).toHaveValue("2");
  expect(writes(await recorded(request))).toHaveLength(0);
});

test("취소한 늦은 응답은 다음 메시지의 후보와 폼 값을 덮어쓰지 못한다", async ({ page, request }) => {
  const assistant = await open(page, `/vendor/requests/${VISITED}/quote`);
  await queueAnswer(request, { fields: [set("amount", 4_550_000, "455만 원")], delay: 1_500 });
  await send(page, "견적 455만 원으로 바꿔 주세요");
  await expectInterpretCount(request, 1);
  await expect(assistant.getByRole("button", { name: "취소" })).toBeVisible();
  await assistant.getByRole("button", { name: "취소" }).click();
  await expect(assistant.locator('[aria-label="변경 후보"]')).toHaveCount(0);

  await queueAnswer(request, { fields: [set("days", 3, "3일")] });
  await send(page, "공사 3일로 고쳐 주세요");
  await expectInterpretCount(request, 2);
  await expect(assistant.getByRole("checkbox", { name: "공사할 날 적용 선택" })).toBeEnabled();
  await page.waitForTimeout(1_650); // 첫 요청의 지연 응답이 돌아온 뒤에도 두 번째 후보를 유지한다.
  await expect(assistant.getByRole("checkbox", { name: "공사할 날 적용 선택" })).toBeChecked();
  await expect(assistant.getByRole("checkbox", { name: "금액 적용 선택" })).toHaveCount(0);
  await expect(page.getByLabel("견적 금액", { exact: true })).toHaveValue("2,970,000");
  await expect(page.getByLabel("공사 기간", { exact: true })).toHaveValue("2");
  expect(writes(await recorded(request))).toHaveLength(0);
});

for (const status of [401, 403, 429]) {
  test(`HTTP ${status}: 인증·권한·제한 오류를 후보 성공으로 숨기지 않는다`, async ({ page, request }) => {
    const assistant = await open(page, `/vendor/requests/${VISITED}/quote`);
    const message = `시험 HTTP ${status} 오류`;
    await queueAnswer(request, { status, message });
    await send(page, "견적 455만 원으로 바꿔 주세요");
    await expectInterpretCount(request, 1);
    await expect(assistant.getByRole("alert")).toContainText(message);
    await expect(assistant.locator('[aria-label="변경 후보"]')).toHaveCount(0);
    await expect(page.getByLabel("견적 금액", { exact: true })).toHaveValue("2,970,000");
    expect(writes(await recorded(request))).toHaveLength(0);
  });
}

test("HTTP 503: 규칙 후보로 되돌아가도 적용 확인 전에는 저장하지 않는다", async ({ page, request }) => {
  const assistant = await open(page, `/vendor/requests/${VISITED}/quote`);
  await queueAnswer(request, { status: 503, message: "시험 모델 장애" });
  await send(page, "견적 455만 원으로 바꿔 주세요");
  await expectInterpretCount(request, 1);
  await expect(assistant.getByRole("status")).toContainText("규칙으로 읽은 후보만 보여 드려요");
  await expect(assistant.getByRole("checkbox", { name: "금액 적용 선택" })).toBeEnabled();
  await expect(page.getByLabel("견적 금액", { exact: true })).toHaveValue("2,970,000");
  expect(writes(await recorded(request))).toHaveLength(0);
});
