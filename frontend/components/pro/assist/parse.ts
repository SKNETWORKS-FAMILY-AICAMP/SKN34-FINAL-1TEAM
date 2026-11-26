// 업체 도우미: 업체가 말로 적은 글에서 폼 칸에 넣을 값만 뽑아요(규칙만 쓰고 LLM은 부르지 않아요).
// 금액은 적은 숫자 그대로 옮기고 더하거나 곱하지 않아요. 저장 · 보내기는 하지 않아요.
// 애매하면 칸을 채우지 않아요(틀린 값을 넣는 것보다 비워 두고 업체가 적는 편이 안전해요).

import { writtenMoney } from "./writtenMoney";
import { crewFinalAssignmentBody, dateCountTokens, explicitPeriodBoundaryTail, explicitPeriodExclusionList, extraTextTargets, isExtraHistoricalReport, isExtraScopedReference, maskExtraQuotes, operationScopeRisk, RECENT_REFERENCE_MARK, reportedPeriodExclusion, requestIntent, resolvedIntentSegments, scopedFieldParts, scopedVatTokens, scopedWithdrawalAffects, standaloneVatTokens, unownedExtraDependentClause, vatSemanticTokens, type ExtraTextTarget } from "./requestIntent";
import { COUNTER_WORD, counterValue, classifyDurationQuantity, partialQuantityPrefix, QUOTE_VALIDITY_LABEL, quantityDeltaSyntax } from "./quantity";
import { validCombinedPeriod } from "./protocol";

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

// 범위 표지를 생략해도 각 경계가 독립적으로 명시되고 확정된 경우는
// 두 날짜의 역할을 알 수 있어요. 날짜 목록이나 미완결 절은 이 경로에
// 들어오지 않으며, 각 경계를 원래 날짜 파서와 결합 검증으로 확인해요.
const PERIOD_BOUNDARY_DATE = "(" + FULL_DATE + "|\\d{1,2}\\s*(?:[/\\.월])\\s*\\d{1,2}\\s*일?|\\d{1,2}\\s*일)";
const PERIOD_BOUNDARY_FINITE = "(?:합니다|하겠습니다|할게요|해요|해\\s*(?:주세요|줘요|주십시오))";
const PERIOD_BOUNDARY_ASSIGN = new RegExp("^" + PERIOD_BOUNDARY_DATE + "\\s*(?:(?:으로|로)\\s*(?:(?:제안|확정|설정|지정|결정|입력|등록|기재|정)" + PERIOD_BOUNDARY_FINITE + "|정해\\s*(?:주세요|줘요|주십시오))|입니다|이에요|예요)$");
const PERIOD_BOUNDARY_EDIT = new RegExp("^" + PERIOD_BOUNDARY_DATE + "\\s*(?:으로|로)\\s*(?:변경|수정)" + PERIOD_BOUNDARY_FINITE + "$");

/** 쉼표로 연결된 두 경계가 하나의 확정 술어를 공유하는 경우예요.
 * 두 필드와 값이 모두 명시되고 마지막 술어가 완결되어야 해요. */
function sharedPeriodBoundaryValues(text:string,today:string,baseMonth?:string|null):
    {values:Partial<Record<"start"|"end",string>>;evidence:Record<"start"|"end",string>;ambiguous:boolean}|undefined{
  const clauses=normalizeFullDates(text).trim().replace(/[.!。]$/,"").split(/[,，]/).map(part=>part.trim());
  if(clauses.length!==2)return;
  const first=explicitPeriodBoundaryTail(clauses[0]),last=explicitPeriodBoundaryTail(clauses[1]);
  if(!first||!last||first.field===last.field)return;
  const a=new RegExp("^"+PERIOD_BOUNDARY_DATE+"\\s*(?:으로|로)?$").exec(first.tail);
  const b=PERIOD_BOUNDARY_ASSIGN.exec(last.tail)??PERIOD_BOUNDARY_EDIT.exec(last.tail);
  if(!a||!b)return;
  const intent=requestIntent("period",text);
  const month:Month={m:Number((baseMonth??today).slice(5,7))};
  const values:Partial<Record<"start"|"end",string>>={};
  for(const [field,match] of [[first.field,a],[last.field,b]] as const){
    const date=daysIn(match[1],today,month);
    if(!month.bad&&date.length===1&&date[0]>today)values[field]=date[0];
  }
  const evidence={start:text,end:text};
  const ambiguous=intent.cancelAll||intent.blocked.size>0||intent.serverOnly.size>0||
    intent.requested.size!==2||!values.start||!values.end||
    !validCombinedPeriod({start:null,end:null,excluded:[]},values,{today});
  return {values,evidence,ambiguous};
}

/** 직접 대입된 한 경계의 값/원문 구간을 보존해요. 범위 또는
 * 상대 날짜 등 다른 검증 문법에는 이 좁은 증명을 사용하지 않아요. */
export function readExplicitPeriodBoundary(text:string,field:"start"|"end",today:string,baseMonth?:string|null):
    {value?:string;ambiguous:boolean;evidence:string[]}|undefined{
  const shared=sharedPeriodBoundaryValues(text,today,baseMonth);
  if(shared)return {value:shared.ambiguous?undefined:shared.values[field],ambiguous:shared.ambiguous,evidence:[shared.evidence[field]]};
  const scopes=scopedFieldParts("period",text,field);
  const proofs:{value?:string;evidence:string}[]=[];
  for(const evidence of scopes){
    const target=explicitPeriodBoundaryTail(normalizeFullDates(evidence));
    if(target?.field!==field)continue;
    const match=PERIOD_BOUNDARY_ASSIGN.exec(target.tail)??PERIOD_BOUNDARY_EDIT.exec(target.tail);
    if(!match)continue;
    const month:Month={m:Number((baseMonth??today).slice(5,7))};
    const values=daysIn(match[1],today,month);
    const value=!month.bad&&values.length===1&&values[0]>today?values[0]:undefined;
    proofs.push({value,evidence});
  }
  if(!proofs.length)return;
  const values=new Set(proofs.map(p=>p.value));
  const evidence=proofs.map(p=>p.evidence);
  // 명시 대입 뒤에 또 다른 날짜 후보가 이어지거나 날짜 자체가
  // 무효면 단일한 값으로 축약하지 않아요.
  const competing=scopes.some(part=>!evidence.includes(part)&&
    new RegExp(DATE).test(part));
  const ambiguous=values.size!==1||values.has(undefined)||competing;
  return {value:ambiguous?undefined:proofs[0].value,ambiguous,evidence};
}
function typedPeriodBoundaries(text: string, today: string, baseMonth?: string | null): PeriodFill | null | undefined {
  if (RANGE.test(text)) return;
  const shared=sharedPeriodBoundaryValues(text,today,baseMonth);
  if(shared)return shared.ambiguous?null:{start:shared.values.start!,end:shared.values.end!,excluded:[],outside:[]};
  const clauses = text.split(/(?<!\d)[,.]|[,.](?!\d)|[;!。\n]/).map(part => part.trim()).filter(Boolean);
  if (clauses.length !== 2) return;
  const month: Month = { m: Number((baseMonth ?? today).slice(5,7)) };
  const boundaries: Partial<Record<"start" | "end", string>> = {};
  for (const clause of clauses) {
    const target = explicitPeriodBoundaryTail(clause);
    if (!target) return;
    const intent = requestIntent("period",clause);
    const match = PERIOD_BOUNDARY_ASSIGN.exec(target.tail);
    if (!match || intent.requested.size !== 1 || !intent.requested.has(target.field) ||
        intent.blocked.has(target.field) || intent.serverOnly.has(target.field) || intent.cancelAll) return;
    if (boundaries[target.field] !== undefined) return null;
    const values = daysIn(match[1],today,month);
    if (month.bad || values.length !== 1) return null;
    boundaries[target.field] = values[0];
  }
  const { start,end } = boundaries;
  if (!start || !end || !validCombinedPeriod({start:null,end:null,excluded:[]},{start,end},{today})) return null;
  return {start,end,excluded:[],outside:[]};
}

/** 서로 독립된 경계 요청 중 이전 후보와 새 값이 충돌한 칸만 보류해요.
 * 일반 기간 파싱이나 날짜 목록에는 이 경로를 사용하지 않아요. */
export function independentPeriodBoundary(text: string, today: string, baseMonth?: string | null): { field: "start" | "end"; value: string; evidence: string } | undefined {
  if (FIX.test(text) || RANGE.test(normalizeFullDates(text))) return;
  const clauses = text.split(/(?<!\d)[,.]|[,.](?!\d)|[;!。\n]/).map(part => part.trim()).filter(Boolean);
  if (clauses.length !== 3) return;
  const overall = requestIntent("period",text);
  const assignments = new Map<"start" | "end", { value: string; evidence: string }>();
  let reference: "start" | "end" | undefined;
  for (const evidence of clauses) {
    const clause = normalizeFullDates(evidence);
    const target = explicitPeriodBoundaryTail(clause);
    if (!target) return;
    const intent = requestIntent("period",clause);
    const { field } = target;
    if (intent.requested.size !== 1 || !intent.requested.has(field) || intent.blocked.has(field) || intent.cancelAll) return;
    if (intent.priorReferences.has(field)) {
      if (reference !== undefined) return;
      reference = field;
      continue;
    }
    if (intent.serverOnly.has(field) || assignments.has(field)) return;
    const match = PERIOD_BOUNDARY_ASSIGN.exec(target.tail) ?? PERIOD_BOUNDARY_EDIT.exec(target.tail);
    if (!match) return;
    const month: Month = { m: Number((baseMonth ?? today).slice(5,7)) };
    const values = daysIn(match[1],today,month);
    if (month.bad || values.length !== 1 || values[0] <= today) return;
    assignments.set(field,{value:values[0],evidence});
  }
  if (!reference || assignments.size !== 2 || !assignments.has(reference) || overall.cancelAll ||
      !overall.carryConflicts.has(reference) || !overall.blocked.has(reference)) return;
  const field = reference === "start" ? "end" : "start";
  const other = assignments.get(field);
  if (!other || overall.blocked.has(field) || overall.serverOnly.has(field)) return;
  return {field,...other};
}

/** 각각 명시된 경계에서 문의만 하는 칸과 완결 대입하는 칸을 분리해요.
 * 두 경계에 값을 확정한 일반 기간·충돌·조건은 여기서 복구하지 않아요. */
export function independentPeriodInquiry(text:string,today:string,baseMonth?:string|null):
    {field:"start"|"end";value:string;evidence:string}|undefined{
  const intent=requestIntent("period",text);
  if(intent.cancelAll||intent.requested.size!==2||!intent.requested.has("start")||!intent.requested.has("end"))return;
  for(const field of ["start","end"] as const){
    const other=field==="start"?"end":"start";
    if(intent.blocked.has(field)||intent.serverOnly.has(field)||!intent.blocked.has(other))continue;
    const own=scopedFieldParts("period",text,field), inquiry=scopedFieldParts("period",text,other);
    if(own.length!==1||inquiry.length!==1||own[0]===inquiry[0]||
        explicitPeriodBoundaryTail(normalizeFullDates(inquiry[0]))?.field!==other||
        !/[?？]|(?:될|할|일|맞을)까요|되나요|괜찮나요|가능한지|맞는지/.test(inquiry[0])||
        !/변경|수정|정정|바꾸|바꿀|바꿔|옮기|조정|연장|단축/.test(inquiry[0]))continue;
    const local=requestIntent("period",own[0]);
    if(local.requested.size!==1||!local.requested.has(field)||local.blocked.has(field)||local.serverOnly.has(field))continue;
    const proof=readExplicitPeriodBoundary(text,field,today,baseMonth);
    if(proof?.value&&!proof.ambiguous&&proof.evidence.length===1)return {field,value:proof.value,evidence:proof.evidence[0]};
  }
}

/** "12/2부터 12/5까지 돼요, 12/4는 안 돼요" → 기간과 빼는 날. 못 읽으면 null.
 *  "12월 2일과 12월 5일만 가능"처럼 날짜만 늘어놓으면 기간이 아니라서 비워요(하루만 말하면 그 하루) */
export function parsePeriod(text: string, today: string, baseMonth?: string | null): PeriodFill | null {
  text = normalizeFullDates(text);
  if (FIX.test(text)) return null;
  const typed = typedPeriodBoundaries(text,today,baseMonth);
  if (typed !== undefined) return typed;
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
const MONEY_END = String.raw`(?=$|[\s,.;!?？。:)]|원|으로|로|[은는이가을를도요]|부터|까지|예요|입니다|쯤|정도)`;
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
  return resolvedIntentSegments("quote",text).some(({part,fields,dependent}) =>
    (!fields.length || fields.includes("amount")) &&
    [...part.matchAll(COMPOUND_MAN)].some((m) => /[천백십]/.test(m[0])) &&
    (dependent || /[?？]|아마|혹시|미정|모르|애매|일까|할까|할지|고민|생각\s*중|검토\s*중|것\s*같|취소|철회|보류|하지\s*마|하지\s*말|안\s*할/.test(part)));
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
  for (const m of text.matchAll(/(\d[\d,]*(?:\.\d+)?)\s*만\s*((?:\d+\s*[천백십]\s*){0,4})(?:(\d[\d,]*)\s*원)?/g)) {
    const base = num(m[1]) * 10000;
    if (!Number.isSafeInteger(Math.round(base)) || Math.abs(base - Math.round(base)) > 1e-6) return undefined;
    let limit = 10000, rest = 0;
    for (const part of m[2].matchAll(/(\d+)\s*([천백십])/g)) {
      const scale = MAN_SCALE[part[2]], value = Number(part[1]) * scale;
      if (scale >= limit || value >= limit) return undefined;
      rest += value;
      limit = scale;
    }
    const tail = m[3] ? num(m[3]) : 0;
    if (tail >= limit || /^\s*\d+\s*[천백십]/.test(text.slice(m.index + m[0].length))) return undefined;
    const won = Math.round(base) + rest + tail;
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
const NOT = new RegExp(String.raw`^\s*(?:은|는|이|가)?\s*(?:안(?!으로|에)\s*\S|못|\(\s*[Xx×]\s*\)|[Xx×](?![A-Za-z]))|^(?:(?!면세|부가|VAT)[^,.\n]){0,8}?(?:${NEG}|없|않|(?:(?<![가-힣])|(?<=[은는이가로]))안\s*(?:돼|됨|되|됩|해|하|함|합|했))`, "i");
/** 바로 뒤에 붙으면 조건이라 고를 수 없는 말("부가세 별도 시 415만 원") */
const IF = /^\s*(?:시|면|하면|이면|라면|일\s*(?:때|경우))(?![가-힣])/;
/** 의미 토큰·필드 소유권·부정/조건을 독립적으로 검사해요.
 *  이미 명시된 세금 절만 값으로 사용하고, 다른 일반 명사의 값은 읽지 않아요. */
export function readVatValue(text:string):{value?:NonNullable<QuoteFill["vat"]>;scopes:string[]}{
  const said = new Set<NonNullable<QuoteFill["vat"]>>();
  const scopes:string[]=[];
  const parts=scopedFieldParts("quote",text,"vat");
  for(const [index,part] of parts.entries()){
    const tokens=scopedVatTokens(part);
    // 현재 상태를 설명한 절과 바로 뒤의 독립 변경 지시는 경쟁 대입이
    // 아니에요. 두 값을 모두 확정하거나 문의하는 구문은 보류해요.
    if(tokens.length===1&&/^(?:현재|지금)\s/.test(part)&&/(?:돼|되어)\s*있는데$/.test(part)&&
        index+1<parts.length&&scopedVatTokens(parts[index+1]).length===1&&
        /(?:변경|수정|정정|바꿔|바꾸|돌려)/.test(parts[index+1])&&
        !requestIntent("quote",parts[index+1]).blocked.has("vat"))continue;
    for(const token of tokens){
      const rest=part.slice(token.end);
      if(IF.test(rest))return {scopes:[]};
      if(!NOT.test(rest)){
        said.add(token.mode);
        scopes.push(part);
      }
    }
  }
  return {value:said.size===1?[...said][0]:undefined,scopes};
}
export function vatEvidenceMatches(value:unknown,evidence:string,text:string,scopes:string[]):boolean{
  const compact=(s:string)=>s.normalize("NFKC").replace(/\s+/g,"");
  const needle=compact(evidence).replace(/[.!。]+$/,"");
  const tokens=vatSemanticTokens(evidence);
  return !!needle&&compact(text).includes(compact(evidence))&&
    scopes.some(scope=>compact(scope).includes(needle)||needle.includes(compact(scope)))&&tokens.some(token=>token.mode===value);
}

/** 유효기간: '유효'에 바로 붙은 날짜만 써요("12/17까지 유효" · "유효기간은 12/17까지"). "12/11까지 공사, 유효기간은 일주일"은 비워요 */
function validityYears(text: string, today: string): string {
  const year = Number(today.slice(0,4));
  return text.normalize("NFKC").replace(
    /(?:내년|다음\s*해|올해|금년|작년|지난\s*해)\s*(?=\d{1,2}\s*(?:월|[/.]))/g,
    word => String(year + (/내년|다음/.test(word) ? 1 : /작년|지난/.test(word) ? -1 : 0)) + "년 ");
}
function validIn(text: string, today: string): string | undefined {
  const normalized = validityYears(text, today);
  const date = "(?<![\\d/.])(" + DATE + ")";
  const label = QUOTE_VALIDITY_LABEL;
  const re = new RegExp(date + "\\s*까지\\s*(?:만\\s*)?(?:" + label + ")|(?:" + label +
    ")\\s*[은는이가을를:]?\\s*" + date + "\\s*까지", "g");
  const fix = new RegExp("^" + FIX_GAP + date + "\\s*까지");
  const hits = [...normalized.matchAll(re)].map(m => (normalized.slice(m.index + m[0].length).match(fix)?.[1] ?? m[1] ?? m[2]).trim());
  // 만료일처럼 날짜 칸을 직접 가리키는 명칭은 명시 월·일을 받아요.
  const direct = new RegExp("(?:" + label + ")\\s*[은는이가을를:]?\\s*(" + FULL_DATE +
    "|\\d{1,2}\\s*(?:월|[/.])\\s*\\d{1,2}\\s*일?)\\s*(?:으로|로)(?!\\s*(?:부터|까지))", "g");
  if (!hits.length) hits.push(...[...normalized.matchAll(direct)].map(m => m[1].trim()));
  const unique = new Set(hits);
  if (unique.size !== 1) return undefined;
  const said = [...unique][0];
  const bare = !/[/.월-]/.test(said);
  const tm = Number(today.slice(5, 7));
  const anchor: Month = { m: tm };
  let d: string | undefined = daysIn(bare ? said.replace(/일/, "") + "일" : said, today, anchor)[0];
  if (anchor.bad) return undefined;
  if (d && bare && d < today) d = iso((tm % 12) + 1, Number(d.slice(8, 10)), today) ?? undefined;
  return d;
}

/** 서로 다른 만료 날짜의 경쟁 지시를 한쪽 날짜로 축약하지 않아요. */
export function readQuoteValidity(text: string, today: string): { value?: string; ambiguous: boolean } {
  const scope = scopedFieldParts("quote", text, "valid").join(", ");
  if (!scope) return { ambiguous: false };
  const normalized = validityYears(scope, today);
  const value = validIn(normalized, today);
  const dates = daysIn(normalized, today, { m: Number(today.slice(5,7)) });
  const competing = new Set(dates).size > 1;
  const correction = !!value && new RegExp(FIX_GAP + DATE + "\\s*까지").test(normalized);
  return { value, ambiguous: competing && !correction };
}

/** 일수의 실제 절만 읽어요. 과거 후보를 말한 정정은 별도 검증 전에는 규칙 후보가 아니에요. */
export type QuoteDuration = { value?: number; ambiguous: boolean; previousOld?: number };

export function readQuoteDuration(text: string): QuoteDuration {
  const scope = scopedFieldParts("quote", text.normalize("NFKC"), "days").join(", ");
  if (!scope) return { ambiguous: false };
  if(quantityDeltaSyntax(scope,"days"))return {ambiguous:true};
  const quantity = classifyDurationQuantity(scope);
  const { tokens } = quantity;
  if (quantity.risk) return { ambiguous: true };
  if (!tokens.length) return { ambiguous: quantity.mentioned };
  if (tokens.length === 1) {
    const token = tokens[0], tail = scope.slice(token.end);
    // 단일 새 수량 뒤의 명시적인 재제안·정정 지시도 확정 값이에요.
    // 앞 수량을 취소하고 다른 수량을 기다리는 표현과는 구별해요.
    const directUpdate = /^\s*(?:으로|로)\s*(?:다시\s*)?(?:제안|정정|수정|변경|확정)\s*(?:해\s*주세요|해\s*주시겠어요|해\s*주실래요|할게요|하겠습니다|합니다|부탁드립니다|부탁해요)\s*[.!?？。]*$/.test(tail);
    if (token.value < 1 || NEG_AFTER.test(tail) || !directUpdate && /아니|정정|→|->|말고|다시/.test(tail.slice(0,12))) {
      return { ambiguous: true };
    }
    return { value: token.value, ambiguous: false };
  }
  if (tokens.length === 2 && tokens[0].value !== tokens[1].value) {
    const [old, next] = tokens;
    const gap = scope.slice(old.end, next.start), tail = scope.slice(next.end);
    if (NEG_AFTER.test(tail)) return { ambiguous: true };
    if (new RegExp("^(?:" + FIX_GAP + ")$").test(gap) ||
      /^\s*에서\s*$/.test(gap) && /^\s*(?:으로|로)\s*(?:변경|수정|정정|확정|바꾸|바꿔|고치|고쳐)/.test(tail)) {
      return { value: next.value, ambiguous: false };
    }
    if (/^\s*(?:이라고|라고|이라|라|으로|로)\s*(?:제가\s*)?(?:했|말했|말씀드렸|제안했|적었|입력했)(?:는데|지만|다가)\s*$/.test(gap) &&
      /^\s*(?:으로|로)\s*(?:변경|수정|정정|확정|바꾸|바꿔|고치|고쳐|고칠)/.test(tail)) {
      return { value: next.value, previousOld: old.value, ambiguous: false };
    }
  }
  return { ambiguous: true };
}
function daysOf(text: string): number | undefined {
  const read = readQuoteDuration(text);
  return read.ambiguous || read.previousOld !== undefined || requestIntent("quote", text).blocked.has("days") ? undefined : read.value;
}

/** "견적 455만 원, 부가세 포함, 공사 3일, 12/17까지 유효" → 견적서 칸 값. 읽은 칸만 돌려줘요 */
export function parseQuote(text: string, today: string): QuoteFill {
  const out: QuoteFill = {};
  const normalized = manOf(text);
  const amount = normalized === null || writtenMoney(text).mentioned || uncertainCompoundAmount(text) ||
    requestIntent("quote", text).blocked.has("amount") ? undefined : amountIn(normalized);
  if (amount) out.amount = amount;
  const vat = readVatValue(text).value;
  if (vat) out.vat = vat;
  const days = daysOf(text);
  if (days) out.days = days;
  const validity = readQuoteValidity(text, today);
  const valid = validity.ambiguous ? undefined : validity.value;
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
  const { value, withoutCrew } = readCrew(text);
  const crew = scopedFieldParts("dates",text,"crew").some(part=>quantityDeltaSyntax(part,"crew"))?undefined:value;
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

/** 명시 일수와 달력 목록을 별도로 읽고 서로 일치할 때만 날짜 후보를 검증해요. */
export function readDateListQuantity(form: "dates" | "period", text: string, today: string, baseMonth?: string | null, holidays: string[] = [], noWeekend = false): { invalid: boolean; dates: string[] } | undefined {
  const month: Month = { m: Number((baseMonth ?? today).slice(5,7)) };
  const scope = scopedFieldParts(form,text,form === "dates" ? "days" : "excluded").map(part => {
    if (form !== "period") return part;
    // 경계와 제외일 수량이 한 문장에 있어도 제외일 목록에 경계를 넣지 않아요.
    const split = part.match(/^(.+?(?:가능하고|잡고|정하고|확정하고|하고))\s*(.+)$/);
    if (!split || !RANGE.test(split[1]) || NO.test(split[1]) || !NO.test(split[2]) || !dateCountTokens(split[2]).length) return part;
    daysIn(split[1],today,month);
    return split[2];
  }).join(", ");
  const tokens = dateCountTokens(scope);
  if (!tokens.length) return undefined;
  const count = new Set(tokens.map(token => token.value));
  const said = tokens.reduceRight((rest, token) => rest.slice(0, token.start) + " ".repeat(token.end-token.start) + rest.slice(token.end),scope);
  const withoutCrew = readCrew(said).withoutCrew;
  const expanded = expandDateList(withoutCrew,today);
  const dates = form === "dates" && RANGE.test(withoutCrew)
    ? parseDates(withoutCrew,today,baseMonth,holidays,noWeekend)?.days ?? []
    : expanded === null || RANGE.test(withoutCrew) ? [] : [...new Set(daysIn(expanded,today,month))].sort();
  return { invalid: count.size !== 1 || month.bad === true || dates.length < 1 || dates.length !== tokens[0].value, dates };
}

/** 값의 독립 검증: 문장 종결어가 아닌 날짜 토큰과 개수만 읽어요.
 * 범위/상대일/중복/불완전 목록은 이 경로로 확인하지 않아요. */
export function calendarEvidenceDates(text: string, today: string, baseMonth?: string | null): string[] | undefined {
  const report=reportedPeriodExclusion(text.replace(/[.!。]+$/,""));
  const normalized = normalizeFullDates(text.normalize("NFKC"));
  if (RANGE.test(normalized) || /오늘|내일|모레|요일|\d{3,}\s*일|\d{3,}\s*월/.test(normalized)) return;
  const counts = dateCountTokens(normalized);
  const literal = counts.reduceRight((rest, token) => rest.slice(0, token.start) +
    " ".repeat(token.end - token.start) + rest.slice(token.end), normalized);
  const expanded = expandDateList(readCrew(literal).withoutCrew, today);
  if (expanded === null) return;
  const month: Month = {m: Number((baseMonth ?? today).slice(5,7))};
  const dates = daysIn(expanded, today, month);
  if (month.bad || !dates.length || new Set(dates).size !== dates.length ||
      report?.count!==undefined&&report.count!==dates.length ||
      counts.some(token => token.value !== dates.length)) return;
  return dates.sort();
}

/** 별도의 명시적 제외일 목록은 기간을 다시 만들지 않고 그 목록만 읽어요.
 * 미확정 수량, 범위, 교체, 중복은 일반 기간 파서로 우회하지 않아요. */
export function readExplicitPeriodExclusions(text: string, today: string, baseMonth?: string | null): { invalid:boolean; dates:string[] } | undefined {
  const scopes = scopedFieldParts("period",text,"excluded");
  if(scopes.length!==1)return;
  const list = explicitPeriodExclusionList(scopes[0]);
  if(!list)return;
  // 달력에 절대 날짜로 정규화할 수 있는 목록에 한정해요.
  if(/오늘|내일|모레/.test(list))return;
  const expanded = expandDateList(normalizeFullDates(list),today);
  const month:Month={m:Number((baseMonth??today).slice(5,7))};
  const dates=expanded===null?[]:daysIn(expanded,today,month);
  const members=list.split(/[,，、·]|하고|이랑|랑|과|와|및|그리고/);
  const unique=[...new Set(dates)].sort();
  const count=reportedPeriodExclusion(scopes[0])?.count;
  return {invalid:expanded===null||!!month.bad||!unique.length||dates.length!==members.length||unique.length!==dates.length||count!==undefined&&count!==unique.length||unique.some(day=>day<=today),dates:unique};
}

/** 별도 제외일 요청 때문에 일반 기간 파서가 실패한 경우에만
 * 독립적으로 명시된 한 경계의 정확한 확정 값을 보존해요. */
export function independentPeriodExclusionBoundary(text:string,today:string,baseMonth?:string|null):{field:"start"|"end";value:string;evidence:string}|undefined{
  const overall=requestIntent("period",text);
  if(overall.cancelAll||!overall.requested.has("excluded"))return;
  const fields=(["start","end"] as const).filter(field=>overall.requested.has(field));
  if(fields.length!==1)return;
  const field=fields[0];
  if(overall.blocked.has(field)||overall.serverOnly.has(field))return;
  const parts=scopedFieldParts("period",text,field);
  if(parts.length!==1)return;
  const evidence=parts[0],clause=normalizeFullDates(evidence);
  const target=explicitPeriodBoundaryTail(clause);
  if(!target||target.field!==field)return;
  const match=PERIOD_BOUNDARY_ASSIGN.exec(target.tail)??PERIOD_BOUNDARY_EDIT.exec(target.tail);
  if(!match)return;
  const month:Month={m:Number((baseMonth??today).slice(5,7))};
  const dates=daysIn(match[1],today,month);
  if(month.bad||dates.length!==1||dates[0]<=today)return;
  return {field,value:dates[0],evidence};
}

const CREW_NUMBER = String.raw`(?:(?<![\d./월-])\d+(?:,\d+)+(?:\.\d+)?|(?<![\d.])\d+(?:\.\d+)?|(?<![가-힣])${COUNTER_WORD})`;
const CREW_UNIT = String.raw`(?:명(?!\d|분|당|씩|단|짜리|치|함|칭)|인(?=$|[\s.,!?;~/∼〜–—－-]|으로|로|[은는을를도가과와만]|부터|에서|입니다|이에요))`;
const CREW_RANGE = new RegExp(String.raw`${CREW_NUMBER}\s*(?:${CREW_UNIT}\s*)?(?:[~/∼〜–—－-]|부터|에서|또는|혹은|아니면|이나|나)\s*${CREW_NUMBER}\s*${CREW_UNIT}|(?<![가-힣])(?:한|두|세|네)\s*(?:한|두|세|네)\s*${CREW_UNIT}`, "g");
const CREW_TERM = new RegExp(String.raw`(${CREW_NUMBER})\s*${CREW_UNIT}`, "g");
const CREW_PREVIOUS = "(?:" + RECENT_REFERENCE_MARK + "\\s*)?";
const CREW_TARGET = /(?:(?:(?:투입(?:할)?|작업)\s*인원|인원(?:\s*수)?|작업자(?:\s*수)?|인력|인부|사람)(?:은|는|만|을|를|이|가|:)?\s*)?/.source;
const CREW_CHANGE = /(?:(?:정정|수정|변경|확정)\s*(?:할게요|하겠습니다|해\s*주세요|해\s*줘요|합니다|해요)|(?:바꿔|고쳐)\s*(?:주세요|줘요|요)|바꿀게요|고칠게요|바꾸겠습니다|고치겠습니다)/.source;
const CREW_REPLACEMENT = new RegExp("^(" + CREW_PREVIOUS + CREW_TARGET + CREW_PREVIOUS + "(" + CREW_NUMBER + ")\\s*(?:(?:" + CREW_UNIT + ")?\\s*에서|" + CREW_UNIT + "\\s*(?:이라고|라고|이라|라|으로|로)\\s*(?:제가\\s*)?(?:했|말했|말씀드렸|적었|입력했|전달했)(?:는데|다가|지만)))\\s*((" + CREW_NUMBER + ")\\s*" + CREW_UNIT + "\\s*(?:으로|로)\\s*" + CREW_CHANGE + ")$");
const CREW_CORRECTION_UNCERTAIN = /[?？]|아마|혹시|미정|보류|검토|취소|철회|고민|가능하면|예정|잠정|모르|애매|정도|쯤|또는|아니면|사이|원래대로|없(?:던|었던)\s*(?:걸로|일로|것으로)|하지\s*마|하지\s*않|라면|다면|으면|이면|하면|경우/;

/** 후보 검증에만 쓰는 인원 정정 관계. 규칙 단독으로 새 값을 적용하지 않아요. */
export function readCrewReplacement(text: string): { old: number; next: number; oldSpan: string; nextSpan: string; oldToken: string; nextToken: string } | undefined {
  const raw = text.normalize("NFKC").trim();
  const clauses = raw.split(/(?<!\d)[,.]|[,.](?!\d)|[;!。\n]|(?<=[?？])/).map(part => part.trim()).filter(Boolean);
  const matches = clauses.map((clause) => clause.trim().match(CREW_REPLACEMENT));
  const found = matches.flatMap((match, i) => match ? [{ match, i }] : []);
  if (found.length !== 1) return undefined;
  const { match, i } = found[0];
  if (CREW_CORRECTION_UNCERTAIN.test(clauses[i])) return undefined;
  // 독립된 날짜의 미정·문의 때문에 인원 정정까지 버리지 않아요.
  // 조건으로 이어지는 절, 인원 경쟁 지시, 대상 없는 위험 표현은
  // 여전히 전체 인원 정정 검증을 실패시켜요.
  for (const [index, part] of clauses.entries()) {
    if (index === i) continue;
    const intent = requestIntent("dates", part);
    if (readCrew(part).mentioned || intent.requested.has("crew")) return undefined;
    if (CREW_CORRECTION_UNCERTAIN.test(part) || /되면|된다면/.test(part)) {
      const independentDate = intent.requested.size === 1 && intent.requested.has("days") &&
        /^(?:(?:공사|작업|시공)\s*)?(?:일정|날짜|일자|일)\s*(?:은|는|이|가|을|를|:)/.test(part) &&
        !/가능하면|조건부|라면|다면|으면|이면|하면|되면|경우/.test(part) &&
        /(?:습니다|입니다|이에요|예요|어요|아요|요|[?？])$/.test(part);
      if (!independentDate) return undefined;
    }
  }
  const count = (value: string) => counterValue(value) ?? (/^[1-9]\d?$/.test(value) ? Number(value) : NaN);
  const old = count(match[2]), next = count(match[4]);
  return Number.isInteger(old) && Number.isInteger(next) && old !== next
    ? { old, next, oldSpan: match[1], nextSpan: match[3], oldToken: match[2], nextToken: match[4] } : undefined;
}

/** 범위 표현을 먼저 걷어내서 '2/3인'의 일부를 날짜나 확정 인원으로 읽지 않는다. */
export function readCrew(text: string): { mentioned: boolean; withoutCrew: string; value: number | undefined } {
  const raw = text.normalize("NFKC");
  const single = raw.replace(CREW_RANGE, " ").replace(/(?<![가-힣])(?:한두|두세|서너|대여섯|예닐곱)\s*(?:명|인)/g, " ");
  let ambiguous = single !== raw || new RegExp(CREW_NUMBER + "\\s*명(?:분|당|씩|단|짜리|치|함|칭)").test(single);
  const counts = new Set<number>();
  const withoutCrew = single.replace(CREW_TERM, (_, raw: string, offset: number) => {
    if (partialQuantityPrefix(single, offset)) ambiguous = true;
    counts.add(counterValue(raw) ?? Number(raw));
    return " ";
  });
  const [n] = counts;
  const final=!ambiguous&&counts.size>1?crewFinalAssignmentBody(raw):undefined;
  const total=final?readCrew(final).value:undefined;
  return { mentioned: ambiguous || counts.size > 0, withoutCrew,
    value: total??(!ambiguous && counts.size === 1 && Number.isInteger(n) && n > 0 && n < 100 ? n : undefined) };
}

export type ExtraFill = QuoteFill & { title?: string; reason?: string };
const WHY = /(?<!견적|계약|문|청구|확인)서\s*요?\s*$|때문/;
/** 항목 이름에서 지울 금액: 만 · 원이 붙은 숫자만("2평 · 3장"은 둬요) */
const AMOUNT_WORD = /(?<![가-힣\d])[일이삼사오육칠팔구십백천만억](?:[ \t]*[일이삼사오육칠팔구십백천만억])*[ \t]*원(?:이|가|은|는)?|\d[\d,]*(?:\.\d+)?\s*억\s*(?:[\d,]*\s*만)?\s*(?:[\d,]*\s*원)?(?:이|가|은|는)?|\d[\d,]*(?:\.\d+)?\s*만\s*(?:\d\s*천)?\s*(?:[\d,]*\s*원)?(?:이|가|은|는)?|\d[\d,]*\s*원(?:이|가|은|는)?/g;
/** 명시적인 제목 지시가 있으면 앞쪽의 '사유 말고'를 제목으로 읽지 않아요. */
const EXTRA_TITLE_PREFIX = /^(?:추가금|추가금액)?(?:사유|이유)[은는을를]?(?:말고|대신)$/;
const EXTRA_ASSIGN_PARTICLE = /(?:이라고|라고|으로|로)/.source;
// 값을 가리키는 조사부터 완결된 대입 술어까지 한 경계로 읽어요.
// '새로'의 마지막 '로'만 조사처럼 잘라 값에 '으로 새'를 남기지 않아요.
const EXTRA_ASSIGN_MODIFIER = /(?:새로이|새롭게|새로|다시|재차|먼저|우선|각각|각기|최종(?:적으로)?|정확히|정확하게|명확히|명확하게|정식으로|한\s*번\s*더)/.source;
const EXTRA_MODIFIER_WORDS = ["새로이","새롭게","새로","다시","재차","먼저","우선","각각","각기","최종","최종적으로","정확히","정확하게","명확히","명확하게","정식으로","한 번 더"];
const EXTRA_PARTIAL_MODIFIER = new RegExp(EXTRA_ASSIGN_PARTICLE + "\\s*(?:" +
  [...new Set(EXTRA_MODIFIER_WORDS.flatMap(word => Array.from({length:word.length},(_,i)=>word.slice(0,i+1).trim())))]
    .map(word=>word.replace(/\s+/g,"\\s*")).join("|") + ")\\s*$");
const EXTRA_POLITE_END = /(?:주시겠어요|주실래요|주시겠습니까|주실\s*수\s*있(?:을까요|나요))/.source;
const EXTRA_REQUEST_END = "(?:주세요|줘요|주십시오|" + EXTRA_POLITE_END + ")";
const EXTRA_ASSIGN_ENDING = "(?:해\\s*" + EXTRA_REQUEST_END + "|" + /합니다|해요|할게요|하겠습니다|하고요?|하며|바랍니다|부탁(?:합니다|드립니다|드려요|해요)/.source + ")";
const EXTRA_ASSIGN_STEM = /(?:제안|확정|변경|수정|정정|입력|기재|작성|기록|설정|반영|등록|선택|결정)/.source;
const EXTRA_OTHER_ASSIGN_STEM = /(?:지정|조정|표기|표시|명시|교체|대체|저장|사용|채택|갱신|정|기입|처리)/.source;
const EXTRA_ASSIGN_PREFIX = EXTRA_ASSIGN_PARTICLE + "\\s*(?:" + EXTRA_ASSIGN_MODIFIER + "\\s*){0,3}";
// 관형형/명사화된 대입 동사 + 요청 대상 + 완결된 요청 술어를
// 각각 확인해요. 일반 명사구의 후보/초안 자체는 지시문이 아니에요.
const EXTRA_ANY_ASSIGN_STEM = "(?:" + EXTRA_ASSIGN_STEM + "|" + EXTRA_OTHER_ASSIGN_STEM + ")";
const EXTRA_ASSIGN_ADNOMINAL = EXTRA_ANY_ASSIGN_STEM + "(?:할|하는|하기\\s*위한)";
const EXTRA_PROPOSAL_NOUN = /(?:초안|후보(?:안)?|제안(?:안)?|안)/.source;
const EXTRA_NAMED_PROPOSAL = EXTRA_ANY_ASSIGN_STEM + "안(?:\\s*[을를])?";
const EXTRA_PROPOSAL_OBJECT = EXTRA_PROPOSAL_NOUN + "(?:\\s*[을를])?";
const EXTRA_PROPOSAL_REQUEST_END = "(?:" + EXTRA_REQUEST_END + "|줘|주시고)";
const EXTRA_PROPOSAL_REQUEST = "(?:(?:부탁|요청)\\s*(?:합니다|드립니다|드려요|해요|해\\s*" + EXTRA_PROPOSAL_REQUEST_END + ")|" + EXTRA_PROPOSAL_REQUEST_END + "|바랍니다|(?:해|만들어|제시해|제안해|보여)\\s*" + EXTRA_PROPOSAL_REQUEST_END + ")";
const EXTRA_ADNOMINAL_PROPOSAL = EXTRA_ASSIGN_ADNOMINAL + "\\s+" + EXTRA_PROPOSAL_OBJECT;
const EXTRA_OBJECT_FIRST_PROPOSAL = EXTRA_PROPOSAL_NOUN + "(?:\\s*(?:[을를]|으로|로)\\s*|\\s+)" + EXTRA_ANY_ASSIGN_STEM + "(?:\\s*[을를])?";
const EXTRA_PROPOSAL_HEAD = "(?:" + EXTRA_ADNOMINAL_PROPOSAL + "|" + EXTRA_OBJECT_FIRST_PROPOSAL + ")";
const EXTRA_PROPOSAL_ASSIGN = EXTRA_PROPOSAL_HEAD + "\\s*" + EXTRA_PROPOSAL_REQUEST;
const EXTRA_TEXT_CHANGE = new RegExp(EXTRA_ASSIGN_PREFIX + "(?:" +
  EXTRA_PROPOSAL_ASSIGN + "|" + EXTRA_PROPOSAL_OBJECT + "\\s*(?:잡아|만들어|작성해|보여|내어|내)\\s*" + EXTRA_REQUEST_END + "|" +
  EXTRA_NAMED_PROPOSAL + "\\s*(?:(?:먼저|우선)\\s*)?(?:보여\\s*" + EXTRA_REQUEST_END + "|확인(?:합니다|하겠습니다|할게요|해\\s*" + EXTRA_REQUEST_END + "))|" +
  EXTRA_ASSIGN_STEM + "(?:\\s*" + EXTRA_ASSIGN_ENDING + ")?|" +
  EXTRA_OTHER_ASSIGN_STEM + "\\s*" + EXTRA_ASSIGN_ENDING + "|" +
  /(?:바꿔|고쳐|적어|넣어|써|해|정해|남겨)\s*/.source + EXTRA_REQUEST_END + "|" + /정합니다|정하겠습니다|정할게요|바꿀게요|고칠게요|바꾸겠습니다|고치겠습니다|바꿉니다|고칩니다|바꾸죠|고치죠|하겠습니다|할게요|적고|쓰고|기입하고/.source + ")$");
const EXTRA_OBJECT_CHANGE = new RegExp("(?:을|를)\\s*(?:넣어|적어|써|기재해|입력해|남겨)\\s*"+EXTRA_REQUEST_END+"$");
const EXTRA_INCOMPLETE_PROPOSAL = new RegExp(EXTRA_ASSIGN_PREFIX + "(?:" + EXTRA_PROPOSAL_HEAD + "(?=\\s|$|[?？은는이가도만])|" + EXTRA_NAMED_PROPOSAL + "(?=\\s|$|[?？은는이가도만])|" + EXTRA_ASSIGN_ADNOMINAL + "\\s*$)");
const EXTRA_LINKED_SCALAR = /(?<![가-힣a-zA-Z\d])(?:(?:추가|공사|시공)\s*)?(?:금액|비용|공사비|추가금(?:액)?|부가세|세금|VAT)(?=\s|[은는이가을를:=])/gi;
const EXTRA_LINK_END = /(?:하고요?|하며|적고|쓰고|기입하고|주시고)$/;
const EXTRA_MODIFIED_ASSIGNMENT = new RegExp(EXTRA_ASSIGN_PARTICLE + "\\s*" + EXTRA_ASSIGN_MODIFIER);
export function hasExtraAssignmentSuffix(value: string): boolean {
  const text = value.trim().replace(/[.!。?？]+$/, "");
  return EXTRA_TEXT_CHANGE.test(text) || EXTRA_OBJECT_CHANGE.test(text) || EXTRA_INCOMPLETE_PROPOSAL.test(text);
}
const EXTRA_TEXT_DIRECTIVE = /(?:기재|작성|기록|설정|반영|확정|지정|조정|제안|결정|선택)\s*(?:해|하|할)|넣어|넣으|써\s*(?:주|줘)|(?:으로|로)\s*(?:하겠|할게|해\s*(?:주|줘)|정하|정할|정해\s*(?:주|줘))/;
// 구두점 없이 금액·세금이 이어지면 문자열 값의 일부인지 별도 필드인지 확정하지 않아요.
const EXTRA_TEXT_NEIGHBOR = /(?:^|\s)(?:(?:추가\s*(?:금액|비용|금)|금액|비용|총액)\s*(?:\d|[일이삼사오육칠팔구십백천만억]+\s*원)|(?:부가(?:가치)?세|세금|VAT)\s*(?:포함|별도|면세))/i;
const EXTRA_TITLE_UNCERTAIN = /[?？]|아마|혹시|미정|모르|애매|일까|할까|고민|예정|생각\s*중|검토\s*중|것\s*같|가능하면|조건부|(?:라면|다면|으면|이면|하면|되면)|경우|(?:바꾸|고치|수정|변경|정정)려고|또는|아니면/;
// 이전 내용을 가리키는 표현 자체를 새 항목 이름으로 쓰지 않아요.
const EXTRA_TITLE_REFERENCE = /^(?:(?:방금|아까|앞서|좀\s*전에|조금\s*전에|이전에|지난번에|그때|전에|처음(?:에)?|최초(?:에)?)\s*(?:말씀드린|말한|얘기한|알려드린|정한|잡은|확정한|제안한|정리한|적은|전달한)|(?:이전|직전|앞서|기존|(?:맨\s*)?처음(?:에)?|최초(?:에)?|현재|지금)\s*(?:입력\s*값|후보|제안|내용|값|작업명|항목명|제목|사유|이유)(?:를|을|으로|로|대로|와|과|\s|$)|(?:현재|지금)\s*(?:있는|적힌|적은|입력한|입력된|등록한|등록된)\s*값)/;
export function isExtraReferenceValue(value: string): boolean { return EXTRA_TITLE_REFERENCE.test(value.trim()); }
const EXTRA_TITLE_DENIED = /(?:그대로|(?<![가-힣])유지|바꾸지|고치지|수정하지|변경하지|건드리지|손대지|삭제|지우|비우|없애|취소|하지\s*마|주지\s*마|말아\s*주|(?<![가-힣])안\s*(?:할|해|하|바꿔|고쳐|수정|변경)|아니|아닙|말고|대신)/;
const EXTRA_OTHER_FIELD = /(?:사유|이유|금액|비용|부가세|VAT)\s*(?::|=|은|는|을|를)/i;
const EXTRA_NOT_FREE_TITLE = /^(?:(?:이번|해당)\s*)?(?:추가금|추가\s*(?:(?:작업|공사|시공)\s*)?(?:금액|비용|요금|사유|이유)|금액|비용|가격|공사비|총액|견적|사유|이유|부가세|부가가치세|세금|VAT|면세|(?:포함|별도)\s*(?:부가세|세금)|제목|항목명|항목|작업명|이름)(?:\s|은|는|이|가|을|를|만|도|:|=|$)/i;
function unquoteExtraValue(value: string): string {
  return [["'","'"],["\"","\""],["“","”"],["‘","’"]].some(([left,right])=>value.startsWith(left)&&value.endsWith(right))
    ? value.slice(1,-1).trim() : value;
}
const EXTRA_CLAUSE_BOUNDARY = /[,，;.!?！？。\n]/;
function extraTextAction(clause: string) {
  const masked=maskExtraQuotes(clause);
  return masked.match(EXTRA_TEXT_CHANGE)??masked.match(EXTRA_OBJECT_CHANGE);
}
// An explicitly owned scalar assignment does not own an unlabelled text
// value in its explanatory prefix. A separate nominal clause and an actual
// title/reason label are different owners, not part of this exclusion.
function explicitExtraScalarAssignment(clause: string): boolean {
  const masked=maskExtraQuotes(clause);
  return [...masked.matchAll(EXTRA_LINKED_SCALAR)].some(hit=>
    !!extraTextAction(masked.slice(hit.index)));
}

function extraOperationParts(text:string):string[] {
  const masked=maskExtraQuotes(text), result:string[]=[];
  let start=0;
  while(start<text.length){
    const boundary=extraTextBoundary(text.slice(start));
    const end=boundary<0?text.length:start+boundary;
    const clause=text.slice(start,end).trim();
    if(clause)result.push(clause);
    if(end===text.length)break;
    const next=end+(extraScalarBoundary(masked,end)?1:0);
    if(next<=start)break;
    start=next;
  }
  return result;
}

// A completed report/explanation before another field's assignment is not
// an independent free-text nominal value. Standalone nouns are not reports.
const EXTRA_BACKGROUND_PREDICATE = /(?:해서|하여|라서|어서|아서|으므로|므로|때문에|인데|건데|했는데|되었는데|됐는데|지만|했습니다|했어요|되었습니다|됐어요|입니다|이에요|예요|네요|군요|습니다|어요|아요)$/;
const EXTRA_BACKGROUND_REPORT = /(?:했습니다|했어요|되었습니다|됐어요|입니다|이에요|예요|네요|군요|습니다|어요|아요)$/;

export function foreignExtraTextScope(text: string, field: "title" | "reason", evidence: string, value?: unknown): boolean {
  const compact=(s:string)=>s.normalize("NFKC").replace(/\s+/g, "").replace(/[,.!;。]+$/, "");
  const proof=compact(evidence), literal=typeof value==="string"?compact(value):"";
  if(!proof)return false;
  const parts=extraOperationParts(text);
  const matching=parts.map((clause,index)=>({clause,index})).filter(({clause})=>{
    const actual=compact(clause);
    return !!actual&&(actual.includes(proof)||literal&&proof.includes(literal)&&actual.includes(literal));
  });
  return matching.length>0&&matching.every(({clause,index})=>{
    const marks=extraTextTargets(clause);
    const otherAssignment=(part:string)=>explicitExtraScalarAssignment(part)||
      extraTextTargets(part).some(mark=>mark.field!==field&&!!extraTextAction(part));
    return !marks.some(mark=>mark.field===field)&&
      (marks.some(mark=>mark.field!==field)||explicitExtraScalarAssignment(clause)||unownedExtraDependentClause(text,clause)||
       EXTRA_BACKGROUND_PREDICATE.test(clause)&&parts.slice(index+1).some(otherAssignment)||
       EXTRA_BACKGROUND_REPORT.test(clause)&&parts.slice(0,index).some(otherAssignment));
  });
}
function extraPoliteAssignment(clause: string): boolean {
  return new RegExp(EXTRA_POLITE_END + "$").test(clause.trim()) && !!extraTextAction(clause.trim());
}
// 완결된 연결형 대입 다음의 명시적 필드만 별도 절로 분리해요.
// 인용값 속 라벨이나 조건을 별도의 지시로 만들지 않아요.
function extraTextBoundary(text: string): number {
  const masked = maskExtraQuotes(text);
  let punct = -1;
  for (let i=0;i<masked.length;i++) if (extraScalarBoundary(masked,i)) { punct=i; break; }
  const indices = [...extraTextTargets(text).map(hit=>hit.index), ...[...masked.matchAll(EXTRA_LINKED_SCALAR)].map(hit=>hit.index)].sort((a,b)=>a-b);
  // 공통 절 판정에서 다른 칸의 완결된 취소/유지 또는 독립된 미정
  // 술어를 확인한 경우 원래 문자열 위치를 경계로 사용해요.
  const resolved=resolvedIntentSegments("extra",masked);
  const first=resolved[0], second=resolved[1];
  const intent=first&&second?requestIntent("extra",masked):undefined;
  if(first&&second&&first.sentence===second.sentence&&!first.dependent&&!second.dependent&&
      first.fields.length>0&&second.fields.length>0&&
      second.fields.every(field=>!first.fields.includes(field))&&
      (first.fields.every(field=>intent?.canceled.has(field)||intent?.currentKeep.has(field))||
       first.fields.every(field=>intent?.serverOnly.has(field)&&!intent.blocked.has(field))&&
       /(?:돌려놓고|되돌리고|복원하고)$/.test(first.part)&&second.fields.every(field=>!intent?.blocked.has(field))||
       first.fields.length===1&&intent?.blocked.has(first.fields[0])&&/(?:고요?|며)$/.test(first.part)&&
       !intent.cancelAll&&!intent.unboundWithdrawal&&second.fields.every(field=>!intent.blocked.has(field)))){
    // 복합 명칭의 일부를 다시 검색하지 않고 공통 절의 실제 시작 위치를
    // 대조해요. 정규화로 원문 위치가 달라진 경우는 기존 경로에 맡겨요.
    const at=masked.indexOf(second.part);
    if(at>0&&(punct<0||at<punct)&&masked.slice(0,at).trim().normalize("NFKC")===first.part)return at;
  }
  for (const index of indices) {
    if (punct >= 0 && index >= punct) break;
    const prefix = masked.slice(0,index).trimEnd();
    if (EXTRA_LINK_END.test(prefix) && EXTRA_TEXT_CHANGE.test(prefix)) return index;
  }
  return punct;
}
function incompleteExtraAssignment(clause: string): boolean {
  const masked = maskExtraQuotes(clause);
  const trimmed = clause.trim();
  const quoteEnd = ({'"':'"',"'":"'","“":"”","‘":"’"} as Record<string,string>)[trimmed[0]];
  if (quoteEnd) {
    const end = trimmed.indexOf(quoteEnd,1);
    if (end < 0) return true;
    const tail = trimmed.slice(end+1).trim();
    if (tail && extraTextAction(tail)?.index !== 0) return true;
  }
  return !extraTextAction(clause) && (EXTRA_MODIFIED_ASSIGNMENT.test(masked) || EXTRA_PARTIAL_MODIFIER.test(masked) ||
    EXTRA_INCOMPLETE_PROPOSAL.test(masked));
}
// 문법이 지원하는 완전한 대입 절 또는 명시적인 단순 명사 값만
// 독립 증명으로 사용해요. 미지원 설명/보고를 값으로 단정하지 않아요.
const EXTRA_UNPARSED_NARRATIVE = /(?:으로|로|이라고|라고)\s*(?:후보|초안|먼저|잡아|올려|띄워|내\s*주)|(?:입니다|이에요|예요|이요|했어요|합니다|하겠습니다|주세요|줘요|바랍니다|부탁드립니다)$/;
/** 명시적 다른 칸의 지시문이 제목 문자열로 돌아오는 경우를 거부해요. */
export function hasExtraOtherFieldLabel(value: string): boolean {
  return /^(?:(?:이번|해당)\s*)?(?:추가\s*(?:(?:작업|공사|시공)\s*)?(?:금액|비용|요금|사유|이유)|추가금(?:액)?|금액|비용|사유|이유|부가세|세금)\s*(?:은|는|이|가|을|를|:|=)(?:\s|$)/.test(value.trim()) ||
    extraTextTargets(value).some(hit=>hit.field==="reason");
}
export function hasExtraTextLabelPrefix(value: string): boolean {
  return extraTextTargets(value.trim()).some(hit=>hit.index===0);
}
// 금액을 지운 뒤 남는 과거 보고·정정의 조사나 서술어는 새 작업명이 아니에요.
const EXTRA_AMOUNT_NARRATIVE = /^(?:(?:아까|앞서|방금|이전에|지난번에|좀\s*전에)\s*)?(?:이라고|라고|이라\s*했|라\s*했|이었|였|으로(?:\s|$)|로(?:\s|$)|입니다|이에요|이요|정확히는|제가\s*잘못|제가\s*계산)/;

type ExtraTextSpan = { field: "title" | "reason"; value: string; clause: string; start: number; end: number };
type ExtraTextClause = { target: ExtraTextTarget; clause: string; tail: string; offset: number; boundary: string; span?: ExtraTextSpan; incomplete: boolean };
const EXTRA_FIELD_LEAD = /^(?:(?:그리고|또|다음으로|다만)\s+)?(?:(?:이|그|해당|이번)\s+)?$/;
function descriptiveTextLead(prefix:string,field:"title"|"reason"):boolean {
  // A completed descriptive lead may identify the reason/name label, but
  // it must not contain another field, risk, competing request or value.
  if(!prefix||extraTextTargets(prefix).length||EXTRA_TITLE_UNCERTAIN.test(prefix)||
    EXTRA_TITLE_DENIED.test(prefix)||EXTRA_TEXT_DIRECTIVE.test(prefix)||/[?？"'“”‘’\d]/.test(prefix))return false;
  return /(?:지만|인데|건데|해서|라서|때문에)$/.test(prefix)||
    /(?:보고서|도면|기록|내역|자료|확인\s*내용)\s*에\s*맞(?:춰|추어)$/.test(prefix)||
    field==="reason"&&/(?:하게\s*된|발생한|발생하게\s*된|필요한|필요하게\s*된|들어간|들어가게\s*된|진행한|진행하게\s*된|추가한)$/.test(prefix);
}
const EXTRA_CLEAR_REQUEST = /(?:(?:지워|비워|없애)(?:\s*(?:주세요|줘요|주십시오|주시겠어요|둘게요)|서\s*[^,;.!?！？。]{0,32}(?:두세요|둬\s*주세요|주세요|줘요))|(?:삭제|제거|초기화)\s*(?:해\s*(?:주세요|줘요|주십시오)|합니다|하겠습니다|할게요))\s*[.!。]?$/;
export function isExtraClearCommandValue(value: string): boolean {
  return EXTRA_CLEAR_REQUEST.test(value.trim());
}
// 쉼표로 나열한 독립된 두 필드가 같은 문장의 확정 술어를 공유할
// 때만 생략된 방향/인용 조사를 분리해요. 통로·회로 같은 명사는 보존해요.
function sharedExtraNominalTail(text: string, end: number, field: "title" | "reason", tail: string): string {
  if(!/[,，]/.test(text[end]??""))return tail;
  const rest=text.slice(end+1), nextEnd=extraTextBoundary(rest);
  const next=nextEnd<0?rest:rest.slice(0,nextEnd), marks=extraTextTargets(next);
  if(marks.length!==1||marks[0].field===field||next.slice(0,marks[0].index).trim())return tail;
  const nextTail=next.slice(marks[0].valueStart).trim(), nextAction=extraTextAction(nextTail);
  if(!nextAction || EXTRA_TITLE_UNCERTAIN.test(maskExtraQuotes(nextTail)) || EXTRA_TITLE_DENIED.test(maskExtraQuotes(nextTail)) ||
      nextEnd>=0&&/[?？]/.test(rest[nextEnd])&&!extraPoliteAssignment(nextTail))return tail;
  const particle=/(?:이라고|라고|으로|로)$/.exec(tail);
  if(!particle)return tail;
  const before=tail.slice(0,particle.index).trimEnd();
  if(!before)return tail;
  if(unquoteExtraValue(before)!==before)return before;
  const last=before.at(-1)!;
  if(!/[가-힣]/.test(last))return tail;
  const final=(last.charCodeAt(0)-0xac00)%28;
  const expected=particle[0]==="이라고"||particle[0]==="라고"?(final?"이라고":"라고"):(final!==0&&final!==8?"으로":"로");
  if(expected!==particle[0]||before.split(/\s+/).at(-1)!.length<2&&!/\s/.test(tail[particle.index-1]??""))return tail;
  return before;
}

/** Shared predicate proof for a nominal text slot and a following independently
 * owned scalar slot. The existing text-only grammar handles two text slots;
 * this helper does not alter the offline parser or its extracted rule value. */
export function sharedScalarTextValue(text: string, field: "title" | "reason", clause: string,
    value: string, following: string): boolean {
  const at = text.indexOf(clause);
  if (at < 0 || text.indexOf(clause,at+clause.length) >= 0) return false;
  const between = text.slice(at+clause.length);
  if (!/^\s*[,，]\s*/.test(between) ||
      !between.replace(/^\s*[,，]\s*/,"").startsWith(following)) return false;
  const marks = extraTextTargets(clause);
  if (marks.length !== 1 || marks[0].field !== field || clause.slice(0,marks[0].index).trim()) return false;
  const tail = clause.slice(marks[0].valueStart).trim();
  if (!value || value !== value.trim() || !tail.startsWith(value)) return false;
  const particle = tail.slice(value.length);
  if (!/^(?:으로|로|이라고|라고)$/.test(particle)) return false;
  const final = value.at(-1);
  if (!final || !/[가-힣]/.test(final)) return false;
  const batchim = (final.charCodeAt(0)-0xac00)%28;
  const expected = /라고$/.test(particle) ? (batchim?"이라고":"라고") : (batchim!==0&&batchim!==8?"으로":"로");
  return particle === expected && value.split(/\s+/).at(-1)!.length >= 2 &&
    !EXTRA_TITLE_UNCERTAIN.test(maskExtraQuotes(tail)) && !EXTRA_TITLE_DENIED.test(maskExtraQuotes(tail)) &&
    !incompleteExtraAssignment(tail) && !isExtraScopedReference(clause) && !isExtraHistoricalReport(tail);
}
// 라벨과 실제 조사 뒤의 값 구간을 한 번 계산해요. 규칙, 근거 검사,
// 다른 칸의 의도 판정이 동일한 시작/끝 인덱스를 사용해요.
function extraTextClauses(text: string): ExtraTextClause[] {
  const masked=maskExtraQuotes(text), clauses: ExtraTextClause[]=[];
  let start=0;
  while(start<text.length){
    const boundary=extraTextBoundary(text.slice(start));
    const end=boundary<0?text.length:start+boundary;
    const raw=text.slice(start,end), marks=extraTextTargets(raw);
    const target=marks[0], mark=masked[end]??"";
    if(target){
      const prefix=raw.slice(0,target.index).trim(), originalTail=raw.slice(target.valueStart).trimEnd();
      const tail=extraTextAction(originalTail)?originalTail:sharedExtraNominalTail(text,end,target.field,originalTail);
      const scope=maskExtraQuotes(tail), action=extraTextAction(tail);
      const field=target.field;
      const entry:ExtraTextClause={target,clause:raw.trim(),tail,offset:start,boundary:mark,incomplete:incompleteExtraAssignment(tail)};
      const validPrefix=EXTRA_FIELD_LEAD.test(prefix) || field==="title"&&EXTRA_TITLE_PREFIX.test(prefix.replace(/\s+/g,""))||
        !!action&&descriptiveTextLead(prefix,field);
      // 독립 절을 분리한 뒤에도 공통 의미 판정이 미정/보류한
      // 술어는 대입 동사 없는 명사 값으로 다시 가리지 않아요.
      const unresolved=!action&&requestIntent("extra",maskExtraQuotes(raw)).blocked.has(field);
      // 인접한 또 다른 명시 라벨/수량이 있는 미분리 절은 명사 값으로
      // 단정하지 않아요. 단순 명사에 들어간 필드 단어는 값으로 유지해요.
      if(validPrefix && marks.length===1 && tail && !entry.incomplete && !unresolved &&
        (target.marker.trim()!=="" || !!action) &&
        (!/[?？]/.test(mark)||extraPoliteAssignment(tail)) &&
        !EXTRA_TITLE_UNCERTAIN.test(scope) && !EXTRA_TITLE_DENIED.test(scope) && !EXTRA_CLEAR_REQUEST.test(scope) &&
        !EXTRA_TITLE_REFERENCE.test(scope) && !isExtraScopedReference(raw) && !isExtraHistoricalReport(tail) && !EXTRA_OTHER_FIELD.test(scope) && !EXTRA_TEXT_NEIGHBOR.test(scope) &&
        (action || !(/바꿔|바꾸|고쳐|고치|변경|수정|정정|입력|적어|기재/.test(scope) || EXTRA_TEXT_DIRECTIVE.test(scope) || EXTRA_UNPARSED_NARRATIVE.test(scope)))){
        const body=(action?tail.slice(0,action.index):tail).trim();
        const value=unquoteExtraValue(body);
        if(value && value.length<=(field==="title"?60:200) && (field!=="title"||!(/^\d/.test(value)))){
          const at=start+target.valueStart+tail.indexOf(body);
          entry.span={field,value,clause:raw.trim(),start:at,end:at+body.length};
        }
      }
      clauses.push(entry);
      // 같은 절 안에서 분리하지 못한 두 명시 대상도 보류 대상이에요.
      for(const other of marks.slice(1))clauses.push({target:other,clause:raw.trim(),tail:raw.slice(other.valueStart).trimEnd(),offset:start,boundary:mark,incomplete:false});
    }
    start=end+(end===text.length||extraScalarBoundary(masked,end)?1:0);
  }
  // A subject-marked evaluation of the present label is an observation,
  // not a second proposed name. Require an independently complete later
  // assignment; do not discard bare competing names, quoted values or risks.
  return clauses.filter((entry,index)=>!(
    /^(?:이|가)$/.test(entry.target.marker.trim()) && /[.!。]/.test(entry.boundary) &&
    /^(?:너무|다소|조금|좀|상당히|매우)\s+/.test(entry.tail) &&
    /(?:적이|스럽|하|길|짧|넓|좁|어렵|쉽|많|적)(?:네요|군요|습니다|어요|아요)$/.test(entry.tail) &&
    !extraTextAction(entry.tail) && !/["'“”‘’]|\d|취소|철회|아니라|말고|조건부|가능하면/.test(entry.tail) &&
    clauses.slice(index+1).some(later=>later.target.field===entry.target.field&&
      !!later.span&&!!extraTextAction(later.tail))));
}

function targetedExtraText(text: string, field: "title" | "reason"): {targeted:boolean;value?:string}{
  const clauses=extraTextClauses(text).filter(entry=>entry.target.field===field);
  if(!clauses.length)return {targeted:false};
  if(clauses.length!==1 || !clauses[0].span)return {targeted:true};
  // 같은 칸 또는 대상 없는 뒤 철회는 새 규칙 후보를 만들지 않아요.
  const entry=clauses[0], later=maskExtraQuotes(text.slice(entry.span!.end)).split(EXTRA_CLAUSE_BOUNDARY).slice(1);
  if(later.some(part=>scopedWithdrawalAffects("extra",part,field)))return {targeted:true};
  return {targeted:true,value:entry.span!.value};
}

export type ExtraTextGrounding = { values: { value: string; clause: string }[]; incomplete: boolean };

/** 실제 요청의 완전한 값 경계를 대조해요. 서버가 반환한 근거 조각에서
 * 값을 다시 추출하지 않아요. 복수의 독립된 대입은 충돌 검증용으로만 보존해요. */
export function extraTextGrounding(text: string, field: "title" | "reason"): ExtraTextGrounding | undefined {
  const clauses=extraTextClauses(text).filter(entry=>entry.target.field===field);
  if(clauses.some(entry=>entry.incomplete))return {values:[],incomplete:true};
  const values=clauses.flatMap(entry=>entry.span?[{value:entry.span.value,clause:entry.span.clause}]:[]);
  return values.length>0 && values.length===clauses.length?{values,incomplete:false}:undefined;
}

/** Positive proof for a fresh text candidate. Unlabelled legacy shorthand
 * is permitted only when the actual independent local parser extracts the
 * same value, rather than accepting arbitrary server text on missing proof. */
export function positiveExtraTextValue(text:string,field:"title"|"reason",value:unknown,evidence:string,today:string):boolean {
  if(typeof value!=="string"||!value.trim()||!evidence.trim()||foreignExtraTextScope(text,field,evidence,value))return false;
  const compact=(s:string)=>s.normalize("NFKC").replace(/\s+/g,"").replace(/[,.!;。]+$/,"");
  const proof=compact(evidence);
  if(!proof||!compact(text).includes(proof)||!proof.includes(compact(value)))return false;
  const grounding=extraTextGrounding(text,field);
  if(grounding){
    if(grounding.incomplete)return false;
    if(grounding.values.some(item=>compact(item.value)===compact(value)&&
      (compact(item.clause).includes(proof)||proof.includes(compact(item.clause)))))return true;
    // An owned nominal ending with a direction particle may share the next
    // scalar's complete predicate. The existing proof checks the exact noun
    // and grammatical particle, rather than accepting a shorter substring.
    const scopes=resolvedIntentSegments("extra",text);
    const owned=scopes.filter(s=>s.fields.includes(field));
    if(grounding.values.length!==1||owned.length!==1||owned[0].dependent||owned[0].fields.length!==1||
        !compact(owned[0].part).includes(proof))return false;
    const following=scopes[scopes.indexOf(owned[0])+1];
    return !!following&&following.sentence===owned[0].sentence&&!following.dependent&&following.fields.length===1&&
      (following.fields[0]==="amount"||following.fields[0]==="vat")&&!!extraTextAction(following.part)&&
      !operationScopeRisk("extra",following.part,following.fields[0])&&
      sharedScalarTextValue(text,field,owned[0].part,value,following.part);
  }
  if(extraTextTargets(text).some(target=>target.field===field))return false;
  const local=parseExtra(text,today)[field];
  return typeof local==="string"&&compact(local)===compact(value)&&
    extraOperationParts(text).some(part=>compact(part).includes(proof)||proof.includes(compact(part))&&compact(part).includes(compact(value)));
}

export function extraTextClearRequested(text: string, field: "title" | "reason"): boolean {
  const clauses=extraTextClauses(text).filter(entry=>entry.target.field===field);
  return clauses.some(entry=>EXTRA_CLEAR_REQUEST.test(maskExtraQuotes(entry.tail))) && !clauses.some(entry=>entry.span);
}

function extraScalarBoundary(text: string, at: number): boolean {
  if (!EXTRA_CLAUSE_BOUNDARY.test(text[at])) return false;
  if (text[at] === "," && /\d/.test(text[at-1] ?? "") && /^\d{3}(?!\d)/.test(text.slice(at+1))) return false;
  if (text[at] === "." && /\d/.test(text[at-1] ?? "") && /\d/.test(text[at+1] ?? "")) return false;
  return true;
}

/** 명시적 제목/사유 바로 뒤의 인용값만 다른 칸의 해석에서 분리해요.
 * 금액 자체를 인용한 경우에는 원래 숫자와 근거를 유지해요. */
export function extraTextScopes(text: string): { intentText: string; scalarText: string } {
  let intentText = text;
  const marks=extraTextTargets(text);
  for (const quote of text.matchAll(/"[^"\r\n]*"|'[^'\r\n]*'|“[^”\r\n]*”|‘[^’\r\n]*’/g)) {
    const at = quote.index;
    const bound = marks.some(hit=>hit.valueStart===at);
    if (bound) intentText = intentText.slice(0,at) + "\uE000".repeat(quote[0].length) + intentText.slice(at+quote[0].length);
  }
  // 확정된 전체 명사 값 내부의 필드 단어만 가려요. 조건/이력/미완결
  // 절 및 이웃의 명시 수량은 위의 구간 증명을 통과하지 않아요.
  for(const entry of extraTextClauses(text)){
    if(!entry.span)continue;
    const {start,end}=entry.span;
    intentText=intentText.slice(0,start)+"\uE000".repeat(end-start)+intentText.slice(end);
  }
  // 견적 파서는 제목/사유 라벨을 모르는 만큼, 명시적으로 독립된
  // 문자열 전용 절만 숫자 위험 검사에서 제외해요. 연결 조건과
  // 같은 절의 금액/세금 및 무대상 위험 표현은 남겨요.
  const bounds = maskExtraQuotes(intentText);
  let scalarText = "", start = 0;
  while(start<bounds.length) {
    const boundary=extraTextBoundary(bounds.slice(start));
    const end=boundary<0?bounds.length:start+boundary;
    const part = intentText.slice(start,end);
    const fields = requestIntent("extra",part).requested;
    const typedText = extraTextTargets(part).length>0 || /항목명|작업명|공사명|제목|항목|이름|사유|이유/.test(part);
    const connectedCondition = /(?:라면|다면|으면|이면|하면|되면|경우)\s*$/.test(part);
    const independent = typedText && (fields.has("title") || fields.has("reason")) &&
      !fields.has("amount") && !fields.has("vat") && !connectedCondition;
    scalarText += independent ? " ".repeat(part.length) : part;
    if(end===bounds.length)break;
    const punct=extraScalarBoundary(bounds,end);
    if(punct) scalarText += independent&&/[?？]/.test(bounds[end])?" ":intentText[end];
    start=end+(punct?1:0);
  }
  return { intentText, scalarText };
}

/** 추가 금액: "곰팡이 방지 처리 12만 원, 부가세 포함, 벽 안쪽까지 곰팡이가 퍼져서" → 항목 · 금액 · 부가세 · 이유 */
export function parseExtra(text: string, today: string): ExtraFill {
  const { intentText, scalarText } = extraTextScopes(text);
  const out: ExtraFill = parseQuote(scalarText, today);
  if (requestIntent("extra",intentText).blocked.has("amount")) delete out.amount;
  delete out.days;
  delete out.valid;
  const explicit = targetedExtraText(text,"title");
  const reason = targetedExtraText(text,"reason");
  if (explicit.value) out.title = explicit.value;
  if (reason.value) out.reason = reason.value;
  for (const raw of extraOperationParts(intentText)) {
    const part = raw.trim();
    if (!part) continue;
    // 다른 칸에 귀속된 철회/철회 보고를 자유 형식 항목명이나
    // 사유로 재해석하지 않아요. 명시 문자열의 증명은 위 경로로 유지해요.
    if(resolvedIntentSegments("extra",part).some(scope=>
      scope.fields.some(field=>field==="amount"||field==="vat")&&
      !scope.fields.some(field=>field==="title"||field==="reason")&&
      /취소|철회|없(?:던|었던)\s*(?:걸로|일로|것으로)/.test(scope.part)))continue;
    if(!extraTextTargets(part).length&&explicitExtraScalarAssignment(part))continue;
    if (!out.reason && !reason.targeted && WHY.test(part)) {
      if(!foreignExtraTextScope(text,"reason",part,part))out.reason = part.replace(/[.。]\s*$/, "");
      continue;
    }
    if (!out.title && !explicit.targeted && !extraTextTargets(part).length && !standaloneVatTokens(part).length && !EXTRA_NOT_FREE_TITLE.test(part) &&
      !EXTRA_TITLE_UNCERTAIN.test(part)) {
      // 정정한 뒤 말은 항목 이름이 아니에요("곰팡이 방지 12만 원이 아니라 15만 원")
      const head = (manOf(part) ?? part).split(/아니라|아니고|말고|부가세|부가가치세|VAT|면세/i)[0];
      const withoutAmount = head.replace(AMOUNT_WORD, "");
      const t = withoutAmount.replace(/\s*(?:추가요|추가돼요|더\s*들어요)\s*$/, "").replace(/\s+/g, " ").trim();
      if (head !== withoutAmount && EXTRA_AMOUNT_NARRATIVE.test(t)) continue;
      if (/^(?:아|앗|아니|네|예|응|음|어|잠깐|알겠습니다|확인했습니다)[.!。]?$/.test(t)) continue;
      if (t && !/^\d/.test(t) && !EXTRA_NOT_FREE_TITLE.test(t) && !EXTRA_TITLE_DENIED.test(t) && !EXTRA_TITLE_REFERENCE.test(t)&&
          !foreignExtraTextScope(text,"title",part,t)) out.title = t.slice(0, 60);
    }
  }
  return out;
}
