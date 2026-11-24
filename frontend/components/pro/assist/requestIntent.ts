import type { AssistForm } from "./protocol";

// 값 추출과 변경 의도를 분리해 규칙·서버 후보에 같은 안전 기준을 적용해요.
const MONEY = /\d\s*[억천백만원]|\d{6,}|\d{1,3}(?:,\d{3})+|[일이삼사오육칠팔구영공십백천만억조경]+\s*원|[십백천][일이삼사오육칠팔구십백천\s]*만|[삼사육칠팔구]\s*만/;
const CREW = /(?:\d|한|두|세|네|다섯|여섯|일곱|여덟|아홉|열)\s*(?:명|인(?=$|[\s.,!?;~/∼〜–—－-]|으로|[은을도과]|부터|에서|입니다|이에요))/;
const DATE = /\d\s*(?:일|월|\/)|\d{4}[-.]\d{1,2}[-.]\d{1,2}|오늘|내일|모레|이번\s*주|다음\s*주|요일/;
const RISK = /[?？]|아마|혹시|미정|모르겠|애매|(?:할|일|될|였|했|맞을)까(?!지)|까요|였나|고민|생각\s*(?:중|하고)|검토\s*중|것\s*같|수도\s*(?:있|없)|가능하면|조건부|(?:한다|된다|라|이|하|되|으)면|경우|잠정|또는|혹은|아니면|보류/;
const NEGATIVE = /(?:넣|적|입력하|확정하|정정하|수정하|변경하|바꾸|고치|옮기|미루|당기|보내|추가하|유지하|하)지\s*(?:마|말|않|못)|말아\s*주|(?<![가-힣])안\s*(?:할|하겠|넣|적|바꿀|고칠)|원하지|거부/;
const CANCEL = /취소|철회|없던\s*걸로|원래대로/;
const CANCEL_END = /(?:취소|철회)\s*(?:해\s*(?:주세요|줘요|주십시오)|할게요|하겠습니다|합니다|해요|할래요)?[.!。]?$|없던\s*걸로\s*(?:해\s*주세요|할게요|하겠습니다)?[.!。]?$/;
const KEEP = /그대로|유지|건드리지|손대지|놔두|남겨\s*두|내버려\s*두/;
const CURRENT = /(?:현재|지금)\s*(?:폼|값|입력|금액|인원|일정|기간|부가세|제목|항목|사유|이유|날짜|작업일|제외일)|직접\s*(?:입력|수정|변경|고쳤|고친)/;
const PRIOR = /이전|앞서|아까|방금|직전|지난번|전에|제안|변경안/;
const BOUNDS = /[~∼〜～–—－−]|(?:\d|만|원|명|인)\s*[-/]\s*(?:\d|[한두세네])|최대|최소|미만|초과|이상|이하|이내|한도|정도|쯤|내외|안팎|가량|대략|약\s|부터[^,.!?]{0,30}까지/;
const MOVE = /옮겨|옮기|옮길|미뤄|미루|당겨|당기|대신|말고|→|->|(?:일|날)\s*(?:만|을|를)?\s*에서/;
const EXCLUDED = /제외|휴무|쉬는\s*(?:날|걸|것)|빼는\s*날|작업\s*불가|안\s*되는\s*날/;
const UNAVAILABLE = /못\s*(?:가|갑|갈|하|합)|안\s*(?:돼|되|됩|될)|불가|어려|힘들|곤란|쉽니다|쉬어요|빼\s*(?:줘|주|주세요)|빼고/;
const START = /시작(?:일|\s*날짜)?|첫\s*(?:작업일|공사일|날)|착공/;
const END = /종료(?:일|\s*날짜)?|마감(?:일|\s*날짜)?|끝나는\s*날|마지막\s*(?:날짜|날)|끝\s*날|준공/;
const PERIOD = /기간|부터|까지|[~∼〜～]/;
const POLITE_DIRECTIVE = /(?:주|주시)(?:겠어요|겠습니까|겠나요)[?？]?$/;
const CLEAR = /(?:삭제|지우|비우|빼)\s*(?:해|하|어|워|주|주세요|줘)|없애|초기화/;
const CONDITIONAL = /가능하면|조건부|(?:라면|다면|으면|이면|하면)|경우|혹시|아마/;
const REASSIGN = /(?:취소|철회)(?:하|해)?고/;

function clauses(text: string): string[] {
  return text.normalize("NFKC").split(/(?<!\d),|,(?!\d{3}(?!\d))|(?<!\d)\.|\.(?!\d)|[;!\n。]|(?<=[?？])/)
    .map((part) => part.trim()).filter(Boolean);
}

function mentioned(form: AssistForm, text: string): string[] {
  const fields = new Set<string>();
  if (form === "quote" || form === "extra") {
    if (/부가(?:가치)?세|VAT|면세|세금/i.test(text)) fields.add("vat");
    if (MONEY.test(text) || /견적\s*(?:금액|가)|(?:추가\s*)?금액|총액|합계|공사비|가격|추가\s*(?:비용|금)|비용/.test(text) ||
      /견적\s*(?:[은는이가을를:]\s*)?(?:\d|그대로|유지|취소|미정)/.test(text)) fields.add("amount");
    if (form === "quote") {
      if (/유효\s*기간|유효|만료/.test(text)) fields.add("valid");
      if (/(?:공사|작업|시공)\s*(?:기간|일수)|(?<!유효\s*)기간|일수|공기|\d+\s*일\s*(?:간|동안|걸|소요)|(?:공사|작업|시공)\s*(?:은|는|이|가)?\s*\d+\s*일/.test(text)) fields.add("days");
    } else {
      if (/항목(?:명)?|제목|작업명|공사명|이름/.test(text)) fields.add("title");
      if (/사유|이유|때문|퍼져서|발견해서/.test(text)) fields.add("reason");
    }
  } else if (form === "dates") {
    if (CREW.test(text) || /인원(?:수)?|작업자|인부/.test(text)) fields.add("crew");
    if (DATE.test(text) || /작업\s*일|공사\s*일|날짜|일정/.test(text)) fields.add("days");
  } else {
    const excluded = EXCLUDED.test(text) || DATE.test(text) && UNAVAILABLE.test(text) && !START.test(text) && !END.test(text);
    if (excluded) fields.add("excluded");
    if (START.test(text)) fields.add("start");
    if (END.test(text)) fields.add("end");
    if (!fields.size && (DATE.test(text) || PERIOD.test(text)) ||
      !excluded && PERIOD.test(text) && !START.test(text) && !END.test(text)) {
      fields.add("start");
      fields.add("end");
    }
  }
  return [...fields];
}

// 구두점 없이 독립된 필드가 이어져도 앞의 숫자와 뒤의 보류를 섞지 않아요.
function parts(form: AssistForm, clause: string): string[] {
  const targets = form === "quote"
    ? /견적\s*금액|유효\s*기간|공사\s*기간|작업\s*기간|시공\s*기간|공사\s*일수|부가가치세|부가세|VAT|금액|총액|합계|기간|일수|공기/gi
    : form === "extra"
      ? /추가\s*금액|추가\s*비용|부가가치세|부가세|VAT|항목명|작업명|공사\s*제목|제목|항목|사유|이유|금액|총액/gi
      : form === "dates"
        ? /작업\s*인원|작업자|인원수|인원|작업\s*일정|공사\s*날짜|작업\s*날짜|작업일|공사일|일정|날짜/g
        : /시작일|종료일|마감\s*날짜|마감일|끝나는\s*날|마지막\s*(?:날짜|날)|첫\s*(?:작업일|공사일|날)|착공일?|준공일?|제외\s*(?:날짜|일)|휴무|쉬는\s*날|시작|종료/g;
  const cuts = [0];
  for (const hit of clause.matchAll(targets)) {
    const at = hit.index, previous = cuts.at(-1)!;
    if (at <= previous) continue;
    const before = mentioned(form, clause.slice(previous, at));
    const next = mentioned(form, hit[0]);
    if (before.length && next.some((field) => !before.includes(field))) cuts.push(at);
  }
  return cuts.map((start, i) => clause.slice(start, cuts[i + 1] ?? clause.length).trim()).filter(Boolean);
}

export type RequestIntent = {
  requested: Set<string>;
  blocked: Set<string>;
  serverOnly: Set<string>;
  replacements: Set<string>;
  currentKeep: Set<string>;
  canceled: Set<string>;
  cleared: Set<string>;
  reassigned: Set<string>;
  cancelAll: boolean;
};

export function requestIntent(form: AssistForm, text: string): RequestIntent {
  const out: RequestIntent = { requested: new Set(), blocked: new Set(), serverOnly: new Set(),
    replacements: new Set(), currentKeep: new Set(), canceled: new Set(), cleared: new Set(), reassigned: new Set(), cancelAll: false };
  let last: string[] = [];
  let prefixRisk = false;
  for (const clause of clauses(text)) for (const part of parts(form, clause)) {
    const fields = mentioned(form, part);
    const risk = RISK.test(part.replace(POLITE_DIRECTIVE, "")), negative = NEGATIVE.test(part), cancel = CANCEL.test(part);
    if (!fields.length) {
      if (cancel && !risk && !negative && CANCEL_END.test(part)) {
        if (!last.length) out.cancelAll = true;
        for (const field of last) { out.blocked.add(field); out.canceled.add(field); }
      } else if (risk || negative || cancel) {
        if (last.length) for (const field of last) {
          if (!out.cleared.has(field) || negative || cancel || CONDITIONAL.test(part)) out.blocked.add(field);
        }
        else prefixRisk = CONDITIONAL.test(part) || risk;
      }
      continue;
    }
    // 날짜의 정상적인 범위 및 부정 가용일은 금액/인원 범위와 다르게 처리해요.
    for (const field of fields) {
      out.requested.add(field);
      const bounds = (field === "amount" || field === "crew" || form === "quote" && field === "days") && BOUNDS.test(part);
      const reassign = REASSIGN.test(part) && !risk && !negative &&
        (MONEY.test(part) || CREW.test(part) || DATE.test(part));
      if (reassign) {
        out.reassigned.add(field);
        out.serverOnly.add(field);
      }
      if (out.cancelAll && !risk && !negative && !cancel && !bounds &&
          (MONEY.test(part) || CREW.test(part) || DATE.test(part))) {
        out.reassigned.add(field);
        out.serverOnly.add(field);
      }
      if (prefixRisk || risk || negative || cancel && !reassign || bounds) out.blocked.add(field);
      if (!prefixRisk && !risk && !negative && !cancel && CLEAR.test(part)) out.cleared.add(field);
      if (cancel && !risk && !negative && CANCEL_END.test(part)) out.canceled.add(field);
      if (KEEP.test(part) && CURRENT.test(part) && !risk && !negative && !cancel &&
        !(MONEY.test(part) || CREW.test(part) || DATE.test(part))) {
        out.currentKeep.add(field);
        out.serverOnly.add(field);
      } else if (KEEP.test(part) && PRIOR.test(part)) out.serverOnly.add(field);
      if ((form === "dates" && field === "days" || form === "period" && field === "excluded") &&
        DATE.test(part) && MOVE.test(part)) {
        out.replacements.add(field);
        out.serverOnly.add(field);
      }
    }
    last = fields;
    prefixRisk = false;
  }
  // 앞 절의 유지/취소를 뒤의 경쟁 지시와 혼동하지 않아요.
  for (const field of out.currentKeep) if (out.blocked.has(field)) out.currentKeep.delete(field);
  return out;
}

/** 다른 필드의 질문·미정은 명시적 한글 금액 자체를 무효화하지 않아요. */
export function amountRiskText(text: string): string {
  return clauses(text).flatMap((clause) => parts("quote", clause)).filter((part) => {
    const fields = mentioned("quote", part);
    return !fields.length || fields.includes("amount");
  }).join(" ");
}
