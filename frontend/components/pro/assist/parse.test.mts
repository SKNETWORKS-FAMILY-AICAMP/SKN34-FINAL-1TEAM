import { amountToConfirm, assistInputIssues } from "./guard.ts";
// 업체 도우미 말 해석 시험: pnpm test:unit (node --test). 애매하면 칸을 채우지 않는지까지 봐요.
import assert from "node:assert/strict";
import { test } from "node:test";
import { SAID_AMOUNT, parseDates, parseExtra, parsePeriod, parseQuote, readCrew } from "./parse.ts";

const T = "2026-11-23";

test("금액: 소수 만 단위 · 금액 여럿이면 전체 금액 하나만", () => {
  assert.equal(parseQuote("455.5만 원", T).amount, 4_555_000);
  assert.equal(parseQuote("철거 20만 원 별도 전체 견적 455만 원", T).amount, 4_550_000);
  assert.equal(parseQuote("철거 20만 원, 폐기물 10만 원", T).amount, undefined); // 고를 수 없음
  assert.equal(parseQuote("견적 455만 5천 원, 공사 3일", T).amount, 4_555_000);
  assert.equal(parseQuote("4,550,000원", T).amount, 4_550_000);
});

test("부가세: 부정한 말은 버리고 하나일 때만", () => {
  assert.equal(parseQuote("면세는 아니고 부가세 포함", T).vat, "included");
  assert.equal(parseQuote("면세 업체예요", T).vat, "exempt");
  assert.equal(parseQuote("부가세 포함, 철거는 별도", T).vat, "included");
  assert.equal(parseQuote("부가세 포함인데 부가세 별도일 수도", T).vat, undefined); // 서로 다름
});

test("실측 기간: 범위 표시가 있을 때만 기간", () => {
  assert.equal(parsePeriod("12월 2일과 12월 5일만 가능", T), null);
  assert.deepEqual(parsePeriod("12/2부터 12/5까지 돼요, 12/4는 안 돼요", T), { start: "2026-12-02", end: "2026-12-05", excluded: ["2026-12-04"], outside: [] });
  assert.deepEqual(parsePeriod("12/3 돼요", T), { start: "2026-12-03", end: "2026-12-03", excluded: [], outside: [] });
});

test("공사 날짜 · 추가 금액", () => {
  assert.deepEqual(parseDates("12/8부터 12/11까지 되는데 12/9는 안 돼요, 3명", T, null, ["2026-12-10"]), { days: ["2026-12-08", "2026-12-11"], crew: 3 });
  assert.deepEqual(parseDates("12월 2일과 12월 5일만 가능", T), { days: ["2026-12-02", "2026-12-05"], crew: undefined });
  // 주말에 공사하지 않는 집: 12/4(금) ~ 12/8(화)에서 토 · 일을 뺀다
  assert.deepEqual(parseDates("12/4부터 12/8까지, 2명", T, null, [], true), { days: ["2026-12-04", "2026-12-07", "2026-12-08"], crew: 2 });
  const x = parseExtra("곰팡이 방지 처리 12만 원, 부가세 포함, 벽 안쪽까지 곰팡이가 퍼져서", T);
  assert.deepEqual(x, { amount: 120_000, vat: "included", title: "곰팡이 방지 처리", reason: "벽 안쪽까지 곰팡이가 퍼져서" });
});

test("날짜 축약 목록은 월을 상속하되 목록을 임의의 기간으로 확장하지 않아요", () => {
  const expected = ["2026-12-11", "2026-12-12", "2026-12-13"];
  assert.deepEqual(parseDates("12/11,12,13일", T, "2026-11-01"), { days: expected, crew: undefined });
  assert.deepEqual(parseDates("12월11,12,13일", T, "2026-11-01"), { days: expected, crew: undefined });
  assert.deepEqual(parseDates("12/11,13일", T, "2026-11-01"), { days: ["2026-12-11", "2026-12-13"], crew: undefined });
  assert.equal(parseDates("12/11,32,13일", T, "2026-11-01"), null);
  assert.equal(parseDates("2/30,31,1일", T, "2026-11-01"), null);
  assert.deepEqual(parseDates("12월7일부터9일까지, 네 명", T, "2026-11-01"), {
    days: ["2026-12-07", "2026-12-08", "2026-12-09"], crew: 4,
  });
  assert.deepEqual(parsePeriod("12월7일부터9일까지", T, "2026-11-01"), {
    start: "2026-12-07", end: "2026-12-09", excluded: [], outside: [],
  });
  assert.deepEqual(parsePeriod("12월7일~9일", T, "2026-11-01"), {
    start: "2026-12-07", end: "2026-12-09", excluded: [], outside: [],
  });
  assert.deepEqual(parsePeriod("12/7~9일", T, "2026-11-01"), {
    start: "2026-12-07", end: "2026-12-09", excluded: [], outside: [],
  });
});

test("한국어 인원 수량만 읽고 응답·애매한 인원 범위를 제외해요", () => {
  for (const [word, crew] of [["한", 1], ["두", 2], ["세", 3], ["네", 4]] as const) {
    assert.deepEqual(parseDates(`작업자 ${word} 명`, T), { days: [], crew });
  }
  assert.equal(parseDates("네, 일정은 그대로", T), null);
  assert.equal(parseDates("두세 명", T), null);
  assert.equal(parseDates("두 세 명", T), null);
  assert.equal(parseDates("두~세 명", T), null);
  assert.equal(parseDates("104명", T), null);
  assert.equal(parseDates("123명", T), null);
  assert.deepEqual(parseDates("12/8부터 12/10까지, 두세 명", T), {
    days: ["2026-12-08", "2026-12-09", "2026-12-10"], crew: undefined,
  });
  assert.deepEqual(parseDates("12/8부터 12/10까지, 2~3명", T), {
    days: ["2026-12-08", "2026-12-09", "2026-12-10"], crew: undefined,
  });
  assert.deepEqual(parseDates("12/8, 12/9, 2명~3명", T), {
    days: ["2026-12-08", "2026-12-09"], crew: undefined,
  });
  assert.deepEqual(parseDates("12/8, 12/9, 104명", T), {
    days: ["2026-12-08", "2026-12-09"], crew: undefined,
  });
});

test("리뷰 회귀: 부정 · 정정 표현", () => {
  assert.equal(parseQuote("부가세 포함 아닙니다", T).vat, undefined);
  assert.equal(parseQuote("부가세 포함 아닙니다, 부가세 별도예요", T).vat, "excluded");
  assert.deepEqual(parsePeriod("12/2부터 12/5까지 됩니다, 12/4는 안 됩니다", T), { start: "2026-12-02", end: "2026-12-05", excluded: ["2026-12-04"], outside: [] });
  assert.equal(parseQuote("견적 455만 원이 아니라 555만 원", T).amount, 5_550_000);
  assert.equal(parseQuote("455만 말고 555만 원이요", T).amount, 5_550_000);
  assert.equal(parseQuote("455만 원이 아니에요", T).amount, undefined); // 고친 값이 없으면 비워요
});

test("리뷰 회귀: 기간 'N일간'은 날짜가 아니에요", () => {
  assert.deepEqual(parseDates("12/8부터 3일간 2명", T), { days: [], crew: 2 }); // 끝 날을 계산하지 않아요
  assert.equal(parsePeriod("12/2부터 5일간", T), null);
  assert.deepEqual(parseDates("12/8부터 3일, 2명", T), { days: [], crew: 2 }); // 3일이 12/3은 아니에요
  assert.deepEqual(parseDates("12/8부터 12/10까지, 2~3명", T), { days: ["2026-12-08", "2026-12-09", "2026-12-10"], crew: undefined });
});

test("리뷰 회귀: 억 · 조건부 부가세", () => {
  assert.equal(parseQuote("견적 1억 500만 원", T).amount, undefined);
  assert.equal(parseQuote("1억 2000만 원", T).amount, undefined);
  const c = parseQuote("455만 원 (부가세 별도 시 415만 원)", T);
  assert.equal(c.vat, undefined);
  assert.equal(c.amount, undefined);
});

test("리뷰 회귀: 부가세 포함을 부정하는 말", () => {
  for (const t of ["부가세 포함 안 됩니다", "부가세 포함 안 해요", "부가세 포함 안 했어요", "부가세 포함 않습니다", "부가세 포함 X", "부가세 포함(X)"]) {
    assert.equal(parseQuote(t, T).vat, undefined, t);
  }
  assert.equal(parseQuote("부가세 포함 안 돼요, 별도예요", T).vat, "excluded");
});

test("리뷰 회귀: 유효기간은 '유효'에 붙은 날짜만", () => {
  assert.equal(parseQuote("12/11까지 공사하고 유효기간은 일주일", T).valid, undefined);
  assert.equal(parseQuote("12/11까지 공사, 견적은 유효기간 없이", T).valid, undefined);
  assert.equal(parseQuote("12/11까지 공사, 12/17까지 유효", T).valid, "2026-12-17");
  assert.deepEqual(parseQuote("견적 455만 원, 부가세 포함, 공사 3일, 12/17까지 유효", T), { amount: 4_550_000, vat: "included", days: 3, valid: "2026-12-17" });
  assert.equal(parseQuote("유효기간은 12/17까지", T).valid, "2026-12-17");
});

test("리뷰 회귀: 안 되는 날 · 괄호 · 요일", () => {
  assert.deepEqual(parsePeriod("12/2부터 12/5까지 돼요, 12/4는 못 갑니다", T), { start: "2026-12-02", end: "2026-12-05", excluded: ["2026-12-04"], outside: [] });
  assert.deepEqual(parsePeriod("12/2부터 12/5까지 돼요, 12/4는 안 될 수도", T), { start: "2026-12-02", end: "2026-12-05", excluded: ["2026-12-04"], outside: [] });
  const both = { start: "2026-12-02", end: "2026-12-05", excluded: ["2026-12-03", "2026-12-04"], outside: [] };
  assert.deepEqual(parsePeriod("12/2 ~ 12/5 (12/3, 12/4 불가)", T), both);
  assert.deepEqual(parsePeriod("12/2 ~ 12/5 (12/3, 12/4 안 돼요)", T), both);
  assert.equal(parsePeriod("12/2, 12/3 빼고 12/5까지", T), null);
  assert.equal(parsePeriod("12/2부터 12/5까지 돼요, 수요일은 안 돼요", T), null);
});

test("리뷰 회귀: 정정하면 비워요", () => {
  assert.equal(parsePeriod("12/2부터 12/5까지 → 12/3부터 12/6까지로 정정", T), null);
  assert.equal(parsePeriod("12/2부터 12/5까지 돼요. 아니 12/3부터요", T), null);
  assert.equal(parseDates("12/2부터 12/5까지 → 12/3부터 12/6까지로 정정", T), null);
  // 공사 기간은 고친 뒤 값을 써요(#174와 같은 규칙). 어느 값인지 모르면 비워요
  assert.equal(parseQuote("공사 3일, 아니 4일", T).days, 4);
  assert.equal(parseQuote("공사 3일 아니고 4일", T).days, 4);
  for (const t of ["공사 3일에서 4일", "3일 정도 걸리는데 주말 끼면 5일", "공사 3일 걸리고 건조 2일"]) {
    assert.equal(parseQuote(t, T).days, undefined, t);
  }
  assert.equal(parseQuote("공사 3일, 12월 17일까지 유효", T).days, 3);
});

test("리뷰 회귀: 추가 금액 항목 이름", () => {
  assert.equal(parseExtra("작은방 천장 2평 추가", T).title, "작은방 천장 2평 추가");
  assert.equal(parseExtra("석고보드 3장 교체, 5만 원", T).title, "석고보드 3장 교체");
  const x = parseExtra("곰팡이 방지 12만 원이 아니라 15만 원", T);
  assert.equal(x.title, "곰팡이 방지");
  assert.equal(x.amount, 150_000);
});

test("#179 추가금: 명시적 제목 대상을 사유·금액과 분리해 읽어요", () => {
  assert.deepEqual(parseExtra("사유 말고 공사 제목을 몰딩 재시공으로 바꿔 주세요.", T), {
    title: "몰딩 재시공",
  });
  assert.deepEqual(parseExtra("제목은 문틀 보강, 금액은 30만 원으로 변경해 주세요.", T), {
    title: "문틀 보강", amount: 300_000,
  });
  assert.equal(parseExtra("추가 항목은 곰팡이 방지 처리로 적어 주세요.", T).title, "곰팡이 방지 처리");
  assert.equal(parseExtra("공사 제목: 누수 보수", T).title, "누수 보수");
  assert.equal(parseExtra("작업명은 현관 몰딩 교체", T).title, "현관 몰딩 교체");
  assert.equal(parseExtra("제목은 문틀 보강, 벽 안쪽까지 곰팡이가 퍼져서", T).reason,
    "벽 안쪽까지 곰팡이가 퍼져서");
});

test("#179 추가금: 명확하지 않은 제목과 다른 필드의 지시를 제목으로 사용하지 않아요", () => {
  for (const text of [
    "제목은 문틀 보강?",
    "제목은 문틀 보강으로 바꿔 주세요. 잠깐, 제목 변경은 취소할게요.",
    "제목은 문틀 보강으로 바꿀까요?",
    "제목은 문틀 보강 또는 몰딩 보수",
    "제목은 문틀 보강으로 바꿀까 고민 중",
    "제목은 문틀 보강으로 바꾸면 될 것 같아요",
    "제목은 문틀 보강으로 변경할 예정입니다",
    "제목은 문틀 보강으로 변경한 게 아닙니다.",
    "제목을 몰딩 재시공으로 바꾸지 마세요.",
    "제목은 그대로 유지해 주세요.",
    "제목은 누수 보수, 제목은 몰딩 재시공",
    "제목은 누수 보수 말고 몰딩 재시공",
    "추가금 사유만 지워 주세요.",
    "추가금 사유는 지우지 말아 주세요.",
    "사유 칸에는 기존 벽체 손상이라고 적어 주세요.",
    "금액은 30만 원으로 변경해 주세요.",
  ]) {
    assert.equal(parseExtra(text, T).title, undefined, text);
  }
  assert.equal(parseExtra("금액은 30만 원으로 변경해 주세요.", T).amount, 300_000);
  assert.equal(parseExtra("벽 안쪽까지 곰팡이가 퍼져서", T).reason, "벽 안쪽까지 곰팡이가 퍼져서");
  assert.equal(parseExtra("벽 안쪽까지 곰팡이가 퍼져서", T).title, undefined);
});

test("이전 제목을 가리키는 말은 새 제목으로 쓰지 않아요", () => {
  for (const text of [
    "제목은 방금 얘기한 걸로 가고요, 금액은 25만4천원입니다.",
    "항목은 아까 말씀드린 걸로 해주세요.",
    "제목은 좀 전에 정한 내용으로 진행해 주세요.",
    "방금 얘기한 걸로 가고요, 금액은 25만4천원입니다.",
  ]) {
    assert.equal(parseExtra(text, T).title, undefined, text);
  }
  assert.equal(parseExtra("제목은 문선 보강으로 해주세요.", T).title, "문선 보강");
  assert.equal(parseExtra("제목은 방금 얘기한 걸로 가고요, 금액은 25만4천원입니다.", T).amount, 254000);
});

// #174(dev)에서 온 시험 문장
test("#174 금액: 소수 만 단위 · 금액 여럿이면 전체 금액 하나만", () => {
  assert.equal(parseQuote("455.5만 원", T).amount, 4_555_000);
  assert.equal(parseQuote("철거 20만 원 별도 전체 견적 455만 원", T).amount, 4_550_000);
  assert.equal(parseQuote("철거 20만 원, 폐기물 10만 원", T).amount, undefined); // 고를 수 없음
  assert.equal(parseQuote("견적 455만 5천 원, 공사 3일", T).amount, 4_555_000);
  assert.equal(parseQuote("4,550,000원", T).amount, 4_550_000);
});

test("#174 부가세: 부정한 말은 버리고 하나일 때만", () => {
  assert.equal(parseQuote("면세는 아니고 부가세 포함", T).vat, "included");
  assert.equal(parseQuote("면세 업체예요", T).vat, "exempt");
  assert.equal(parseQuote("부가세 포함, 철거는 별도", T).vat, "included");
  assert.equal(parseQuote("부가세 포함인데 부가세 별도일 수도", T).vat, undefined); // 서로 다름
  // QA: '아닙니다'도 부정, 부정만 있고 남은 방식이 없으면 비워요
  assert.equal(parseQuote("부가세 포함 아닙니다", T).vat, undefined);
  assert.equal(parseQuote("면세는 아닙니다", T).vat, undefined);
  assert.equal(parseQuote("부가세 별도 아닙니다", T).vat, undefined);
  assert.equal(parseQuote("부가세 포함된 금액이 아닙니다", T).vat, undefined);
  assert.equal(parseQuote("면세는 아닙니다, 부가세 별도예요", T).vat, "excluded");
  // 리뷰: '안 합니다 · 하지 않아요'도 부정
  assert.equal(parseQuote("부가세 포함 안 합니다", T).vat, undefined);
  assert.equal(parseQuote("부가세 별도 안 합니다", T).vat, undefined);
  assert.equal(parseQuote("면세 안 합니다", T).vat, undefined);
  assert.equal(parseQuote("부가세 포함 안 해요", T).vat, undefined);
  assert.equal(parseQuote("부가세 포함하지 않아요", T).vat, undefined);
  assert.equal(parseQuote("면세 안 합니다, 부가세 포함이에요", T).vat, "included");
});

test("#174 정정: 고친 뒤 값을 쓰고 앞의 틀린 값은 넣지 않아요", () => {
  assert.equal(parseQuote("견적 455만 원이 아니라 555만 원입니다", T).amount, 5_550_000);
  assert.equal(parseQuote("공사 3일 아니고 5일 걸려요", T).days, 5);
  assert.equal(parseQuote("유효기간 12/17까지 아니고 12/20까지", T).valid, "2026-12-20");
  // 고친 값 없이 부정만 하면 비워요
  assert.equal(parseQuote("견적 455만 원은 아니에요", T).amount, undefined);
  assert.equal(parseQuote("공사 3일은 아닙니다", T).days, undefined);
  assert.equal(parseQuote("12/17까지 유효는 아니에요", T).valid, "2026-12-17"); // 부정이 날짜 바로 뒤가 아니면 그대로
});

test("#174 변경 표현: 바꾼 값을 쓰고, 못 고르면 비워요", () => {
  assert.equal(parseQuote("견적 455만 원에서 555만 원으로 변경", T).amount, 5_550_000);
  assert.equal(parseQuote("견적은 455만 원, 아니 555만 원", T).amount, 5_550_000);
  assert.equal(parseQuote("견적 455만 원 → 555만 원", T).amount, 5_550_000);
  assert.equal(parseQuote("공사 3일에서 5일로 변경했어요", T).days, 5);
  assert.equal(parseQuote("공사 3일, 아니 5일 걸려요", T).days, 5);
  // 바꾸는 말은 있는데 어느 값인지 모르면 앞 값을 넣지 않아요
  assert.equal(parseQuote("견적 455만 원에서 555만 원 사이로 변경될 수 있어요", T).amount, undefined);
  assert.equal(parseQuote("견적 455만 원 변경해서 555만 원", T).amount, undefined);
  assert.equal(parseQuote("공사 3일인데 변경되면 5일", T).days, undefined);
});

test("#174 실측 기간: 범위 표시가 있을 때만 기간", () => {
  assert.equal(parsePeriod("12월 2일과 12월 5일만 가능", T), null);
  assert.deepEqual(parsePeriod("12/2부터 12/5까지 돼요, 12/4는 안 돼요", T), { start: "2026-12-02", end: "2026-12-05", excluded: ["2026-12-04"], outside: [] });
  assert.deepEqual(parsePeriod("12/3 돼요", T), { start: "2026-12-03", end: "2026-12-03", excluded: [], outside: [] });
});

test("#174 실측 기간: '안 됩니다 · 불가 · 어렵습니다'도 빼는 날", () => {
  const want = { start: "2026-12-02", end: "2026-12-05", excluded: ["2026-12-04"], outside: [] };
  assert.deepEqual(parsePeriod("12/2부터 12/5까지 됩니다, 12/4는 안 됩니다", T), want);
  assert.deepEqual(parsePeriod("12/2부터 12/5까지 가능합니다, 12/4는 불가합니다", T), want);
  assert.deepEqual(parsePeriod("12/2부터 12/5까지 됩니다, 12/4는 어렵습니다", T), want);
  assert.deepEqual(parsePeriod("12/2부터 12/5까지 됩니다, 12/4는 힘들어요", T), want);
  assert.deepEqual(parsePeriod("12/2부터 12/5까지 됩니다, 12/4는 못 갑니다", T), want);
  assert.deepEqual(parsePeriod("12/2부터 12/5까지 됩니다, 12/4는 쉽니다", T), want);
});

test("#174 실측 기간: 안 된다고 한 날은 첫날 · 끝날 · 늘어놓은 날이어도 빼요", () => {
  assert.deepEqual(parsePeriod("12/2부터 12/5까지 가능합니다, 12/2는 안 돼요", T), { start: "2026-12-03", end: "2026-12-05", excluded: [], outside: [] });
  assert.deepEqual(parsePeriod("12/2부터 12/5까지 가능합니다, 12/5는 안 돼요", T), { start: "2026-12-02", end: "2026-12-04", excluded: [], outside: [] });
  assert.deepEqual(parsePeriod("12/2부터 12/5까지 가능합니다, 12/4, 12/3은 안 돼요", T), { start: "2026-12-02", end: "2026-12-05", excluded: ["2026-12-03", "2026-12-04"], outside: [] });
  assert.deepEqual(parsePeriod("12/2부터 12/5까지 가능합니다, 12/2, 12/3은 안 돼요", T), { start: "2026-12-04", end: "2026-12-05", excluded: [], outside: [] });
  assert.equal(parsePeriod("12/2부터 12/3까지 가능합니다, 12/2, 12/3은 안 돼요", T), null); // 남는 날이 없으면 비움
  assert.deepEqual(parsePeriod("12/2, 12/3 돼요", T), null); // 범위 표시 없이 늘어놓은 날은 그대로 비움
});

test("#174 실측 기간: 이중 부정('어렵지 않아요')은 빼지 않아요", () => {
  const all = { start: "2026-12-02", end: "2026-12-05", excluded: [], outside: [] };
  assert.deepEqual(parsePeriod("12/2부터 12/5까지 됩니다, 12/4도 어렵지 않아요", T), all);
  assert.deepEqual(parsePeriod("12/2부터 12/5까지 됩니다, 12/4는 안 되지는 않아요", T), all);
  assert.deepEqual(parsePeriod("12/2부터 12/5까지 됩니다, 12/4는 힘들지 않습니다", T), all);
});

test("리뷰 회귀: 천 · 백을 섞어 쓴 만 단위 금액, 못 읽은 금액은 '말했다'로 알아봐요", () => {
  assert.equal(parseQuote("견적 1천만 원, 부가세 별도", T).amount, 10_000_000);
  assert.equal(parseQuote("견적 3백만 원", T).amount, 3_000_000);
  assert.equal(parseQuote("견적 1천5백만 원", T).amount, 15_000_000);
  assert.equal(parseQuote("견적 455만 5천 원", T).amount, 4_555_000); // 만 뒤 천은 그대로
  assert.equal(parseExtra("합판 보강 1천만 원, 부가세 포함", T).title, "합판 보강");
  assert.equal(parseExtra("합판 보강 1천만 원, 부가세 포함", T).amount, 10_000_000);
  for (const t of ["견적 1억 원", "천만 원이요", "삼백만원 부가세 별도", "견적 2억 3천만 원"]) {
    assert.equal(parseQuote(t, T).amount, undefined, t);
    assert.ok(SAID_AMOUNT.test(t), t);
  }
  assert.ok(!SAID_AMOUNT.test("부가세 별도, 공사 3일"));
});

test("리뷰 회귀: 한글 숫자 금액은 항목 이름에 남기지 않아요", () => {
  assert.equal(parseExtra("합판 보강 천만 원, 부가세 별도", T).title, "합판 보강");
  assert.equal(parseExtra("몰딩 교체 삼십만원", T).title, "몰딩 교체");
  assert.equal(parseExtra("이중 초배 추가 5만 원", T).title, "이중 초배 추가"); // '이중'의 '이'는 숫자가 아니에요
});

test("천 단위 쉼표 금액과 부가세 표기를 추가금 항목명에서 분리해요", () => {
  // 리뷰(PR #176): 천 단위 쉼표를 문장 구분으로 보지 않아요
  const mold = parseExtra("곰팡이 처리 1,000,000원, 부가세 포함, 벽면 곰팡이 발견해서", T);
  assert.equal(mold.title, "곰팡이 처리");
  assert.equal(mold.amount, 1_000_000);
  assert.equal(mold.vat, "included");
  assert.equal(mold.reason, "벽면 곰팡이 발견해서");
  // QA: 천 단위 쉼표에서 토막을 나누지 않고, 억 금액은 항목 이름에 남기지 않아요
  const big = parseExtra("걸레받이 교체 1,000,000,000원 부가세 별도, 기존 것이 썩어서", T);
  assert.equal(big.title, "걸레받이 교체");
  assert.equal(big.amount, 1_000_000_000);
  assert.equal(big.reason, "기존 것이 썩어서");
  const eok = parseExtra("합판 보강 2억 원, 부가세 포함", T);
  assert.equal(eok.title, "합판 보강");
  assert.equal(eok.amount, undefined); // 억은 읽지 않아요(화면이 '금액을 읽지 못했어요'로 알려요)
});

test("복합 금액과 인원은 뒷자리만 후보로 만들지 않아요", () => {
  for (const [text, value] of [["1천500만 원", 15_000_000], ["2천300만 원", 23_000_000], ["2천3백5십만 원", 23_500_000]] as const) {
    assert.equal(parseQuote(text, T).amount, value);
    assert.equal(parseExtra(text, T).amount, value);
  }
  for (const text of ["1.5천500만 원", "천500만 원", "1천3000만 원", "견적 천만, 부가세 별도"]) {
    assert.equal(parseQuote(text, T).amount, undefined, text);
  }
  assert.equal(parseDates("12명", T)?.crew, 12);
  assert.equal(parseDates("10명", T)?.crew, 10);
  assert.equal(parseDates("2~3명", T)?.crew, undefined);
  assert.equal(parseDates("앞서 정한 세 명 그대로", T)?.crew, 3);
});

test("인원 범위와 잘린 숫자는 날짜와 분리해도 확정하지 않아요", () => {
  for (const phrase of ["2〜3명", "2—3명", "2－3명", "2~3인", "2인〜3인", "두세 인", "1,003명", "1,03명"]) {
    assert.equal(readCrew(phrase).mentioned, true, phrase);
    assert.equal(readCrew(phrase).value, undefined, phrase);
    assert.deepEqual(parseDates("12/8부터 9일까지, " + phrase, T), {
      days: ["2026-12-08", "2026-12-09"], crew: undefined,
    }, phrase);
  }
  assert.deepEqual(parseDates("12/8,3명", T), { days: ["2026-12-08"], crew: 3 });
  assert.equal(readCrew("12인").value, 12);
});

test("날짜에 명시한 연도를 유지하고 축약한 날짜는 그 연도를 상속해요", () => {
  const expected = { days: ["2027-12-08", "2027-12-09"], crew: 3 };
  for (const text of ["2027년 12월 8일부터 9일까지, 3명", "2027년 12월 8일부터 12월 9일까지, 3명",
    "2027-12-08부터 2027-12-09까지, 3명",
    "2027/12/08, 9일, 3명", "2027.12.08, 9일, 3명"]) {
    assert.deepEqual(parseDates(text, T), expected, text);
  }
  assert.deepEqual(parsePeriod("2027년 12월 8일부터 9일까지", T), {
    start: "2027-12-08", end: "2027-12-09", excluded: [], outside: [],
  });
  assert.deepEqual(parsePeriod("2027년 1월 30일부터 2월 2일까지", T), {
    start: "2027-01-30", end: "2027-02-02", excluded: [], outside: [],
  });
  assert.deepEqual(parsePeriod("2026-12-31부터 2027-01-02까지", T), {
    start: "2026-12-31", end: "2027-01-02", excluded: [], outside: [],
  });
  assert.deepEqual(parseDates("2027-12-08", T), { days: ["2027-12-08"], crew: undefined });
  assert.deepEqual(parseDates("2028년 2월 28일,29일", T), { days: ["2028-02-28", "2028-02-29"], crew: undefined });
  assert.equal(parseQuote("유효기간은 2027년 12월 8일까지", T).valid, "2027-12-08");
  assert.equal(parseQuote("유효기간은 2027-12-08까지", T).valid, "2027-12-08");
});

test("명시적인 과거 날짜나 잘못된 날짜는 다른 연도나 부분 날짜로 바꾸지 않아요", () => {
  for (const text of ["2025년 12월 8일", "2025-12-08", "2027년 2월 29일", "2027-13-02",
    "2027년 2월 30일부터 3월 2일까지", "2027년 12월 8일, 32일", "2025년 12월 8일, 2027년 12월 9일"]) {
    assert.equal(parseDates(text, T), null, text);
  }
  assert.equal(parsePeriod("2025년 12월 8일부터 9일까지", T), null);
  assert.equal(parsePeriod("2027년 12월 31일부터 1월 2일까지", T), null);
  assert.equal(parseQuote("2025년 12월 8일까지 유효", T).valid, undefined);
});

test("읽지 못한 한글 금액은 조사나 띄어쓰기와 상관없이 두 금액 폼에서 저장을 막아요", () => {
  const unresolved = [
    "견적 이천만으로", "견적 오백만으로", "견적 천 만으로", "견적 천만요",
    "이 천 오 백 만 원으로", "삼백만은", "십오만으로", "삼만으로",
    "금액은 이만으로", "추가금액은 오만으로", "견적 100만 원에서 삼만으로",
    "견적 100만 원에서 이천만으로", "이천오백만원입니다", "일억으로",
  ];
  for (const phrase of unresolved) {
    const text = phrase + ", 부가세 별도";
    assert.equal(SAID_AMOUNT.test(text), true, text);
    assert.equal(parseQuote(text, T).amount, undefined, text);
    assert.equal(parseExtra(text, T).amount, undefined, text);
    assert.equal(amountToConfirm(text, T), undefined, text);
    for (const form of ["quote", "extra"] as const) {
      assert.match(assistInputIssues(form, text, { today: T }).amount ?? "", /금액을 확인하지 못했어요/, text);
    }
  }
});

test("금액과 무관한 만·이만 표현 및 정상 복합 금액 정정·유지는 차단하지 않아요", () => {
  for (const text of [
    "기간만 변경해 주세요", "부가세만 별도", "이만 마무리할게요", "견적 이만 마무리",
    "일만 먼저 진행해 주세요", "견적은 이만큼만", "금액은 그대로",
  ]) {
    assert.equal(SAID_AMOUNT.test(text), false, text);
    assert.equal(assistInputIssues("quote", text, { today: T }).amount, undefined, text);
  }
  for (const [text, expected] of [
    ["견적 1천500만 원, 공사 기간은 미정", 15_000_000],
    ["견적 2천300만 원 그대로", 23_000_000],
    ["견적 1 천 500 만 원", 15_000_000],
    ["견적 1천500만 원에서 2천300만 원으로 변경", 23_000_000],
    ["견적 455만 5 천 원", 4_555_000],
  ] as const) {
    assert.equal(parseQuote(text, T).amount, expected, text);
    assert.equal(assistInputIssues("quote", text, { today: T }, amountToConfirm(text, T)).amount, undefined, text);
  }
  const correction = "아까 1천500만 원이라고 했는데 2천300만 원으로 정정해 주세요";
  assert.equal(amountToConfirm(correction, T), 23_000_000);
  assert.equal(assistInputIssues("quote", correction, { today: T }, amountToConfirm(correction, T)).amount, undefined);
});

test("추측·보류·취소한 복합 금액은 규칙 후보와 확인 금액을 만들지 않아요", () => {
  for (const phrase of [
    "견적 1천500만 원으로 할까?", "아마 견적 2천300만 원",
    "1천500만 원으로 할지 고민 중", "1천500만 원은 미정",
    "1천500만 원 취소", "견적 2천300만 원으로 하지 말아 주세요",
  ]) {
    const text = phrase + ", 부가세 별도";
    assert.equal(parseQuote(text, T).amount, undefined, text);
    assert.equal(amountToConfirm(text, T), undefined, text);
    assert.match(assistInputIssues("quote", text, { today: T }).amount ?? "", /금액을 확인하지 못했어요/, text);
  }
});
