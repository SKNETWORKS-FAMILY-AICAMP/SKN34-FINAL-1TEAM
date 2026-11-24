// 업체 도우미: 업체가 말로 적은 글에서 폼 칸에 넣을 값만 뽑아요(규칙만 쓰고 LLM은 부르지 않아요).
// 금액은 적은 숫자 그대로 옮기고 더하거나 곱하지 않아요. 저장 · 보내기는 하지 않아요.
// 애매하면 칸을 채우지 않아요(틀린 값을 넣는 것보다 비워 두고 업체가 적는 편이 안전해요).

/** outside: 안 된다고 했지만 기간 밖이라 넣지 않은 날 */
export type PeriodFill = { start: string; end: string; excluded: string[]; outside: string[] };
export type QuoteFill = { amount?: number; vat?: "included" | "excluded" | "exempt"; days?: number; valid?: string };

const NO = /안\s*(?:돼|됨|되|됩|될)|못\s*\S|불가|빼고|빼\s*주|빼요|어려|어렵|힘들|곤란|제외|휴무|쉬어|쉽니다|쉬는|않/;
/** 이중 부정: "어렵지 않아요 · 안 되지는 않아요"는 되는 날이에요 */
const NO_NOT = /(?:어렵|힘들|곤란|안\s*되|불가하)지\s*(?:는|도|만)?\s*않/;
/** 값을 부정하는 말("455만 원이 아니라", "3일 아니고", "포함 아닙니다") */
const NEG = String.raw`(?:아니|아닙|아냐|아녜|아님|말고)`;
/** 값 바로 뒤에 부정이 붙었는지 */
const NEG_AFTER = new RegExp(String.raw`^\s*원?\s*(?:은|는|이|가)?\s*${NEG}`);
/** 앞 값을 뒤 값으로 고치는 사이 말: "아니라 · 아니고 · 말고" · "455만 원, 아니 555만 원" · "3일 → 5일" */
const FIX_GAP = String.raw`(?:\s*원?\s*(?:은|는|이|가)?\s*(?:아니라|아니고|아니구|아닌|말고)[\s,]*|\s*원?\s*,?\s*아니(?:요|오)?[\s,]+|\s*원?\s*(?:→|->|=>)\s*)`;
/** 바꾸는 말: 두 값 사이에 있는데 어느 값인지 못 고르면 비워요 */
const CHANGE = /변경|바꾸|바꿔|바뀌|바꿨|바꿈|수정|정정|아니|→|->|=>|에서/;
/** 날짜만 지운 토막에 남은 게 조사 · 이음말뿐이면 날짜만 늘어놓은 토막이에요("12/4, 12/3은 안 돼요"의 12/4) */
const FULL_DATE = String.raw`(?<!\d)\d{4}\s*(?:년|[-/.])\s*\d+\s*(?:월|[-/.])\s*\d+\s*일?`;
const DATE = String.raw`(?:${FULL_DATE}|(?:\d{1,2}\s*(?:\/|\.|월)\s*)?\d{1,2}\s*일?)`;
const DATE_ALL = new RegExp(DATE, "g");
const BARE = /^[\s과와랑및도은는이가]*$/;
// 정정: "12/2부터 → 12/3부터로 정정" · "아니 12/3부터요"면 날짜를 비워요
const FIX = /→|->|정정|(?:^|[\s.,!?])아니(?:\s|,|$)|말고|다시/;
// 토막 나누기: 쉼표 · 줄바꿈 · 이음말, 마침표는 숫자 사이(12.2)가 아닐 때만
const PARTS = /,|\n|(?<!\d)\.|\.(?!\d)|그리고|하고|인데|는데|지만/;
const pad = (n: number) => String(n).padStart(2, "0");

/** 연도와 날짜를 한 토큰으로 보존하고 ISO 날짜 안의 '-'를 범위 표시와 구분해요. */
function normalizeFullDates(text: string): string {
  return text.replace(/(?<!\d)(\d{4})\s*(?:년|[-/.])\s*(\d+)\s*(?:월|[-/.])\s*(\d+)\s*일?/g,
    (_, year: string, month: string, day: string) => `${year}년 ${month}월 ${day}일`);
}

/** 토막 나누기. 괄호 안은 한 토막으로 둬요("(12/3, 12/4 불가)"는 둘 다 안 되는 날) */
function partsOf(text: string): string[] {
  const inner: string[] = [];
  const outer = text.replace(/\(([^)]*)\)/g, (_, x: string) => { inner.push(x); return ","; });
  return [...outer.split(PARTS), ...inner].filter((p) => p.trim());
}

/** 월 · 일 → "2026-12-02". 오늘보다 여섯 달 넘게 앞선 달이면 내년으로 봐요(12월에 적은 1월) */
function iso(month: number, day: number, today: string, explicitYear?: number): string | null {
  if (month < 1 || month > 12 || day < 1 || day > 31) return null;
  const ty = Number(today.slice(0, 4));
  const tm = Number(today.slice(5, 7));
  const y = explicitYear ?? (month < tm - 6 ? ty + 1 : ty);
  if (y < 1000 || y > 9999) return null;
  const d = new Date(Date.UTC(y, month - 1, day));
  if (d.getUTCMonth() !== month - 1) return null;
  return `${y}-${pad(month)}-${pad(day)}`;
}

type Month = { m: number; year?: number; prev?: string; bad?: boolean };

/** 글 한 토막의 날짜들. "12/2", "12.2", "12월 2일", "3일", "12/2~5"를 읽고, 달이 없으면 앞에서 본 달을 써요.
 *  "3일간 · 3일 정도"는 기간이라 날짜가 아니에요. ok면 달 없는 날이 앞 날보다 이를 때 애매하다고 표시해요("12/8부터 3일") */
function daysIn(part: string, today: string, month: Month, ok = false): string[] {
  const text = normalizeFullDates(part).replace(/[~∼〜\-–—－]\s*(\d{1,2})(?!\s*[/.월\d])/g, "~$1일");
  const out: string[] = [];
  for (const x of text.matchAll(/(?<!\d)(\d{4})년\s*(\d+)월\s*(\d+)일|(?<!\d)(\d{1,2})\s*(?:\/|\.|월)\s*(\d{1,2})(?!\d)\s*일?|(?<!\d)(\d{1,2})\s*일(?!\s*(?:간|동안|정도|째|걸|소요))/g)) {
    if (x[1]) {
      month.year = Number(x[1]);
      month.m = Number(x[2]);
    } else if (x[4]) {
      // 같은 날짜 표현에서 생략한 연도는 앞에서 명시한 연도를 상속해요.
      month.m = Number(x[4]);
    }
    const day = iso(month.m, Number(x[3] ?? x[5] ?? x[6]), today, month.year);
    if (!day || (month.year !== undefined && day < today)) { month.bad = true; continue; }
    if (ok) {
      if (x[6] && month.prev && day < month.prev) month.bad = true;
      month.prev = day;
    }
    out.push(day);
  }
  return out;
}

/** 기간 표시: '부터 · 까지 · ~ · -'가 있어야 처음 ~ 끝을 기간으로 봐요 */
const RANGE = /부터|까지|~|∼|〜|(?<=\d\s*일?\s*)[-–—－](?=\s*\d)/;

/** "12/2부터 12/5까지 돼요, 12/4는 안 돼요" → 기간과 빼는 날. 못 읽으면 null.
 *  "12월 2일과 12월 5일만 가능"처럼 날짜만 늘어놓으면 기간이 아니라서 비워요(하루만 말하면 그 하루) */
export function parsePeriod(text: string, today: string, baseMonth?: string | null): PeriodFill | null {
  text = normalizeFullDates(text);
  if (FIX.test(text)) return null;
  const month: Month = { m: Number((baseMonth ?? today).slice(5, 7)) };
  const ok: string[] = [];
  const no: string[] = [];
  // 날짜만 있는 토막은 뒤 토막의 말(되는지 · 안 되는지)을 따라요
  let wait: string[] = [];
  for (const part of partsOf(text)) {
    if (!RANGE.test(part) && /\d/.test(part) && BARE.test(part.replace(DATE_ALL, ""))) { wait.push(part); continue; }
    if (!NO.test(part) || NO_NOT.test(part)) {
      for (const w of [...wait, part]) ok.push(...daysIn(w, today, month, true));
      wait = [];
      continue;
    }
    const got = [...wait, part].flatMap((w) => daysIn(w, today, month));
    wait = [];
    // "수요일은 안 돼요" · "12/3 빼고 12/5까지"는 날을 정할 수 없어요
    if (!got.length && /요일|주말/.test(part)) return null;
    if (got.length && RANGE.test(part)) return null;
    no.push(...got);
  }
  for (const w of wait) ok.push(...daysIn(w, today, month, true));
  if (month.bad || !ok.length) return null;
  // 범위 표시 없이 늘어놓거나, "12/2부터"처럼 끝이 없으면 비워요
  if (new Set(ok).size > 1 !== RANGE.test(text)) return null;
  // 연도를 명시한 범위의 끝이 시작보다 앞서면 다음 해라고 추측하지 않아요.
  if (/\d{4}년/.test(text) && RANGE.test(text) && ok.some((d, i) => i > 0 && d < ok[i - 1])) return null;
  ok.sort();
  const nos = new Set(no);
  // 안 된다고 한 날은 첫날 · 끝날이어도 가능한 날로 넣지 않아요(12/2~12/5에 12/2가 안 되면 12/3부터)
  let start = ok[0];
  let end = ok[ok.length - 1];
  while (start <= end && nos.has(start)) start = shift(start, 1);
  while (end >= start && nos.has(end)) end = shift(end, -1);
  if (start > end) return null;
  const sorted = [...nos].sort();
  return { start, end, excluded: sorted.filter((d) => d > start && d < end), outside: sorted.filter((d) => d < ok[0] || d > ok[ok.length - 1]) };
}

/** "2026-12-02"에서 며칠 앞뒤 */
function shift(day: string, by: number): string {
  const d = new Date(`${day}T00:00:00Z`);
  d.setUTCDate(d.getUTCDate() + by);
  return d.toISOString().slice(0, 10);
}

const num = (s: string) => Number(s.replace(/,/g, ""));
const MAN_SCALE: Record<string, number> = { 천: 1000, 백: 100, 십: 10 };
const COMPOUND_MAN = /(?<![\d.,천백십])(?:[\d천백십][ \t]*){1,24}만/g;
const MONEY_END = String.raw`(?=$|[\s,.;!?？。:)]|원|으로|로|[은는이가을를도요]|부터|까지|예요|쯤|정도)`;
const WRITTEN_START = String.raw`(?<![가-힣\d])(?<!\d[ \t]+)`;
// '이만'이나 '기간만'은 금액이 아닐 수 있어요. 원이 없으면 숫자 단위나 명시적 금액 대상을 확인해요.
const WRITTEN_AMOUNT = new RegExp([
  String.raw`${WRITTEN_START}(?:[일이삼사오육칠팔구십백천만억][ \t]*){1,16}원${MONEY_END}`,
  String.raw`${WRITTEN_START}(?=[일이삼사오육칠팔구십백천 \t]*[십백천])(?:[일이삼사오육칠팔구십백천][ \t]*){1,16}만${MONEY_END}`,
  String.raw`${WRITTEN_START}(?:[일이삼사오육칠팔구십백천][ \t]*){1,16}억${MONEY_END}`,
  String.raw`${WRITTEN_START}[삼사육칠팔구][ \t]*만${MONEY_END}`,
  String.raw`(?:견적(?:[ \t]*금액)?|금액|가격|비용|공사비|총액|추가금(?:액)?|합계|계약금|잔금)[ \t]*(?:[은는이가을를:=][ \t]*)?(?:[일오][ \t]*만${MONEY_END}|이[ \t]*만(?=$|[,.;!?？。]|원|으로|로|입니다|이에요|예요|요))`,
].join("|"));
export const SAID_AMOUNT = new RegExp(String.raw`\d\s*[억천백만원]|${WRITTEN_AMOUNT.source}`);

/** 만 앞의 혼합 표기를 통째로 읽어요. 천500만처럼 시작 숫자가 없으면 뒷부분도 쓰지 않아요. */
function manOf(text: string): string | null {
  let invalid = false;
  const result = text.replace(COMPOUND_MAN, (word) => {
    if (!/[천백십]/.test(word)) return word;
    const body = word.slice(0, -1).replace(/\s/g, "");
    const groups = /(\d+)([천백십]?)/gy;
    let limit = Infinity;
    let total = 0;
    while (groups.lastIndex < body.length) {
      const part = groups.exec(body);
      if (!part) { invalid = true; break; }
      const scale = MAN_SCALE[part[2]] ?? 1;
      const value = Number(part[1]) * scale;
      if (scale >= limit || value >= limit) { invalid = true; break; }
      limit = scale;
      total += value;
    }
    return total + "만";
  });
  // 소수·쉼표가 잘못 붙어 합성 단위를 전부 해석하지 못했으면 뒷자리만 채택하지 않는다.
  return invalid || /[천백십][\d.,천백십 \t]*만/.test(result) || WRITTEN_AMOUNT.test(result) ? null : result;
}

/** 복합 금액과 같은 절에서 보류·질문으로 말한 금액은 규칙 후보도 확정하지 않아요. */
export function uncertainCompoundAmount(text: string): boolean {
  return text.split(/(?<!\d),|,(?!\d{3}(?!\d))|\n|(?<!\d)\.|\.(?!\d)|。/).some((clause) =>
    [...clause.matchAll(COMPOUND_MAN)].some((m) => /[천백십]/.test(m[0])) &&
    /[?？]|아마|혹시|미정|모르|애매|일까|할까|할지|고민|생각\s*중|검토\s*중|것\s*같|취소|철회|보류|하지\s*마|하지\s*말|안\s*할/.test(clause));
}

export function recognizedCompoundAmount(text: string): number | undefined {
  if (uncertainCompoundAmount(text)) return undefined;
  const matches = [...text.matchAll(COMPOUND_MAN)];
  if (matches.length !== 1 || !/[천백십]/.test(matches[0][0])) return undefined;
  const [part] = matches;
  const rest = text.slice(0, part.index) + text.slice(part.index + part[0].length);
  if (SAID_AMOUNT.test(rest)) return undefined;
  const normalized = manOf(text);
  return normalized === null ? undefined : amountIn(normalized);
}
/** 금액 앞에 붙으면 '전체 금액'으로 보는 말 */
const TOTAL = /(?:전체|총|합계|견적)\s*(?:금액|견적|은|는|이|가)?\s*$/;

/** 금액: "455만 원" · "455.5만 원" · "455만 5천 원" · "455만 5,000원" · "4,550,000원". 만 뒤 숫자는 바로 '원'이 붙을 때만 더해요("455만 3일"의 3은 금액이 아니에요).
 *  금액이 둘 이상이면 '전체 · 총 · 합계 · 견적'이 붙은 하나만 쓰고, 고를 수 없으면 비워요("철거 20만 원 별도 전체 견적 455만 원"은 455만) */
function amountIn(text: string): number | undefined {
  if (/억/.test(text)) return undefined; // 억 단위는 읽지 않아요
  const hits: { at: number; end: number; won: number }[] = [];
  for (const m of text.matchAll(/(\d[\d,]*(?:\.\d+)?)\s*만\s*(?:(\d)\s*천)?\s*(?:(\d[\d,]*)\s*원)?/g)) {
    const won = Math.round(num(m[1]) * 10000) + (m[2] ? Number(m[2]) * 1000 : 0) + (m[3] ? num(m[3]) : 0);
    hits.push({ at: m.index, end: m.index + m[0].length, won });
  }
  for (const m of text.matchAll(/(\d{1,3}(?:,\d{3})+|\d{6,})\s*원?/g)) {
    if (!hits.some((h) => m.index >= h.at && m.index < h.end)) hits.push({ at: m.index, end: m.index + m[0].length, won: num(m[1]) });
  }
  hits.sort((a, b) => a.at - b.at);
  // 정정: "455만 원이 아니라 555만 원" · "455만 원, 아니 555만 원" · "455만 원에서 555만 원으로"는 앞 자리에 뒤 값을 써요.
  // 두 금액 사이에 바꾸는 말이 있는데 어느 쪽인지 모르면 비워요. 뒤 값 없이 부정만 하면 그 값을 버려요
  for (let i = hits.length - 1; i >= 0; i--) {
    const next = hits[i + 1];
    const gap = next ? text.slice(hits[i].end, next.at) : "";
    const to = next ? /^\s*원?\s*(?:으로|로)/.test(text.slice(next.end)) : false;
    if (next && (new RegExp(`^${FIX_GAP}$`).test(gap) || (to && /^\s*원?\s*에서\s*$/.test(gap)))) {
      hits[i].won = next.won;
      hits.splice(i + 1, 1);
    } else if (next && CHANGE.test(gap)) return undefined;
    else if (NEG_AFTER.test(text.slice(hits[i].end))) hits.splice(i, 1);
  }
  if (hits.length <= 1) return hits[0]?.won;
  const total = hits.filter((h) => TOTAL.test(text.slice(Math.max(0, h.at - 12), h.at)));
  return total.length === 1 ? total[0].won : undefined;
}

/** 뒤에 붙으면 그 말을 버리는 부정("면세는 아니고", "포함된 금액이 아닙니다", "포함하지 않아요").
 *  X 표시는 바로 뒤만, 말은 쉼표 · 마침표나 다음 부가세 말을 넘지 않는 8자 안에서 봐요 */
const NOT = new RegExp(String.raw`^\s*(?:은|는|이|가)?\s*(?:안\s*\S|못|\(\s*[Xx×]\s*\)|[Xx×](?![A-Za-z]))|^(?:(?!면세|부가|VAT)[^,.\n]){0,8}?(?:${NEG}|없|않|안\s*(?:돼|됨|되|됩|해|하|함|합|했))`, "i");
/** 바로 뒤에 붙으면 조건이라 고를 수 없는 말("부가세 별도 시 415만 원") */
const IF = /^\s*(?:시|면|하면|이면|라면|일\s*(?:때|경우))(?![가-힣])/;
/** "부가세 포함 안 돼요, 별도예요"처럼 포함을 부정한 뒤 이어지는 토막 */
const THEN_EX = /^[^,.\n]*[,.\n]\s*(?:별도|따로|미포함)/;

/** 부가세: '부가세 · VAT' 바로 뒤 말만 봐요("부가세 포함, 철거는 별도"는 포함이에요).
 *  "면세는 아니고 부가세 포함"처럼 부정한 말은 버리고, 남은 방식이 하나일 때만 채워요 */
function vatIn(text: string): QuoteFill["vat"] {
  const said = new Set<NonNullable<QuoteFill["vat"]>>();
  for (const m of text.matchAll(/면세/g)) {
    if (!NOT.test(text.slice(m.index + m[0].length))) said.add("exempt");
  }
  for (const m of text.matchAll(/(?:부가세|부가가치세|VAT)\s*(?:는|은)?\s*(별도|빼고|미포함|따로|포함)/gi)) {
    const rest = text.slice(m.index + m[0].length);
    if (IF.test(rest)) return undefined;
    if (!NOT.test(rest)) said.add(m[1] === "포함" ? "included" : "excluded");
    else if (m[1] === "포함" && THEN_EX.test(rest)) said.add("excluded");
  }
  return said.size === 1 ? [...said][0] : undefined;
}

/** 유효기간: '유효'에 바로 붙은 날짜만 써요("12/17까지 유효" · "유효기간은 12/17까지"). "12/11까지 공사, 유효기간은 일주일"은 비워요 */
function validIn(text: string, today: string): string | undefined {
  const D = String.raw`(?<![\d/.])(${DATE})`;
  const re = new RegExp(String.raw`${D}\s*까지\s*(?:만\s*)?유효|유효\s*기간\s*(?:은|는|이)?\s*${D}\s*까지`, "g");
  // "유효기간 12/17까지 아니고 12/20까지"는 고친 뒤 날짜를 써요
  const fix = new RegExp(String.raw`^${FIX_GAP}${D}\s*까지`);
  const hits = new Set([...text.matchAll(re)].map((m) => (text.slice(m.index + m[0].length).match(fix)?.[1] ?? m[1] ?? m[2]).trim()));
  if (hits.size !== 1) return undefined;
  const said = [...hits][0];
  const bare = !/[/.월-]/.test(said);
  const tm = Number(today.slice(5, 7));
  const anchor: Month = { m: tm };
  let d: string | undefined = daysIn(bare ? `${said.replace(/일/, "")}일` : said, today, anchor)[0];
  if (anchor.bad) return undefined;
  // 달 없이 "17일까지"라고 했는데 이미 지난 날이면 다음 달로 봐요
  if (d && bare && d < today) d = iso((tm % 12) + 1, Number(d.slice(8, 10)), today) ?? undefined;
  return d;
}

/** 달 없이 쓴 "N일"(날짜 "12월 17일" · "17일까지"는 빼요). 둘 이상이면 공사 기간을 고를 수 없어요 */
const BARE_DAYS = /(?<![\d/.]|월\s*)(\d{1,3})\s*일(?!\s*(?:까지|부터|요일))/g;

/** 공사 기간: "공사 3일" · "3일 걸려요" · "3일간"처럼 기간이라는 말이 붙을 때만 읽어요("9일부터"는 날짜예요).
 *  "공사 3일 아니고 5일" · "3일, 아니 5일" · "3일 → 5일" · "3일에서 5일로"는 고친 뒤 값을 쓰고,
 *  뒤 값 없이 부정만 하거나 "3일 걸리고 건조 2일"처럼 후보가 여럿이면 비워요 */
function daysOf(text: string): number | undefined {
  const m = text.match(/(?:공사|작업|시공)\s*(?:기간)?\s*(?:은|는|이|가)?\s*(\d{1,3})\s*일(?!\s*까지)/) ?? text.match(/(\d{1,3})\s*일\s*(?:간|동안|걸|소요|작업|정도)/);
  if (!m) return undefined;
  const rest = text.slice((m.index ?? 0) + m[0].length);
  const fixed = rest.match(new RegExp(String.raw`^(?:${FIX_GAP}|\s*에서\s*(?=\d{1,3}\s*일\s*(?:으로|로)))(\d{1,3})\s*일(?!\s*까지)`));
  if (fixed) return Number(fixed[1]);
  if (NEG_AFTER.test(rest)) return undefined;
  if ([...text.matchAll(BARE_DAYS)].length !== 1) return undefined;
  if (/아니|정정|→|->|말고|다시/.test(text.slice((m.index ?? 0) + m[0].length).slice(0, 12))) return undefined;
  return Number(m[1]);
}

/** "견적 455만 원, 부가세 포함, 공사 3일, 12/17까지 유효" → 견적서 칸 값. 읽은 칸만 돌려줘요 */
export function parseQuote(text: string, today: string): QuoteFill {
  const out: QuoteFill = {};
  const normalized = manOf(text);
  const amount = normalized === null || uncertainCompoundAmount(text) ? undefined : amountIn(normalized);
  if (amount) out.amount = amount;
  const vat = vatIn(text);
  if (vat) out.vat = vat;
  const days = daysOf(text);
  if (days) out.days = days;
  const valid = validIn(text, today);
  if (valid) out.valid = valid;
  return out;
}

export type DatesFill = { days: string[]; crew?: number };

/** 명시적 월 뒤에 압축된 목록("12/11,12,13일")의 중간 숫자만 날짜로 읽어요. */
function expandDateList(text: string, today: string): string | null {
  let invalid = false;
  const rewritten = text.replace(
    /(?:\d{4}년\s*)?\d{1,2}\s*(?:\/|\.|월)\s*\d{1,2}\s*일?(?:\s*,\s*\d{1,2})+\s*일(?!\s*(?:간|동안|정도))/g,
    (list) => {
      const parts = list.split(",").map((part) => part.trim());
      const anchor = parts[0].match(/^(?:(\d{4})년\s*)?(\d{1,2})\s*(?:\/|\.|월)\s*(\d{1,2})/);
      if (!anchor || parts.some((part, i) => {
        const day = i === 0 ? Number(anchor[3]) : Number(part.match(/^\d{1,2}/)?.[0]);
        return !iso(Number(anchor[2]), day, today, anchor[1] ? Number(anchor[1]) : undefined);
      })) {
        invalid = true;
        return list;
      }
      return parts.map((part) => part.endsWith("일") ? part : `${part}일`).join(", ");
    },
  );
  return invalid ? null : rewritten;
}

/** 공사 날짜: "12/8부터 12/10까지, 2명" → 그 사이 날 모두(안 된다고 한 날 · 공휴일 빼고, noWeekend면 토 · 일도 빼고).
 *  "12/8, 12/9, 12/11"처럼 늘어놓으면 그 날만(말한 날은 주말이어도 그대로 두고 서버가 다시 봐요) */
export function parseDates(text: string, today: string, baseMonth?: string | null, holidays: string[] = [], noWeekend = false): DatesFill | null {
  const { value: crew, withoutCrew } = readCrew(text);
  const said = normalizeFullDates(withoutCrew);
  const days: string[] = [];
  if (RANGE.test(said)) {
    const p = parsePeriod(said, today, baseMonth);
    const skip = new Set([...(p?.excluded ?? []), ...holidays]);
    for (let d = new Date(`${p?.start}T00:00:00Z`); p && Date.parse(p.end) - Date.parse(p.start) <= 120 * 86_400_000 && d.toISOString().slice(0, 10) <= p.end; d.setUTCDate(d.getUTCDate() + 1)) {
      const k = d.toISOString().slice(0, 10);
      if (!skip.has(k) && !(noWeekend && (d.getUTCDay() === 0 || d.getUTCDay() === 6))) days.push(k);
    }
  } else {
    const month: Month = { m: Number((baseMonth ?? today).slice(5, 7)) };
    const skip = new Set(holidays);
    const expanded = expandDateList(said, today);
    const got = FIX.test(said) || expanded === null ? [] : partsOf(expanded).filter((x) => !NO.test(x)).flatMap((x) => daysIn(x, today, month, true));
    if (!month.bad) days.push(...[...new Set(got)].filter((d) => !skip.has(d)).sort());
  }
  if (!days.length && !crew) return null;
  return { days, crew };
}

const NATIVE_CREW: Record<string, number> = { 한: 1, 두: 2, 세: 3, 네: 4, 다섯: 5, 여섯: 6, 일곱: 7, 여덟: 8, 아홉: 9, 열: 10, 열두: 12 };
const CREW_NUMBER = String.raw`(?:(?<![\d./월-])\d+(?:,\d+)+(?:\.\d+)?|(?<![\d.])\d+(?:\.\d+)?|(?<![가-힣])(?:한|두|세|네|다섯|여섯|일곱|여덟|아홉|열두|열))`;
const CREW_UNIT = String.raw`(?:명|인(?=$|[\s.,!?;~/∼〜–—－-]|으로|[은을도과]|부터|에서|입니다|이에요))`;
const CREW_RANGE = new RegExp(String.raw`${CREW_NUMBER}\s*(?:${CREW_UNIT}\s*)?(?:[~/∼〜–—－-]|부터|에서|또는|혹은)\s*${CREW_NUMBER}\s*${CREW_UNIT}|(?<![가-힣])(?:한|두|세|네)\s*(?:한|두|세|네)\s*${CREW_UNIT}`, "g");
const CREW_TERM = new RegExp(String.raw`(${CREW_NUMBER})\s*${CREW_UNIT}`, "g");

/** 범위 표현을 먼저 걷어내서 '2/3인'의 일부를 날짜나 확정 인원으로 읽지 않는다. */
export function readCrew(text: string) {
  const single = text.replace(CREW_RANGE, " ");
  const ambiguous = single !== text;
  const counts = new Set<number>();
  const withoutCrew = single.replace(CREW_TERM, (_, raw: string) => {
    counts.add(NATIVE_CREW[raw] ?? Number(raw));
    return " ";
  });
  const [n] = counts;
  return { mentioned: ambiguous || counts.size > 0, withoutCrew,
    value: !ambiguous && counts.size === 1 && Number.isInteger(n) && n > 0 && n < 100 ? n : undefined };
}

export type ExtraFill = QuoteFill & { title?: string; reason?: string };
const WHY = /(?<!견적|계약|문|청구|확인)서\s*요?\s*$|때문/;
/** 항목 이름에서 지울 금액: 만 · 원이 붙은 숫자만("2평 · 3장"은 둬요) */
const AMOUNT_WORD = /[일이삼사오육칠팔구십백천만억]{1,8}\s*만?\s*원(?:이|가|은|는)?|\d[\d,]*(?:\.\d+)?\s*억\s*(?:[\d,]*\s*만)?\s*(?:[\d,]*\s*원)?(?:이|가|은|는)?|\d[\d,]*(?:\.\d+)?\s*만\s*(?:\d\s*천)?\s*(?:[\d,]*\s*원)?(?:이|가|은|는)?|\d[\d,]*\s*원(?:이|가|은|는)?/g;
/** 명시적인 제목 지시가 있으면 앞쪽의 '사유 말고'를 제목으로 읽지 않아요. */
const EXTRA_TITLE_TARGET = /(?<![가-힣a-zA-Z\d])(?:(?:추가|공사|시공)\s*)?(?:항목명|작업명|제목|항목|이름)\s*(?::|=|은|는|을|를)\s*/g;
const EXTRA_TITLE_PREFIX = /^(?:추가금|추가금액)?(?:사유|이유)[은는을를]?(?:말고|대신)$/;
const EXTRA_TITLE_CHANGE = /(?:으로|로)\s*(?:(?:바꿔|고쳐|적어|해)\s*(?:주세요|줘요|주십시오)|(?:변경|수정|정정|입력)\s*해\s*(?:주세요|줘요|주십시오)|바꿀게요|고칠게요)$/;
const EXTRA_TITLE_UNCERTAIN = /[?？]|아마|혹시|미정|모르|애매|일까|할까|고민|예정|생각\s*중|검토\s*중|것\s*같|가능하면|(?:바꾸|고치|수정|변경|정정)려고|또는|아니면/;
const EXTRA_TITLE_DENIED = /(?:그대로|유지|바꾸지|고치지|수정하지|변경하지|건드리지|손대지|삭제|지우|비우|없애|취소|하지\s*마|주지\s*마|말아\s*주|안\s*(?:할|해|하|바꿔|고쳐|수정|변경)|아니|아닙|말고|대신)/;
const EXTRA_OTHER_FIELD = /(?:사유|이유|금액|비용|부가세|VAT)\s*(?::|=|은|는|을|를)/i;
const EXTRA_NOT_FREE_TITLE = /^(?:추가금|추가\s*(?:금액|비용|요금)|금액|비용|가격|공사비|총액|견적|사유|이유|부가세|부가가치세|VAT|면세|제목|항목명|항목|작업명|이름)(?:\s|은|는|이|가|을|를|만|도|:|=|$)/i;

/** 제목 대상이 하나로 정해졌을 때만 해당 절에서 값을 추출해요. */
function targetedExtraTitle(text: string): { targeted: boolean; value?: string } {
  const targets = [...text.matchAll(EXTRA_TITLE_TARGET)];
  if (!targets.length) return { targeted: false };
  if (targets.length !== 1) return { targeted: true };
  const match = targets[0];
  const before = text.slice(0, match.index);
  const prefix = before.split(/[,;.!?？\n]/).at(-1)?.trim();
  if (prefix && !EXTRA_TITLE_PREFIX.test(prefix.replace(/\s+/g, ""))) return { targeted: true };
  const rest = text.slice(match.index + match[0].length);
  const boundary = rest.search(/[,;.!?？\n]/);
  if (boundary >= 0 && /[?？]/.test(rest[boundary])) return { targeted: true };
  if (boundary >= 0) {
    const later = rest.slice(boundary + 1).split(/[,;.!?？\n]/);
    if (later.some((part) => /취소|철회|보류/.test(part) &&
      (/제목|항목|작업명|이름/.test(part) || !/금액|사유|이유|비용|부가세|세금/.test(part)))) {
      return { targeted: true };
    }
  }
  const clause = (boundary < 0 ? rest : rest.slice(0, boundary)).trim();
  if (!clause || EXTRA_TITLE_UNCERTAIN.test(clause) || EXTRA_TITLE_DENIED.test(clause) || EXTRA_OTHER_FIELD.test(clause)) {
    return { targeted: true };
  }
  const action = clause.match(EXTRA_TITLE_CHANGE);
  // 수정 동사만 있고 값을 분리할 수 없다면 문장 전체를 제목으로 사용하지 않아요.
  if (!action && /바꿔|바꾸|고쳐|고치|변경|수정|정정|입력|적어/.test(clause)) return { targeted: true };
  const value = (action ? clause.slice(0, action.index) : clause).trim();
  if (!value || value.length > 60 || /^\d/.test(value)) return { targeted: true };
  return { targeted: true, value };
}

/** 추가 금액: "곰팡이 방지 처리 12만 원, 부가세 포함, 벽 안쪽까지 곰팡이가 퍼져서" → 항목 · 금액 · 부가세 · 이유 */
export function parseExtra(text: string, today: string): ExtraFill {
  const out: ExtraFill = parseQuote(text, today);
  delete out.days;
  delete out.valid;
  const explicit = targetedExtraTitle(text);
  if (explicit.value) out.title = explicit.value;
  for (const raw of text.split(/(?<!\d),|,(?!\d{3})|\n/)) {
    const part = raw.trim();
    if (!part) continue;
    if (!out.reason && WHY.test(part)) { out.reason = part.replace(/[.。]\s*$/, ""); continue; }
    if (!out.title && !explicit.targeted && !EXTRA_NOT_FREE_TITLE.test(part) &&
      !EXTRA_TITLE_UNCERTAIN.test(part)) {
      // 정정한 뒤 말은 항목 이름이 아니에요("곰팡이 방지 12만 원이 아니라 15만 원")
      const head = (manOf(part) ?? part).split(/아니라|아니고|말고|부가세|부가가치세|VAT|면세/i)[0];
      const t = head.replace(AMOUNT_WORD, "").replace(/\s*(?:추가요|추가돼요|더\s*들어요)\s*$/, "").replace(/\s+/g, " ").trim();
      if (t && !/^\d/.test(t) && !EXTRA_TITLE_DENIED.test(t)) out.title = t.slice(0, 60);
    }
  }
  return out;
}
