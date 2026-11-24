import { amountRiskText } from "./requestIntent";

// 명시적인 원 단위 한글 정수 금액만 서버와 같은 문법으로 확인해요.
const DIGIT: Record<string, number> = Object.fromEntries([..."일이삼사오육칠팔구"].map((c, i) => [c, i + 1]));
const SMALL: Record<string, number> = { 천: 1000, 백: 100, 십: 10 };
const MAJOR: Record<string, number> = { 억: 100_000_000, 만: 10_000 };
const RUN = /(?<![가-힣0-9])(?<![0-9][.,])(?!공사(?:\s|$)|[0-9]+\s*일\s)(?<body>[일이삼사오육칠팔구영공십백천만억조경0-9](?:\s*[일이삼사오육칠팔구영공십백천만억조경0-9]|[,.](?=[0-9]))*)(?<won>\s*원)?/g;
const WRITTEN_DIGIT = /[일이삼사오육칠팔구영공조경]/;
const HANGUL_RUN = /[일이삼사오육칠팔구영공십백천만억]+/g;
const ATTACHED_UNIT = /[십백천]|[일이삼사오육칠팔구영공십백천][만억]/;
const ATTACHED_LABEL = /(?:견적|금액|가격|비용|공사비|총액|추가금(?:액)?|합계)[은는이가을를]/g;
const HANGUL_NUMBER_CHARS = "일이삼사오육칠팔구영공십백천만억";
const ASCII_DIGITS = "0123456789";
const ASCII_NUMBER_CHARS = ASCII_DIGITS + ",.";
const OTHER_WRITTEN = /[일이삼사오육칠팔구영공십백천만억조경]\s*원/;
const SPLIT_NUMBER = /[일이삼사오육칠팔구영공십백천만억0-9]\s*[,./·:점]\s*$/;
const RANGE_SEPARATORS = [..."~∼〜–—－−/.,·:-", "부터", "에서", "또는", "혹은", "아니면", "점"];
const END = /^(?:$|[\s,.;!?？。:)]|으로|로|이에요|예요|입니다|이요|이네요|이죠|이고|이며|이라고|라고|[은는이가을를도만](?=$|\s|[,.;!?？。]))/;
const MONEY_TARGET = /(?:견적|금액|가격|비용|공사비|총액|추가금(?:액)?|합계|계약금|잔금)\s*(?:[은는이가을를:=]\s*)?$/;
const SHORT_OMITTED_END = /^(?:$|[,.;!?？。]|으로|로|입니다|이에요|예요|요)/;
const OTHER_MONEY = /[0-9]\s*[억천백만원]|(?:견적|금액|가격|비용|공사비|총액|추가금(?:액)?|합계|단가|청구)\s*(?:[은는이가을를:=]\s*)?[0-9]|[0-9](?=$|\s|[,.;!?])/;
const UNCERTAIN = /[?？~∼〜–—－−]|아마|혹시|미정|모르|애매|일까|할까|할지|거죠|까요|또는|아니면|혹은|사이|정도|쯤|약\s|대략|내외|안팎|가량|언저리|고민|생각\s*중|검토\s*중|몇|취소|철회|보류|않|아니|아닙|말고|대신|지\s*(?:마|말|않|못)|원하지|거부|금지|안\s*(?:할|해|하|넣|적|맞)|최대|최소|미만|초과|이상|이하|이내|한도|라면|이면|조건|가능하면|경우|마이너스|음수|플러스|잘못|틀렸|지난|이전|기존|예전|과거|원래|(?:말했|말씀드렸|정했|적었|썼|입력했|했)(?:는데|지만|다가)/;
const RANGE = /부터|까지|에서|[+/<>-]/;
const BREAK = /[,;.!?？。]/;

function skipChars(text: string, pos: number, chars: string): number {
  while (pos < text.length && chars.includes(text[pos])) pos++;
  return pos;
}

function skipSpaces(text: string, pos: number): number {
  while (pos < text.length && /\s/.test(text[pos])) pos++;
  return pos;
}

function attachedEnd(text: string, pos: number): boolean {
  pos = skipChars(text, pos, " \t");
  return pos < text.length && (ASCII_DIGITS + "원").includes(text[pos]);
}

function writtenRangeEnd(text: string, pos: number): boolean {
  pos = skipSpaces(text, pos);
  const separator = RANGE_SEPARATORS.find((s) => text.startsWith(s, pos));
  if (separator === undefined) return false;
  pos = skipSpaces(text, pos + separator.length);
  if (pos >= text.length || !ASCII_DIGITS.includes(text[pos])) return false;
  pos = skipSpaces(text, skipChars(text, pos, ASCII_NUMBER_CHARS));
  return pos < text.length && "억천백십만원".includes(text[pos]);
}

function attachedLabelNumber(text: string, pos: number): boolean {
  let end = skipChars(text, pos, HANGUL_NUMBER_CHARS);
  if (end === pos) return false;
  end = skipSpaces(text, end);
  return end < text.length && ASCII_DIGITS.includes(text[end]);
}

// 숫자 묶음을 한 번씩 읽고 끝 위치에서만 검사해 반복 탐색을 피한다.
function attachedOrMixed(text: string, includeRange = false): boolean {
  for (const match of text.matchAll(HANGUL_RUN)) {
    const start = match.index, end = start + match[0].length;
    const body = match[0], before = text[start - 1] ?? "";
    const suffix = /[가-힣]/.test(before) ? body : body.slice(1);
    if (ATTACHED_UNIT.test(suffix) && attachedEnd(text, end)) return true;
    if (includeRange && !/[가-힣0-9]/.test(before) && !/[만억]/.test(body) &&
        writtenRangeEnd(text, end)) return true;
  }
  for (const label of text.matchAll(ATTACHED_LABEL)) {
    if (attachedLabelNumber(text, label.index + label[0].length)) return true;
  }
  return false;
}

function coefficient(body: string): number | undefined {
  let total = 0, pending = 0, limit = 10_000;
  for (const char of body) {
    if (DIGIT[char]) {
      if (pending) return undefined;
      pending = DIGIT[char];
    } else if (SMALL[char]) {
      const scale = SMALL[char];
      if (scale >= limit) return undefined;
      total += (pending || 1) * scale;
      pending = 0;
      limit = scale;
    } else return undefined;
  }
  total += pending;
  return total > 0 && total < 10_000 ? total : undefined;
}

function valueOf(body: string): number | undefined {
  let total = 0, start = 0, limit = 1_000_000_000;
  for (let i = 0; i < body.length; i++) {
    const scale = MAJOR[body[i]];
    if (!scale) continue;
    const n = coefficient(body.slice(start, i));
    if (n === undefined || scale >= limit) return undefined;
    total += n * scale;
    start = i + 1;
    limit = scale;
  }
  if (start < body.length) {
    const tail = coefficient(body.slice(start));
    if (tail === undefined) return undefined;
    total += tail;
  }
  return total > 0 && total <= 1_000_000_000 ? total : undefined;
}

/** 언급을 발견했지만 정규화하지 못하면 금액 후보를 모두 보류해요. */
export function writtenMoney(text: string): { mentioned: boolean; amount?: number } {
  const raw = text.normalize("NFKC").trim().replace(/\s+/g, " ");
  const mentions: { match: RegExpMatchArray; body: string }[] = [];
  let omittedShort = false;
  for (const match of raw.matchAll(RUN)) {
    let body = match.groups!.body.replace(/\s+/g, "");
    const suffix = raw.slice(match.index + match[0].length);
    if (!match.groups!.won && suffix) {
      if (/[억만천백십]이$/.test(match.groups!.body) ||
        (/^[가-힣]/.test(suffix) && /[억만천백십]\s+[일이삼사오육칠팔구영공조경]$/.test(match.groups!.body))) body = body.slice(0, -1);
    }
    const written = WRITTEN_DIGIT.test(body) || "십백천만억".includes(body[0]) || /[억만][십백천만억]/.test(body);
    if (!written) continue;
    if (!match.groups!.won) {
      if (!/[만억]/.test(body)) continue;
      if (["만", "일만", "이만", "오만"].includes(body) &&
        !(MONEY_TARGET.test(raw.slice(0, match.index)) && SHORT_OMITTED_END.test(suffix))) {
        omittedShort ||= SHORT_OMITTED_END.test(suffix);
        continue;
      }
    }
    mentions.push({ match, body });
  }
  const held = { mentioned: true };
  if (!mentions.length) return attachedOrMixed(raw, true) ? held : { mentioned: false };
  if (mentions.length !== 1 || omittedShort || UNCERTAIN.test(amountRiskText(raw))) return held;
  const [{ match, body }] = mentions;
  if (match.index === undefined) return held;
  const before = raw.slice(0, match.index);
  const after = raw.slice(match.index + match[0].length);
  if (!match.groups!.won || !END.test(after) || /[+\-−/]$/.test(before)) return held;
  const outside = before + " " + after;
  if (OTHER_MONEY.test(outside) || attachedOrMixed(outside) || OTHER_WRITTEN.test(outside)) return held;
  if (SPLIT_NUMBER.test(before) || /(?:^|\s)(?:한|두|세|네|두세|서너)\s*$/.test(before)) return held;
  if (body.length === 1 && match.groups!.won === "원" && !MONEY_TARGET.test(before)) return held;
  const clause = before.split(BREAK).at(-1)! + match[0] + after.split(BREAK)[0];
  if (RANGE.test(clause)) return held;
  const amount = valueOf(body);
  return amount === undefined ? held : { mentioned: true, amount };
}
