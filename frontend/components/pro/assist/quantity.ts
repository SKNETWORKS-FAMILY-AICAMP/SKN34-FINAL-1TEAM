// 정확한 고유어 일수 토큰만 읽어요. 날짜와 공사 일수의 범위를 구분하는 데도 써요.
const DAY_NAMES: Record<string, number> = {
  하루: 1, 이틀: 2, 사흘: 3, 나흘: 4, 닷새: 5, 엿새: 6,
  이레: 7, 여드레: 8, 아흐레: 9, 열흘: 10,
  열하루: 11, 열이틀: 12, 열사흘: 13, 열나흘: 14,
  보름: 15, 열닷새: 15, 열엿새: 16, 열이레: 17, 열여드레: 18, 열아흐레: 19,
};
const ONE: Record<string, number> = { 한: 1, 두: 2, 세: 3, 네: 4, 다섯: 5, 여섯: 6, 일곱: 7, 여덟: 8, 아홉: 9 };
const TEN: Record<string, number> = { 열: 10, 스물: 20, 서른: 30, 마흔: 40, 쉰: 50, 예순: 60, 일흔: 70, 여든: 80, 아흔: 90 };
const words = (table: Record<string, number>) => Object.keys(table).sort((a,b) => b.length-a.length).join("|");
export const NATIVE_DAY_WORD = "(?:열[ \\t]*(?:" + words(Object.fromEntries(Object.entries(DAY_NAMES).filter(([, value]) => value < 10))) + ")|" + words(DAY_NAMES) + ")";
export const COUNTER_WORD = "(?:(?:" + words(TEN) + ")[ \\t]*(?:" + words(ONE) + ")|" + words(TEN) + "|스무|" + words(ONE) + ")";
export function counterValue(raw: string): number | undefined {
  const compact = raw.replace(/[ \t]+/g, "");
  if (compact === "스무") return 20;
  if (ONE[compact] !== undefined) return ONE[compact];
  if (TEN[compact] !== undefined) return TEN[compact];
  for (const [ten,value] of Object.entries(TEN)) {
    if (compact.startsWith(ten) && ONE[compact.slice(ten.length)] !== undefined) return value + ONE[compact.slice(ten.length)];
  }
  return undefined;
}
const DAY_END = /(?=$|[\s,.!?？;:。~∼〜–—－+/ -]|으로|로|은|는|이|가|을|를|만|도|인데|이고|아니|라고|라|간|동안|까지|부터|에서|정도|쯤|가량|내외|안팎|이내|이상|이하|이나|나|하고|과|와|랑|걸|소요|예요|이에요|입니다|예정)/.source;
const DAY_TAIL = new RegExp("^" + DAY_END);
const NON_QUANTITY = /^\s*(?:째|전(?:에|부터|까지|$)|후(?:에|부터|까지|$)|뒤(?:에|부터|까지|$)|당|씩|치|분량|요일|짜리|날짜)|^(?:자(?:로|는|가|를|$)|정(?!도))/;
const CALENDAR_PREFIX = /(?:\d+\s*(?:월|년)|(?:이번|다음|지난|매)\s*(?:달|월|해|년))\s*$/;
const PARTIAL_PREFIX = new RegExp("(?:\\d+|(?<![가-힣])(?:" + COUNTER_WORD + "|[십백천만억]|서|대))\\s+$");
const SIGN_PREFIX = /(?:^|[^\d])[+\-−]\s*$/;
const CALENDAR = /(?<!\d)(?:\d{4}\s*(?:년|[-/.])\s*)?\d{1,2}\s*(?:월|[-/.])\s*\d{1,2}\s*일?/g;
export type QuantityToken = { value: number; start: number; end: number; native: boolean };

export const QUOTE_DURATION_LABEL = /(?:공사|작업|시공)\s*(?:소요\s*(?:일수|기간|일)|일수|기간)|소요\s*(?:일수|기간|일)|기간|일수|공기/.source;
export const QUOTE_VALIDITY_LABEL = /(?:(?:견적(?:서)?\s*)?(?:유효\s*(?:기간|기한|일자|날짜|일)?|만료\s*(?:일자|날짜|일)?|효력(?:\s*(?:기간|기한|일자|날짜|일))?)|견적(?:서)?\s*마감\s*(?:기한|일자|날짜|일))/.source;
// 불완전 수량도 완전한 토큰과 같은 시작 경계에서 검사해요.
// '제안한 일수'의 '한'이나 단위가 아닌 명사 '일수'는 수량이 아니에요.
const NATIVE_BOUNDARY = "(?<![가-힣\\d])";
// 필드명과 붙여 쓴 수사도 위험 검사에는 남기되, 기존처럼
// 분명한 단어 경계를 확인하기 전에는 정확한 수량으로 만들지 않아요.
const NATIVE_START = "(?:" + NATIVE_BOUNDARY + "|(?<=(?:" + QUOTE_DURATION_LABEL + "|공사|작업|시공)(?:[은는이가을를에:])?))";
const NUMERIC_START = "(?<![\\d.])(?<!\\d,)";
const COUNTER_DAY = COUNTER_WORD + "[ \\t]*(?:날|일(?!수))";
// 일반 근삿수사는 실제 시간 단위 또는 단독 수량 경계에서만
// 일수 위험으로 판단해요. '한두 번', '두세 차례'는 다른 수량이에요.
const APPROX_COUNTER_END = "(?=\\s*(?:(?:날|일(?!수)|시간|분|주|개월|달|년)" + DAY_END +
  "|$|[.,!?！？;。:~∼〜–—－+/\\-]|(?:으로|로|은|는|이|가|을|를|만|도)(?=$|[\\s,.!?！？;。])|" +
  "입니다|이에요|예요|이고|정도|가량|쯤|내외|안팎|이내|이상|이하|미만|초과))";
const APPROX_DAY_WORD = "(?:(?:한두|두세|서너|대여섯|예닐곱)" + APPROX_COUNTER_END +
  "|하루\\s*이틀|이틀\\s*사흘|사흘\\s*나흘|사나흘|이삼일|삼사일)";
export const DURATION_WORD = "(?:" + NUMERIC_START + "[+\\-−]?\\d+(?:[.,]\\d+)*\\s*일|" +
  NATIVE_START + "(?:" + APPROX_DAY_WORD + "|" + NATIVE_DAY_WORD + "|" + COUNTER_DAY + "|반나절))";
const DURATION_LEXEME = new RegExp(DURATION_WORD, "g");
const DURATION_CONTEXT = new RegExp("(?:" + QUOTE_DURATION_LABEL + "|(?:공사|작업|시공)(?=\\s*(?:은|는|이|가|을|를|에|:)?\\s*" + DURATION_WORD + "))");
const APPROX = /최대|최소|대략|(?<![가-힣])약\s*|적어도|기껏해야|정도|가량|쯤|내외|안팎|이내|이상|이하|미만|초과|범위|사이|(?<![가-힣])안으로|반(?=$|[\s,.!?]|으로|로|은|는|이|가)/;
const COMPOUND_RANGE = new RegExp(NATIVE_START + "(?:" + APPROX_DAY_WORD + "|(?:" + words(ONE) + ")\\s*(?:" + words(ONE) + ")\\s*(?:날|일(?!수)))");
const RANGE_LINK = /[~∼〜–—－/+-]|부터|까지|또는|혹은|아니면|이나|나|랑|과|와/;
const MALFORMED = /(?<![\d.])(?:[+−-]\s*\d+|\d+(?:[.,]\d+)+|\d{4,})\s*일|반나절/;

export function partialQuantityPrefix(text: string, start: number): boolean {
  return PARTIAL_PREFIX.test(text.slice(0,start)) || SIGN_PREFIX.test(text.slice(0,start));
}

type DurationQuantity = { tokens: QuantityToken[]; mentioned: boolean; risk: boolean };

/** 원문 수량의 존재, 완전한 토큰, 위험을 같은 어휘 경계에서 분류해요. */
export function classifyDurationQuantity(text: string): DurationQuantity {
  const calendar = [...text.matchAll(CALENDAR)].map(m => [m.index,m.index+m[0].length]);
  const tokens: QuantityToken[] = [];
  const lexemes = [...text.matchAll(DURATION_LEXEME)];
  let incomplete = false;
  for (const hit of lexemes) {
    const start = hit.index, end = start + hit[0].length;
    const prefix = text.slice(0,start), tail = text.slice(end);
    if (CALENDAR_PREFIX.test(prefix) || calendar.some(([a,b]) => start < b && end > a)) continue;
    const prior = calendar.filter(([,b]) => b <= start).at(-1)?.[1];
    if (prior !== undefined && !/^\s*(?:간|동안)/.test(tail) &&
      /^[\s,과와랑및부터까지~∼〜–—－-]*$/.test(text.slice(prior,start))) continue;
    // 달력과 다른 명사는 제외하지만 실제 잘린/불완전한 수량은
    // 다른 정확한 수량이 함께 있어도 위험으로 남겨요.
    const number = /^(\d{1,3})\s*일$/.exec(hit[0]);
    const name = DAY_NAMES[hit[0].replace(/[ \t]+/g,"")];
    const counted = hit[0].endsWith("날") || hit[0].endsWith("일") && /^\s*(?:간|동안)/.test(tail)
      ? counterValue(hit[0].replace(/\s*(?:날|일)$/,"")) : undefined;
    const value = number ? Number(number[1]) : name ?? counted;
    if (value === undefined || !number && /[가-힣\d]$/.test(prefix) || partialQuantityPrefix(text,start) || NON_QUANTITY.test(tail) ||
      !DAY_TAIL.test(tail)) {
      incomplete = true;
      continue;
    }
    tokens.push({value,start,end,native: !number});
  }
  const withoutCalendar = text.replace(CALENDAR, " ");
  const risk = incomplete || APPROX.test(withoutCalendar) || COMPOUND_RANGE.test(withoutCalendar) ||
    MALFORMED.test(withoutCalendar) || /(?<!\d)\d+(?:[.,]\d+)+\s*일\s*(?:간|동안|걸|소요)/.test(text) ||
    tokens.some(token => /^\s*(?:[~∼〜–—－/+-]|까지|부터|안으로|또는|혹은|아니면|이나|나(?=\s)|(?:\d+(?:[.,]\d+)?|한|두|세|네)\s*(?:시간|분|주|개월))/.test(text.slice(token.end))) ||
    tokens.some((token,i) => i > 0 && RANGE_LINK.test(text.slice(tokens[i-1].end,token.start)) &&
      !/아니(?:고|라|구)|말고/.test(text.slice(tokens[i-1].end,token.start)));
  return {tokens,mentioned:lexemes.length>0,risk};
}

/** 완전한 토큰과 원문 위치를 반환해요. 달력 날짜와 잘린 수사는 읽지 않아요. */
export function durationTokens(text: string): QuantityToken[] {
  return classifyDurationQuantity(text).tokens;
}

/** 날짜 및 다른 폼에서 수량 자체를 근거로 날짜를 합성하지 않아요. */
export function nativeDayTokens(text: string): QuantityToken[] {
  return durationTokens(text).filter(token => token.native);
}

export function quoteDurationMention(text: string): boolean {
  return DURATION_CONTEXT.test(text) ||
    new RegExp("(?<![가-힣\\d])" + NATIVE_DAY_WORD + "(?=\\s*(?:간|동안|걸|소요|(?:으로|로)\\s*(?:잡|정|확정|설정|바꾸|고치)))").test(text) ||
    /(?<![\d./])\d+\s*일\s*(?:간|동안|걸|소요|작업|정도)/.test(text);
}

/** 실제 기간 절의 범위·부분 수량·다른 시간 단위는 정확한 일수로 만들지 않아요. */
export function durationQuantityRisk(text: string): boolean {
  return classifyDurationQuantity(text).risk;
}

// A relative quantity is an operation operand, never an absolute assignment.
// Keep lexical recognition independent of the current value so every caller
// can fail closed when the baseline is unavailable or ambiguous.
export type QuantityDelta = Readonly<{
  direction: 1 | -1;
  operand?: number;
  base?: number;
  explicitCurrent: boolean;
  priorReference: boolean;
}>;
const DELTA_ADD = /^(?:더\s*)?(?:늘(?:려|리)|연장|증(?:가|원)|추가|붙여|더해)/;
const DELTA_SUB = /^(?:더\s*)?(?:줄(?:여|이)|단축|감(?:소|원)|빼)/;
const DELTA_FOLLOW = /^\s*(?:만\s*)?(?!(?:으로|로|이라고|라고))/;
const DELTA_CURRENT = /(?:현재|지금|기존|등록된|입력된|저장된)\s*(?:작업\s*|공사\s*|투입\s*)?(?:일수|소요일|기간|공기|인원|인력|값|수량)(?:에서|에|보다|은|는|이|가|을|를)?/;
const DELTA_PRIOR = /아까|방금|앞서|이전|직전|지난번|제안|후보/;
const DELTA_AMBIGUOUS = /(?:약\s*|정도|쯤|가량|내외|이상|이하|최대|최소|부터|까지|또는|혹은|아니면|미정|보류|검토\s*중|조건부|만약|가능하면|하지\s*(?:마|말|않|못))/;
function crewDeltaTokens(text:string):QuantityToken[] {
  const pattern=new RegExp(String.raw`(?<![가-힣\d.])([1-9]\d?|${COUNTER_WORD})\s*(?:명(?!\d|분|당|씩|단|짜리|치|함|칭)|인(?=$|[\s,.;!?]|으로|로|까지))`,"g");
  return [...text.matchAll(pattern)].map(hit=>({value:counterValue(hit[1])??Number(hit[1]),start:hit.index,end:hit.index+hit[0].length,native:/[가-힣]/.test(hit[1])}));
}
export function quantityDeltaSyntax(part:string,field:"days"|"crew"):QuantityDelta|undefined {
  const text=part.normalize("NFKC");
  const duration=field==="days"?classifyDurationQuantity(text):undefined;
  const tokens=duration?.tokens??crewDeltaTokens(text);
  const hits=tokens.flatMap(token=>{
    const tail=text.slice(token.end);
    const gap=DELTA_FOLLOW.exec(tail);
    if(!gap)return [];
    const predicate=tail.slice(gap[0].length);
    const add=DELTA_ADD.test(predicate),sub=DELTA_SUB.test(predicate);
    return add||sub?[{token,direction:(add?1:-1) as 1|-1}]:[];
  });
  if(!hits.length)return;
  // Existing explicit final-total relation is validated separately against
  // both the actual baseline and declared final total.
  if(field==="crew"&&tokens.length===2&&/총\s*|합계\s*|모두\s*/.test(text.slice(hits[0].token.end)))return;
  const {token,direction}=hits[0];
  const prefix=text.slice(0,token.start);
  const explicitCurrent=DELTA_CURRENT.test(prefix);
  const priorReference=DELTA_PRIOR.test(prefix);
  const baseToken=tokens.length===2&&tokens[0]!==token&&
    /^\s*(?:에서|보다|에)\s*$/.test(text.slice(tokens[0].end,token.start))?tokens[0]:undefined;
  const safe=hits.length===1&&(tokens.length===1||!!baseToken)&&
    !duration?.risk&&!DELTA_AMBIGUOUS.test(text)&&!partialQuantityPrefix(text,token.start)&&
    Number.isSafeInteger(token.value)&&token.value>0;
  return {direction,operand:safe?token.value:undefined,base:baseToken?.value,explicitCurrent,priorReference};
}
