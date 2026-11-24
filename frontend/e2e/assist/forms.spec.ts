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
  origin_request_id?: string;
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

for (const form of ["quote", "extra"] as const) {
  test(`${form}: #176 수정의 10억 원 한도와 금액 절삭 방지를 유지한다`, async ({ page, request }) => {
    await open(page, form === "quote" ? `/vendor/requests/${VISITED}/quote` : `/vendor/jobs/${WORKING}?step=extra`);
    const amount = page.getByLabel(form === "quote" ? "견적 금액" : "금액", { exact: true });
    await amount.fill("10000000000");
    await expect(amount).toHaveValue("10,000,000,000");
    await expect(page.getByText("10억 원까지 넣을 수 있어요.", { exact: true })).toBeVisible();
    await page.waitForTimeout(950);
    expect(writes(await recorded(request))).toHaveLength(0);
    await amount.fill("1000000000");
    await expect(amount).toHaveValue("1,000,000,000");
    await expect(page.getByText("10억 원까지 넣을 수 있어요.", { exact: true })).toHaveCount(0);
    if (form === "quote") {
      await expect.poll(async () => writes(await recorded(request)).length).toBe(1);
      expect(writes(await recorded(request))[0].body?.entered_amount).toBe(1_000_000_000);
    }
  });
}

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

test("복합 금액은 전체 원 단위로 확인한 뒤에만 견적서에 저장해요", async ({ page, request }) => {
  const box = await open(page, `/vendor/requests/${VISITED}/quote`);
  const amount = page.getByLabel("견적 금액", { exact: true });
  for (const [text, expected] of [["견적 1천500만 원, 부가세 별도", 15_000_000], ["견적 2천300만 원, 부가세 별도", 23_000_000]] as const) {
    const before = writes(await recorded(request)).length;
    await send(page, text);
    await expect(box.getByRole("checkbox", { name: "금액 적용 선택" })).toBeEnabled();
    await page.getByRole("button", { name: "임시 저장", exact: true }).click();
    expect(writes(await recorded(request))).toHaveLength(before);
    await box.getByRole("button", { name: "폼에 적용" }).click();
    await expect(amount).toHaveValue(expected.toLocaleString("ko-KR"));
    await expect.poll(async () => writes(await recorded(request)).slice(before).map((entry) => [
      entry.method, entry.path, entry.body?.entered_amount,
    ])).toEqual([["PUT", QUOTE_PATH, expected]]);
  }
});

test("예약된 견적 자동 저장을 멈추고 같은 금액을 다시 확인하면 한 번 저장해요", async ({ page, request }) => {
  await open(page, `/vendor/requests/${VISITED}/quote`);
  const amount = page.getByLabel("견적 금액", { exact: true });
  await page.clock.install();
  await amount.fill("6000000");
  await send(page, "견적 천만, 부가세 별도");
  await page.clock.fastForward(1000);
  expect(writes(await recorded(request))).toHaveLength(0);
  await amount.fill("600000");
  await amount.fill("6000000");
  await page.clock.fastForward(1000);
  await expect.poll(async () => writes(await recorded(request)).map((w) => w.body?.entered_amount)).toEqual([6_000_000]);
});

for (const form of ["quote", "extra"] as const) {
  for (const phrase of ["견적 천만, 부가세 별도", "견적 천만으로, 부가세 별도", "견적 이천만으로, 부가세 별도"]) {
    test(`${form}: ${phrase} 차단 후 금액만 고치면 정확한 값으로 한 번 발송해요`, async ({ page, request }) => {
      await open(page, form === "quote" ? `/vendor/requests/${VISITED}/quote` : `/vendor/jobs/${WORKING}?step=extra`);
      const amount = page.getByLabel(form === "quote" ? "견적 금액" : "금액", { exact: true });
      if (form === "extra") {
        await page.getByLabel("항목", { exact: true }).fill("추가 보강");
        await page.getByLabel("이유", { exact: true }).fill("현장 요청");
        await page.getByRole("radio", { name: "포함", exact: true }).check();
      } else {
        // 실제 첨부가 존재하는 견적 초안을 열고 날짜 유효성도 별도로 충족한다.
        await expect(page.getByText("마루와벽_상계동_견적서.pdf", { exact: true })).toBeVisible();
        await page.getByLabel("유효기간", { exact: true }).fill("2028-12-31");
      }
      await page.clock.install();
      await amount.fill("1000000");
      if (phrase.includes("으로")) {
        await queueAnswer(request, { fields: [set("amount", 100_000, "천만"), set("vat", "excluded", "별도")] });
      } else {
        await queueAnswer(request, { status: 503, message: "시험용 연결 실패" });
      }
      await send(page, phrase);
      await expect(page.getByText(/말씀하신 금액을 확인하지 못했어요/).first()).toBeVisible();
      await expect(page.getByRole("button", { name: "폼에 적용" })).toBeEnabled();
      await page.getByRole("button", { name: "폼에 적용" }).click();
      await expect(amount).toHaveValue("1,000,000");
      if (form === "quote") {
        await page.getByLabel("공사 기간", { exact: true }).fill("3");
        await page.getByRole("button", { name: "임시 저장", exact: true }).click();
      } else {
        await page.getByLabel("이유", { exact: true }).fill("사유만 직접 고침");
      }
      await page.getByRole("button", { name: "고객에게 보내기", exact: true }).click();
      await page.clock.fastForward(1200);
      expect(writes(await recorded(request))).toHaveLength(0);
      await amount.fill("1500000");
      await page.getByRole("button", { name: "고객에게 보내기", exact: true }).click();
      const postPath = form === "quote" ? QUOTE_PATH + "/send" : `/vendor/jobs/${WORKING}/extras`;
      await expect.poll(async () => writes(await recorded(request)).filter((r) => r.method === "POST" && r.path === postPath).length).toBe(1);
      const saved = writes(await recorded(request));
      if (form === "quote") {
        expect(saved.map((r) => [r.method, r.path])).toEqual([["PUT", QUOTE_PATH], ["POST", postPath]]);
        expect(saved[0].body?.file_ids).toHaveLength(1);
      } else {
        expect(saved).toHaveLength(1);
        expect(saved[0].body?.reason).toBe("사유만 직접 고침");
      }
      expect(saved[0].body).toMatchObject({ entered_amount: 1_500_000, vat_mode: "excluded" });
    });
  }
}

for (const phrase of ["12명", "10명", "2~3명", "12인", "2~3인", "2〜3명", "두세 인"]) {
  test(`인원 ${phrase}: 기존값 발송을 막고 직접 고른 3명으로만 보내요`, async ({ page, request }) => {
    const box = await open(page, `/vendor/jobs/${DATES}`);
    // 두 날짜는 주말·공휴일을 피하고 서버가 허용하는 오늘 이후 365일 안에 둔다.
    const day = new Date();
    day.setUTCDate(8);
    day.setUTCMonth(day.getUTCMonth() + 10);
    while (day.getUTCDay() !== 1) day.setUTCDate(day.getUTCDate() + 1);
    const first = day.toISOString().slice(0, 10);
    day.setUTCDate(day.getUTCDate() + 1);
    const days = [first, day.toISOString().slice(0, 10)];
    const range = `${day.getUTCFullYear()}년 ${day.getUTCMonth() + 1}월 ${day.getUTCDate() - 1}일부터 ${day.getUTCDate()}일까지`;
    if (phrase === "2〜3명" || phrase === "두세 인") {
      // 서버에 연결할 수 없어도 원문의 연도와 인원 범위를 보존해야 한다.
      await queueAnswer(request, { status: 503, message: "시험용 연결 실패" });
    } else {
      await queueAnswer(request, { fields: [set("days", days, range), set("crew", 2, phrase)] });
    }
    await send(page, range + ", " + phrase);
    await expect(page.getByText(/말씀하신 인원을 확정할 수 없어요/).first()).toBeVisible();
    await box.getByRole("button", { name: "폼에 적용" }).click();
    await page.getByRole("button", { name: "고객에게 보내기", exact: true }).click();
    expect(writes(await recorded(request))).toHaveLength(0);
    await page.getByLabel("인원", { exact: true }).selectOption("3");
    await page.getByRole("button", { name: "고객에게 보내기", exact: true }).click();
    await expect.poll(async () => writes(await recorded(request)).length).toBe(1);
    expect(writes(await recorded(request))[0]).toMatchObject({
      method: "POST", path: `/vendor/jobs/${DATES}/dates`, body: { days, crew: 3 },
    });
  });
}

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

test("견적 origin baseline: 연속 3회 전송에서 최초 금액·기간 기준값과 출처를 그대로 전달한다", async ({ page, request }) => {
  const assistant = await open(page, `/vendor/requests/${VISITED}/quote`);
  const amount = page.getByLabel("견적 금액", { exact: true });
  const days = page.getByLabel("공사 기간", { exact: true });
  const valid = page.getByLabel("유효기간", { exact: true });
  await expect(amount).toHaveValue("2,970,000");
  await expect(days).toHaveValue("2");

  const firstText = "견적은 455만 원으로 변경해 주세요";
  await queueAnswer(request, { fields: [set("amount", 4_550_000, "455만 원")] });
  await send(page, firstText);
  await expectInterpretCount(request, 1);
  await expect(assistant.getByRole("checkbox", { name: "금액 적용 선택" })).toBeChecked();
  const firstCall = interpreted(await recorded(request))[0];
  const firstId = firstCall.body?.request_id;
  expect(typeof firstId).toBe("string");
  expect(firstCall.body?.previous_candidate).toBeNull();

  const secondText = "공사 기간을 3일로 바꿔 주세요";
  await queueAnswer(request, { fields: [
    { ...set("amount", 4_550_000, "455만 원"), origin_request_id: String(firstId) },
    set("days", 3, "공사 기간을 3일"),
  ] });
  await send(page, secondText);
  await expectInterpretCount(request, 2);
  await expect(assistant.getByRole("checkbox", { name: "공사할 날 적용 선택" })).toBeChecked();
  const secondCall = interpreted(await recorded(request))[1];
  const secondId = secondCall.body?.request_id;
  expect(typeof secondId).toBe("string");
  expect(secondCall.body?.current).toMatchObject({ amount: 2_970_000, days: 2 });
  expect(secondCall.body?.previous_candidate).toMatchObject({
    request_id: firstId,
    text: firstText,
    fields: [{ field: "amount", action: "set", value: 4_550_000, evidence: "455만 원" }],
    baselines: { amount: 2_970_000 },
    sources: { amount: "llm" },
    origins: { amount: firstId },
  });

  await queueAnswer(request, { fields: [
    { ...set("amount", 4_550_000, "455만 원"), origin_request_id: String(firstId) },
    { ...set("days", 3, "공사 기간을 3일"), origin_request_id: String(secondId) },
    set("valid", "2027-12-31", "2027-12-31까지"),
  ] });
  await send(page, "유효기간 2027-12-31까지로 해 주세요");
  await expectInterpretCount(request, 3);
  await expect(assistant.getByRole("checkbox", { name: "유효기간 적용 선택" })).toBeChecked();
  const thirdCall = interpreted(await recorded(request))[2];
  expect(thirdCall.body?.previous_candidate).toMatchObject({
    request_id: secondId,
    text: `${firstText}\n${secondText}`,
    fields: [
      { field: "amount", action: "set", value: 4_550_000, evidence: "455만 원" },
      { field: "days", action: "set", value: 3, evidence: "공사 기간을 3일" },
    ],
    baselines: { amount: 2_970_000, days: 2 },
    sources: { amount: "llm", days: "llm" },
    origins: { amount: firstId, days: secondId },
  });
  expect(writes(await recorded(request))).toHaveLength(0);
  await expect(amount).toHaveValue("2,970,000");
  await expect(days).toHaveValue("2");

  await assistant.getByRole("button", { name: "폼에 적용" }).click();
  await expect(amount).toHaveValue("4,550,000");
  await expect(days).toHaveValue("3");
  await expect(valid).toHaveValue("2027-12-31");
  await expect.poll(async () => (await recorded(request)).filter((entry) =>
    entry.method === "PUT" && entry.path === QUOTE_PATH).length).toBe(1);
  expect((await recorded(request)).some((entry) => entry.path === `${QUOTE_PATH}/send`)).toBe(false);
});

test("견적 stale baseline: 금액 수동 변경 뒤 다음 전송은 옛 금액 후보를 제외하고 기간만 적용한다", async ({ page, request }) => {
  const assistant = await open(page, `/vendor/requests/${VISITED}/quote`);
  const amount = page.getByLabel("견적 금액", { exact: true });
  const days = page.getByLabel("공사 기간", { exact: true });
  await expect(amount).toHaveValue("2,970,000");
  await expect(days).toHaveValue("2");

  await queueAnswer(request, { fields: [set("amount", 4_550_000, "455만 원")] });
  await send(page, "견적은 455만 원으로 고쳐 주세요");
  await expectInterpretCount(request, 1);
  await expect(assistant.getByRole("checkbox", { name: "금액 적용 선택" })).toBeChecked();
  expect(writes(await recorded(request))).toHaveLength(0);

  await amount.fill("6000000");
  await expect(amount).toHaveValue("6,000,000");
  // 업체가 직접 수정한 값의 정상적인 자동 임시 저장은 허용해요.
  await expect.poll(async () => (await recorded(request)).filter((entry) =>
    entry.method === "PUT" && entry.path === QUOTE_PATH && entry.body?.entered_amount === 6_000_000).length).toBe(1);

  await queueAnswer(request, { fields: [set("days", 3, "공사 기간 3일")] });
  await send(page, "공사 기간만 3일로 바꿔 주세요");
  await expectInterpretCount(request, 2);
  const secondCall = interpreted(await recorded(request))[1];
  expect(secondCall.body?.current).toMatchObject({ amount: 6_000_000, days: 2 });
  expect(secondCall.body?.previous_candidate).toBeNull();
  expect(JSON.stringify(secondCall.body)).not.toContain("4550000");

  const preview = assistant.locator('[aria-label="변경 후보"]');
  await expect(preview).toBeVisible();
  await expect(assistant.getByRole("checkbox", { name: "금액 적용 선택" })).toHaveCount(0);
  await expect(preview).not.toContainText("4,550,000");
  await expect(assistant.getByRole("checkbox", { name: "공사할 날 적용 선택" })).toBeChecked();
  await expect(amount).toHaveValue("6,000,000");
  await expect(days).toHaveValue("2");

  await assistant.getByRole("button", { name: "폼에 적용" }).click();
  await expect(amount).toHaveValue("6,000,000");
  await expect(days).toHaveValue("3");
  const all = await recorded(request);
  expect(all.filter((entry) => entry.method === "PUT" && entry.path === QUOTE_PATH)
    .every((entry) => entry.body?.entered_amount === 6_000_000)).toBe(true);
  expect(all.some((entry) => entry.path === `${QUOTE_PATH}/send`)).toBe(false);
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

for (const status of [401, 403, 404, 409, 422, 429]) {
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
  await expect(assistant.getByRole("status")).toContainText("규칙으로 읽은 이번 메시지의 후보만 보여 드려요");
  await expect(assistant.getByRole("checkbox", { name: "금액 적용 선택" })).toBeEnabled();
  await expect(page.getByLabel("견적 금액", { exact: true })).toHaveValue("2,970,000");
  expect(writes(await recorded(request))).toHaveLength(0);
});
