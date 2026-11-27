import type { AssistForm } from "./protocol";
import { COUNTER_WORD, counterValue, DURATION_WORD, durationTokens, classifyDurationQuantity, QUOTE_DURATION_LABEL, QUOTE_VALIDITY_LABEL, quoteDurationMention } from "./quantity";

// 값 추출과 변경 의도를 분리해 규칙·서버 후보에 같은 안전 기준을 적용해요.
const MONEY = /\d\s*[억천백만원]|\d{6,}|\d{1,3}(?:,\d{3})+|[일이삼사오육칠팔구영공십백천만억조경]+\s*원|[십백천][일이삼사오육칠팔구십백천\s]*만|[삼사육칠팔구]\s*만/;
const CREW = new RegExp("(?:\\d|" + COUNTER_WORD + ")\\s*(?:명|인(?=$|[\\s.,!?;~/∼〜–—－-]|으로|로|[은는을를도과와]|부터|에서|입니다|이에요))");
const DATE = /\d\s*(?:일|월|\/)|\d{4}[-.]\d{1,2}[-.]\d{1,2}|오늘|내일|모레|이번\s*주|다음\s*주|요일/;
const RISK = /[?？]|아마|혹시|미정|모르겠|애매|(?:할|일|될|였|했|맞을)까(?!지)|까요|되나요|되겠나요|괜찮나요|였나|고민|생각\s*(?:중|하고)|검토\s*중|것\s*같|수도\s*(?:있|없)|가능하면|조건부|(?:한다|된다|다|라|이|하|되|으)면|경우|잠정|또는|혹은|아니면|보류/;
const NEGATIVE = /(?:넣|적|쓰|사용하|입력하|확정하|정정하|수정하|변경하|바꾸|고치|옮기|미루|당기|보내|추가하|유지하|늘리|줄이|잡|정하|설정하|연장하|단축하|두|기재하|지정하|조정하|하)지\s*(?:마|말|않|못)|말아\s*주|(?<![가-힣])안\s*(?:할|하겠|넣|적|쓰겠|쓸|사용하|바꿀|고칠|늘릴|줄일|잡을|(?:(?:놔|놓아|남겨|내버려)\s*)?(?:두|둘|둬))|원하지|거부/;
const WITHDRAW_IDIOM = /없(?:던|었던)\s*(?:걸로|일로|것으로)/.source;
const CANCEL = new RegExp("취소|철회|" + WITHDRAW_IDIOM + "|원래대로");
const PLACEMENT_KEEP = /(?:(?:놔|놓아|남겨|내버려)\s*)?(?:두(?:고요?|세요|겠습니다|죠)|둘게요|둡니다|(?:두어|둬)\s*(?:주세요|줘요|주십시오|요))/.source;
const KEEP = new RegExp(/그대로|유지|건드리지|손대지|놔두|남겨\s*두|내버려\s*두|두고|두겠|둘게|두세요|둡니다|(?:되)?돌리|(?:되)?돌려|(?:이전|직전|앞서)\s*(?:후보|제안)(?:를|을)?\s*(?:쓰고|써\s*주세요|사용)/.source + "|" + PLACEMENT_KEEP);
// 인용된 유지 지시를 현재 발화의 유지 술어로 취급하지 않아요.
export function reportedRetention(part:string):boolean {
  return KEEP.test(part)&&/(?:다|라|자|냐)고\s*(?:(?:이미|앞서|아까)\s*)?(?:(?:말씀|말|설명|전달|보고|안내|언급|답변|기재|기록)\s*(?:했|하|해|한|드렸|드리|드려|받)|들었|들으|들어|했(?:습니다|어요|죠|는데|지만))/.test(part);
}
const CURRENT = /(?:현재|지금)\s*(?:폼|화면|달력|값|입력|(?:있는|적힌|적은|등록된|등록한)\s*값|금액|인원|일정|기간|부가세|제목|항목|사유|이유|날짜|작업일|제외일)|직접\s*(?:입력|수정|변경|고쳤|고친)/;
const CURRENT_HANDS_OFF = /(?:손대지|건드리지)\s*않(?:을게요|겠습니다|아요|습니다)[.!。]?$/;
const PRIOR = /이전|앞(?:서|에(?:서)?)|아까|방금|직전|지난번|전에|제안|변경안/;
const BOUNDS = /[~∼〜～–—－−]|(?:\d|만|원|명|인)\s*[-/]\s*(?:\d|[한두세네])|최대|최소|미만|초과|이상|이하|이내|한도|정도|쯤|내외|안팎|가량|대략|약\s|부터[^,.!?]{0,30}까지/;
const MOVE = /옮겨|옮기|옮길|미뤄|미루|당겨|당기|대신|말고|→|->|(?:일|날)\s*(?:만|을|를)?\s*에서/;
const EXCLUDED = /제외\s*(?:날짜|일자|일)?|(?:방문|실측)?\s*불가\s*(?:날짜|일자|일)|휴무(?:일자|일)?|쉬는\s*(?:날|걸|것)|빼는\s*날|작업\s*(?:불가|안\s*하는\s*날|하지\s*않는\s*날)|안\s*되는\s*날|(?:제외하|빼|쉬)기로\s*(?:한|정한|했던)(?=\s*(?:\d{1,2}\s*월\s*)?\d{1,2}\s*일)/;
const UNAVAILABLE = /못\s*(?:가|갑|갈|하|합|받)|받지\s*못|안\s*(?:돼|되|됩|될|받)|불가|어려|힘들|곤란|쉽니다|쉬어요|빼\s*(?:줘|주|주세요)|빼고/;
const PERIOD_BOUNDARY_PREFIX = /(?:(?:실측|방문)(?:\s*접수)?\s*)?(?:가능\s*기간(?:\s*의)?\s*|가능한\s*)?/.source;
const START = new RegExp(PERIOD_BOUNDARY_PREFIX + /(?:시작\s*(?:날짜|일자|일)?|첫\s*(?:작업일|공사일|날|(?:방문|실측)\s*(?:가능\s*)?(?:일자|날짜|일|날)))|착공일?/.source);
const END = new RegExp(PERIOD_BOUNDARY_PREFIX + /(?:종료\s*(?:날짜|일자|일)?|마감\s*(?:날짜|일자|일)?|끝나는\s*날|마지막\s*(?:(?:공사|작업|방문|실측)\s*(?:가능\s*)?)?(?:날짜|일자|날|일)|끝\s*날)|준공일?/.source);
const PERIOD = /기간|부터|까지|[~∼〜～]/;
// 직접 실행 동사에 붙은 완결된 존칭 요청만 문의 종결과 구별해요.
// 가능 여부 질문이나 앞 절의 부정/조건은 그대로 검사해요.
const EXECUTION_NOUN = /확정|변경|수정|정정|제안|입력|등록|설정|반영|선택|교체|제외|연장|단축|지정|결정|조정/.source;
const POLITE_EXECUTION = new RegExp("(?:(?:"+EXECUTION_NOUN+"|기재|작성|기록|표기|사용|저장|처리|부탁|요청)\\s*해|잡아|넣어|바꿔|고쳐|옮겨|미뤄|당겨|빼|늘려|줄여|정해|두어|둬|맞춰|적어|써|해)\\s*(?:주시겠어요|주실래요|주실\\s*수\\s*있을까요|주시겠습니까|주시겠나요)\\s*[?？]?$");
// 문자열 후보의 완결된 관형형 목적어 요청도 기존 문법 그대로
// 유지해요. 날짜 이동에는 동작을 직접 말한 종결만 사용해요.
const POLITE_TEXT_PROPOSAL = /(?:제안|작성|등록|입력|기재|기록|설정|지정|반영|확정|수정|변경|정정|선택|결정)(?:할|하는|하기\s*위한)\s+(?:후보(?:안)?|초안|제안(?:안)?|안)(?:\s*[을를])?\s*(?:주시겠어요|주실래요|주실\s*수\s*있을까요|주시겠습니까|주시겠나요)\s*[?？]?$/;
// A complete request to *show a candidate* is not a conditional execution.
// Strip only the grammatical preview object/closing request for risk analysis;
// do not remove conditions, competing values or denials from the assignment body.
const PREVIEW_OBJECT = /(?:\s*(?:할|하는|하기\s*위한)|\s*(?:바꿀|고칠|넣을|정할))(?:\s*경우의)?\s+(?:후보(?:안)?|변경안|초안|제안(?:안)?|안)(?:을|를)\s*(?:먼저\s*|우선\s*)?보여\s*(?:주세요|줘요|주십시오|주시겠어요|주실래요|주시겠습니까)\s*[?？.!。]?$/;
export function previewAssignmentBody(part: string): string | undefined {
  const m=PREVIEW_OBJECT.exec(part);
  if(!m)return;
  const before=part.slice(0,m.index);
  const body=/(?:바꿀|고칠|넣을|정할)/.test(m[0].slice(0,8))?before+" 변경":before;
  if(!new RegExp("(?:"+EXECUTION_NOUN+")\\s*$").test(body))return;
  return body;
}
const CLEAR = /(?:삭제|지우|비우|빼)\s*(?:해|하|어|워|주|주세요|줘)|없애|초기화/;
// 상태를 나타내는 동사와 연결 어미를 함께 검사해요. 명사 '화면'처럼
// 단순히 '면'으로 끝나는 단어를 조건으로 해석하지 않아요.
const CONDITIONAL_STATE = /(?:맞|가능하|확정되|정해지|확인되|승인되|충족되|허용되|맞춰지|끝나|완료되|잡히|되|있|없|나)(?:으)?면/.source;
const CONDITIONAL_END = /라면|다면|으면|이면|하면|오면|(?:별도|포함|면세)면/.source + "|" + CONDITIONAL_STATE;
const CONDITION_SCOPE = new RegExp("가능하면|조건부|만약|가정하면|(?:" + CONDITIONAL_END +
  ")(?=$|[\\s,，;.!?！？。])|(?:는|할|될)\\s*경우|(?:일|할|될)\\s*때");
const CONDITIONAL = new RegExp(CONDITION_SCOPE.source+"|경우|혹시|아마");
const CONDITION_LINK = new RegExp("(?:"+CONDITIONAL_END+"|(?:일|는|할|될)\\s*경우|(?:일|할|될)\\s*때)\\s*$");
const COMPLETED_INQUIRY = /(?:[?？]|[가-힣](?:까요|나요|겠습니까)|(?:가능한지|가능할지|되는지|될지|맞는지|괜찮은지|있는지|여부)[^,;.!?！？。\n]{0,20}(?:궁금합니다|궁금해요|알려\s*주세요|확인\s*(?:부탁합니다|부탁드립니다|해\s*주세요)))$/;
const VALUE_DENIAL = /(?:아닙니다|아니에요|아니예요|아님|아냐)\s*[.!。]?$/;
const REASSIGN = /(?:취소|철회)(?:하|해)?고/;
const INDIRECT_INQUIRY = /가능한지|가능할지|가능한가요|가능\s*여부|수\s*(?:있는지|없는지|있을지|없을지)|될지|되는지|맞는지|얼마나\s*가능/;
const CREW_LIMIT = /(?:명|인)\s*(?:까지|이하|이상|미만|초과|이내)/;
const CANCEL_CURRENT_LINK = /(?:취소|철회)(?:하|해)?고\s*(.+)$/;
const CURRENT_KEEP_LINK = new RegExp("(?:유지\\s*하(?:고요?|며)|그대로\\s*(?:" + PLACEMENT_KEEP + "))$");
const CHANGE_ASSIGNMENT = /(?:변경|수정|정정|확정|설정|지정|결정|입력|등록|교체|대체|이동|기재|작성|조정)\s*(?:합니다|하겠습니다|할게요|해요|해\s*(?:주세요|줘요|주십시오)|부탁(?:합니다|드립니다|해요))$|(?:바꿔|고쳐|잡아|정해|넣어|적어|써)\s*(?:주세요|줘요|주십시오)$|(?:바꾸겠습니다|고치겠습니다|바꿀게요|고칠게요)$/;
const VAT_CONTEXT = /^(?:(?:이번|해당|이|그)\s*)?(?:추가\s*(?:공사\s*)?(?:금액|비용|금)|견적(?:\s*금액)?)(?:은|는|의)?\s*$/;
type VatMode = "included" | "excluded" | "exempt";
type VatToken = {start:number;end:number;mode:VatMode};
const TAX_LABEL=/(?:부가(?:가치)?세|세금|VAT)(?:\s*(?:조건|방식|구분))?/.source;
const TAX_PARTICLE="(?:으로|로|은|는|이|가|을|를|만|도|과|와|의|에서|이나)";
const TAX_RIGHT="(?=$|[\\s,，;:.!?！？。()\"'“”‘’/·]|"+TAX_PARTICLE+
  "|입니다|이에요|예요|이고|이며|이라|이라면|인지|이었|라고|라는|라면|아닙|아니|된|되는|되어|돼|됐|하(?:는|지|기|고|며|면|자|실|려고|겠습니다)|해|했|할|함|합|한다|한(?:다|것|다면)?|받(?:아|는|기|고|으면|을|습)|않|맞|가능)";
const TAX_LABEL_TOKEN=new RegExp("(?<![가-힣a-zA-Z0-9])"+TAX_LABEL+"(?=$|[\\s,，;:.!?！？。]|\\s*"+TAX_PARTICLE+")","gi");
const TAX_TOKEN=new RegExp("(?<![가-힣a-zA-Z0-9])(?:(?<negative>포함\\s*하지\\s*않는\\s*(?:방식|조건|구분|형태))"+
  "|(?<taxExempt>"+TAX_LABEL+"\\s*(?:[은는이가을를만도]\\s*)?(?:없음|면제|0\\s*원))"+
  "|(?<billing>(?:따로|별개로|(?:견적(?:\\s*금액)?|공사\\s*(?:금액|비용)|금액|총액|공사비)\\s*(?:과|와|에서)\\s*별개로)\\s*(?:청구|부과|계산|받(?:아|는|기|고|으면|을|습)?))"+
  "|(?<excluded>미포함|별도|제외|빼고|따로)|(?<included>포함된|포함)|(?<exempt>면세|면제))"+TAX_RIGHT,"gi");
const TAX_LEAD=/^(?:(?:우선|먼저|다시|그럼|그러면|그냥|최종적으로|이번에는|가능하면|혹시|아|네|예)\s+)*(?:(?:이번|해당|이|그)\s*건(?:은|는|만)\s*)?$/;
const TAX_LINK=/^\s*(?:은|는|이|가|을|를|으로|로)?\s*(?:아니고|아니라|말고|대신|에서|또는|혹은|이나|나|및|과|와)\s*[,，]?\s*$/;
// 값 뒤의 명사구, 대입 위치, 요청 술어를 각각 읽어요. 대상 없는
// 일반 명사/확인 동사는 여전히 세금 값의 완결 구문이 아니에요.
const TAX_VALUE_NOMINAL=/(?:(?:방식|조건|구분|형태|것|거|걸|처리|청구|계산|부과)(?:으로|로)?\s*)?/.source;
const TAX_VALUE_POSITION=/(?:(?:으로|로|은|는|이|가|을|를|만|도|이라고|라고)\s*)?(?:(?:새로|다시|우선|최종(?:적으로)?|먼저)\s*)?(?:(?:후보(?:안)?|초안|제안(?:안)?|안)(?:으로|로|에)?\s*)?/.source;
const TAX_PROPOSAL_OBJECT_TAIL=new RegExp("^\\s*(?:으로|로)\\s*(?:(?:새로|다시|우선|최종(?:적으로)?|먼저)\\s*)?(?:후보(?:안)?|초안|제안(?:안)?|안)\\s*(?:을|를)\\s*보여\\s*(?:주세요|줘요|주십시오|주시겠어요|주실래요)\\s*[.!。]?$");
// 인용한 세금 값이 후보 생성의 내용일 때만 토큰을 보존해요.
// 필드 소유권 및 부정/조건/문의의 독립 검증은 유지해요.
const TAX_QUOTED_PROPOSAL_TAIL=/^\s*(?:이라는|라는)\s*(?:후보(?:안)?|초안|제안(?:안)?|안)\s*[을를]\s*(?:만들어|작성해|보여)\s*(?:주세요|줘요|주십시오|주시겠어요|주실래요)\s*$/;
const TAX_REQUEST_END=/(?:주세요|줘요|주십시오|주시겠어요|주실래요)/.source;
const TAX_ACTION_END=/(?:합니다|해요|할게요|하겠습니다|하고요?|하며|이고|이며|해\s*(?:주세요|줘요|주십시오|주시겠어요|주실래요)|하지\s*(?:마세요|말아\s*주세요|않겠습니다)|할까요|할지|하면|한다면|할\s*경우|부탁(?:합니다|드립니다|드려요|해요)?|바랍니다|바라요)/.source;
const TAX_VALUE_TAIL=new RegExp("^\\s*(?:(?:(?:하(?:는|기|ㄴ)|할|한|받는|는|기)\\s*(?:것|거|걸|방식|조건|구분|형태)(?:으로|로)?\\s*)?"+
  TAX_VALUE_NOMINAL+TAX_VALUE_POSITION+
  "(?:(?:선택|확정|결정|설정|지정|정|처리|적용|입력|기입|정리|표시|표기|기재|반영|제안|제시|변경|수정|정정|등록|추가|사용|채택|유지|청구|계산|부과)\\s*(?:"+TAX_ACTION_END+")?"+
  "|(?:해|정해|바꿔|골라|받아|넣어|적어|돌려|올려|보여|잡아)\\s*"+TAX_REQUEST_END+
  "|(?:부탁(?:합니다|드립니다|드려요|해요)?|할게요|하겠습니다|합니다|해요|할까요|할지|하면|한다면|하지\\s*(?:마세요|말아\\s*주세요)|하고요?|하며|잡고요?|이고|이며|맞습니다|맞아요|입니다|이에요|예요|아닙니다|아니에요|미정(?:입니다|이에요)?|고민\\s*중(?:입니다|이에요)?|가능한지\\s*(?:확인해|알려)\\s*주세요|(?:돼|되어)\\s*(?:있습니다|있어요|있는데|있음)))?)\\s*[.!?！？。]?\\s*$");
function vatValueTail(tail:string):boolean{
  if(TAX_VALUE_TAIL.test(tail)||TAX_PROPOSAL_OBJECT_TAIL.test(tail))return true;
  // 완결된 대입 연결형 뒤에 별도의 칸/나머지 지시가 올 때만
  // 값 구문을 그 경계에서 종료해요.
  return [...tail.matchAll(/(?:하고요?|하며|잡고요?|이고|이며)\s+/g)].some(join=>
    TAX_VALUE_TAIL.test(tail.slice(0,join.index+join[0].length))&&
    /^(?:나머지|다른\s*(?:값|후보|칸)|(?:추가(?:된)?\s*)?(?:사유|이유|제목|항목(?:명)?|금액|총액|비용|공사\s*기간|기간|일수|유효기간|만료일|부가세|세금))/.test(tail.slice(join.index+join[0].length)));
}
const TAX_SELECTION_TAIL=/^\s*(?:(?:하(?:는|기)|할|한)\s*(?:것|거|걸|방식|조건|구분|형태))?\s*(?:으로|로)?\s*(?:선택|후보|제안|변경안|정정안|조건|방식|구분)(?=\s*[은는이가을를만도]?\s*(?:전부\s*|모두\s*|다\s*)?(?:취소|철회|없(?:던|었던)\s*))/;
const TAX_GAP=/^\s*(?:(?:[은는이가을를만도]|:|=)\s*)?$/;
const TAX_REVERSE_GAP=/^\s*(?:의\s*)?$/;

// 의미 토큰과 세금 칸 소유권은 달라요. 부사 '따로 확인'이나
// 일반 '별도 비용', '면세점'은 새 세금 값이 아니에요.
export function vatSemanticTokens(raw:string):VatToken[]{
  return [...raw.matchAll(TAX_TOKEN)].flatMap(hit=>{
    const start=hit.index,end=start+hit[0].length;
    const word=hit[0];
    if(word==="따로"||word==="별도"){
      const tail=raw.slice(end).split(/[,，;.!?！？。\n]/,1)[0];
      if(!vatValueTail(tail)&&!TAX_QUOTED_PROPOSAL_TAIL.test(tail)&&!new RegExp(TAX_LINK.source.slice(0,-1)).test(tail)&&
          !TAX_SELECTION_TAIL.test(raw.slice(end))&&
          !/^\s*(?:으로|로)?\s*(?:하고|하며|정하고|선택하고|처리하고|확정하고)\s+/.test(tail)&&
          !/^\s*(?:부가(?:가치)?세|세금|VAT)/i.test(tail)&&
          !/^\s*(?:안\s*|않|아닙|아니|말고|시(?:\s|$)|면|이면|라면|일\s*(?:때|경우)|\(?[Xx×])/.test(tail))return [];
    }
    const mode:VatMode=hit.groups?.negative!==undefined||hit.groups?.billing!==undefined||hit.groups?.excluded!==undefined?
      "excluded":hit.groups?.included!==undefined?"included":"exempt";
    return [{start,end,mode}];
  });
}
export function standaloneVatTokens(raw:string,tokens=vatSemanticTokens(raw)):VatToken[]{
  if(!tokens.length||!TAX_LEAD.test(raw.slice(0,tokens[0].start))||
      tokens.slice(1).some((token,i)=>!TAX_LINK.test(raw.slice(tokens[i].end,token.start)))||
      !vatValueTail(raw.slice(tokens.at(-1)!.end)))return [];
  return ["따로","면제","빼고"].includes(raw.slice(tokens[0].start,tokens[0].end))?[]:tokens;
}
export function scopedVatTokens(raw:string):VatToken[]{
  const tokens=vatSemanticTokens(raw);
  if(standaloneVatTokens(raw,tokens).length)return tokens;
  const labels=[...raw.matchAll(TAX_LABEL_TOKEN)];
  const result:VatToken[]=[];
  for(const token of tokens){
    const word=raw.slice(token.start,token.end);
    const adjacent=labels.some(label=>{
      const end=label.index+label[0].length;
      return end<=token.start&&TAX_GAP.test(raw.slice(end,token.start))||
        token.end<=label.index&&TAX_REVERSE_GAP.test(raw.slice(token.end,label.index));
    });
    const linked=result.length&&TAX_LINK.test(raw.slice(result.at(-1)!.end,token.start));
    const explicitExempt=word==="면세"&&TAX_LEAD.test(raw.slice(0,token.start))&&
      (vatValueTail(raw.slice(token.end))||/^\s*업체(?:예요|입니다|이에요)$/.test(raw.slice(token.end))||
        tokens.some(next=>next.start>token.end&&TAX_LINK.test(raw.slice(token.end,next.start))));
    if(adjacent||new RegExp("^"+TAX_LABEL,"i").test(word)||linked||explicitExempt)result.push(token);
  }
  return result;
}
// '포함/별도' 전체를 세금 필드 별칭으로 만들지 않아요. 선택 등
// 후보 명사가 바로 뒤의 철회 술어에 연결된 경우에만 값 지정 철회예요.
const VAT_MODE_ALIASES: Record<VatMode, readonly string[]> = {
  included: ["포함","포함된"],
  excluded: ["별도","미포함","제외"],
  exempt: ["면세","부가세 없음","부가세 면제","부가세 0원"],
};
const VAT_ALIAS_MODE = new Map(Object.entries(VAT_MODE_ALIASES).flatMap(([mode,words]) => words.map(word => [word,mode as VatMode] as const)));
const VAT_ALIAS = [...VAT_ALIAS_MODE.keys()].sort((a,b)=>b.length-a.length).join("|");
const VAT_MODE_WITHDRAWAL = new RegExp("^(?:(?:가능하면|혹시|아마|조건부로)\\s*)?(?:(부가(?:가치)?세|세금|VAT)(?:\\s*(?:조건|방식|구분))?\\s*[은는이가]?\\s*)?(" + VAT_ALIAS + ")(?:\\s*(?:으로|로))?\\s*(?:선택|후보|제안|변경안|정정안|조건|방식|구분)\\s*[은는이가을를만도]?\\s*(?:전부\\s*|모두\\s*|다\\s*)?(?:" + CANCEL.source + ")", "i");
const VAT_MODE_CANCEL_END = new RegExp("(?:취소|철회)\\s*(?:해\\s*(?:주세요|줘요|주십시오)|할게요|하겠습니다|합니다|해요|할래요)$|" + WITHDRAW_IDIOM + "\\s*(?:해\\s*(?:주세요|줘요|주십시오)|할게요|하겠습니다|합니다|해요)$");
function vatModeWithdrawal(form: AssistForm, part: string): { mode: VatMode; bound: boolean; span: string } | undefined {
  if (form !== "quote" && form !== "extra") return;
  const match = VAT_MODE_WITHDRAWAL.exec(part);
  if (!match) return;
  const [,tax,word] = match;
  const mode = VAT_ALIAS_MODE.get(word);
  if (!mode) return;
  return { mode, bound: !!tax || mode === "exempt", span: match[0] };
}
const AMOUNT_LABEL = /(?:견적(?:서)?|추가\s*(?:공사|작업|시공)?|공사|시공)\s*(?:금액|총액|비용|금)|견적액|추가금(?:액)?|금액|총액|합계|공사비|가격|비용|견적\s*가/.source;
const VAT_LABEL = TAX_LABEL+"|(?<![가-힣])면세"+TAX_RIGHT;
const CREW_COUNT_SUFFIX = /(?:\s*수(?=$|[\s,:=;.!?！？。은는이가을를만도과와]))?/.source;
const CREW_LABEL = "(?:투입(?:할)?|작업)\\s*인원"+CREW_COUNT_SUFFIX+"|작업자"+CREW_COUNT_SUFFIX+"|인원"+CREW_COUNT_SUFFIX+"|인력|인부";
const WORK_DATE_LABEL = /(?:공사|작업|시공|방문|실측)\s*(?:날짜|일자|일정|할\s*날|일)|날짜|일정|일자/.source;
// 업체 추가금의 명시 대상. 긴 명칭부터 읽고, 명칭이 생략된
// '추가 작업/공사/시공'은 실제 조사와 독립된 필드 위치가 있어야 해요.
export const EXTRA_EXPLICIT_TITLE = /(?:(?:추가\s*(?:작업|공사|시공)?|공사|시공|작업)\s*)?(?:항목\s*(?:제목|이름)|항목명|작업명|공사명|제목|항목|이름)/.source;
const EXTRA_IMPLICIT_TITLE = /추가\s*(?:작업|공사|시공)(?=\s*(?:[:=]|[은는이가을를](?:\s|$)))(?!\s*[은는이가을를]\s*(?:하(?:게|는|려|기|던|였|고)|할|했|해야|진행하|시작하|하게|하게\s*된))/.source;
export const EXTRA_REASON_LABEL = /(?:(?:추가금|추가\s*(?:비용|작업|공사|시공)?)\s*)?(?:사유|이유|경위|원인)(?:\s*항목)?|왜\s+[^,，;.!?！？。\n]{1,60}?(?:는지|았는지|었는지)\s*(?:쓰는|적는|기재하는|기록하는)\s*(?:칸|란)/.source;
// 분리된 조사는 그 뒤에 실제 경계가 필요해요. '항목 이름표'의
// '이'를 앞 명사의 조사로 잘라 읽지 않아요.
export const EXTRA_LABEL_MARKER = /(?:(?:\s*(?:칸|입력란|란))?\s*[:=]|(?:\s*(?:칸|입력란|란))?(?:에는|[은는을를이가만도에])|(?:\s*(?:칸|입력란|란))?\s+(?:에는|[은는을를이가만도에])(?=$|[\s:'"“‘,.;!?！？。]))\s*/.source;
const EXTRA_IMPLICIT_POSITION = /(?<=(?:^|[,，;.!?！？。\n])\s*(?:(?:그리고|또|다음으로|다만|가능하면|만약|혹시|아마|조건부로)\s+|[^,，;.!?！？。\n]{0,48}(?:라면|다면|으면|이면|하면|되면|경우)\s+)?|(?:하고요?|하며|적고|쓰고|기입하고|주시고)\s*)/.source;
const EXTRA_IMPLICIT_WITHDRAWAL = /추가\s*(?:작업|공사|시공)\s*(?:변경|수정|정정|제안|후보|선택|요청)(?=\s*[은는이가을를만도]?\s*(?:전부\s*|모두\s*|다\s*)?(?:취소|철회|없(?:던|었던)\s*))/.source;
const EXTRA_WORD_START = /(?<![가-힣a-zA-Z\d])/.source;
const EXTRA_WORD_END = /(?=$|[\s,:=;.!?！？。은는이가을를만도과와]|(?:입력)?란|칸|하고|이랑|랑|으로|로|에는|에|의(?=\s))/.source;
const TITLE_LABEL = EXTRA_WORD_START + "(?:" + EXTRA_EXPLICIT_TITLE + "|" + EXTRA_IMPLICIT_POSITION + "(?:" + EXTRA_IMPLICIT_TITLE + "|" + EXTRA_IMPLICIT_WITHDRAWAL + "))" + EXTRA_WORD_END;
const REASON_LABEL = EXTRA_WORD_START + "(?:" + EXTRA_REASON_LABEL + ")" + EXTRA_WORD_END;

export function maskExtraQuotes(text: string): string {
  return text.replace(/"[^"\r\n]*"|'[^'\r\n]*'|“[^”\r\n]*”|‘[^’\r\n]*’/g, value => "\uE000".repeat(value.length));
}
export type ExtraTextTarget = { field: "title" | "reason"; index: number; labelEnd: number; valueStart: number; marker: string };
/** 명칭과 조사/구분자를 함께 찾고 원문의 값 시작 인덱스를 보존해요.
 * 인용문 내부는 필드 표지가 아니며, 필드의 명칭은 값에 포함되지 않아요. */
export function extraTextTargets(text: string): ExtraTextTarget[] {
  const masked = maskExtraQuotes(text);
  // A quoted literal can follow an explicit label without a particle.
  // Only the quote position supplies that boundary; the text parser still
  // requires a complete assignment predicate for this shorthand.
  const pattern = new RegExp("(?:(?<title>" + TITLE_LABEL + ")|(?<reason>" + REASON_LABEL + "))(?<marker>(?:" + EXTRA_LABEL_MARKER + ")|\\s+(?=\\uE000))", "g");
  return [...masked.matchAll(pattern)].map(hit => ({
    field: hit.groups!.title !== undefined ? "title" : "reason",
    index: hit.index, labelEnd: hit.index + hit[0].length - hit.groups!.marker.length,
    valueStart: hit.index + hit[0].length, marker: hit.groups!.marker,
  }));
}

/** 별칭 목록을 대상 인식과 독립 절 분리에 함께 사용해요. 긴 유효기간 이름이 '기간'보다 먼저예요. */
function labelPattern(form: AssistForm): RegExp {
  const entries: [string,string][] = form === "quote" ? [
    ["valid", QUOTE_VALIDITY_LABEL], ["amount", AMOUNT_LABEL], ["vat", VAT_LABEL],
    ["days", QUOTE_DURATION_LABEL + "|(?:공사|작업|시공)(?=\\s*(?:은|는|이|가|을|를|에|:)?\\s*" + DURATION_WORD + ")"],
  ] : form === "extra" ? [
    ["amount", AMOUNT_LABEL], ["vat", VAT_LABEL], ["title", TITLE_LABEL], ["reason", REASON_LABEL],
  ] : form === "dates" ? [
    ["crew", CREW_LABEL], ["days", WORK_DATE_LABEL],
  ] : [
    ["start", START.source], ["end", END.source], ["excluded", "(?:(?:방문|실측|접수)\\s*)?(?:"+EXCLUDED.source+")"],
    ["period", "(?:(?:실측|방문)\\s*(?:가능\\s*)?|가능\\s*|전체\\s*)?기간(?!\\s*(?:의\\s*)?(?:시작|종료|끝|마감))"],
  ];
  return new RegExp(entries.map(([name, source]) => "(?<" + name + ">" + source + ")").join("|"), "gi");
}

function labelFields(form: AssistForm, text: string): Set<string> {
  const fields = new Set<string>();
  for (const hit of text.matchAll(labelPattern(form))) {
    for (const [name,value] of Object.entries(hit.groups ?? {})) if (value !== undefined) {
      if (name === "period") { fields.add("start"); fields.add("end"); }
      else fields.add(name);
    }
  }
  return fields;
}

// Explicit identity of the actual form value, distinct from an earlier
// proposal. Linked predicates require a separately owned following clause.
const ACTUAL_IDENTITY = /(?:현재|지금)\s*(?:값|(?:등록|입력|저장)(?:된|한)\s*(?:값|대로)|(?:폼|화면|달력)\s*(?:값|대로))|이미\s*(?:예약|등록|입력|저장)(?:한|된|해\s*둔)/;
const IDENTITY_LINK = /(?:값\s*(?:으로|로)\s*고정하고|대로\s*(?:하고|하며)|건드리지\s*마시고|손대지\s*마시고|고정하며|유지하고요?)$/;
const IDENTITY_END = /(?:(?:고정|유지)(?:해\s*(?:주세요|줘요|주십시오)|합니다|하겠습니다|할게요)|대로\s*(?:두세요|두겠습니다|둘게요|해주세요)|(?:건드리지|손대지)\s*(?:마세요|말아\s*주세요))$/;
// "원래대로 두세요" explicitly leaves an owned form value unchanged.
// The bare restoration/cancellation idiom is still ambiguous, and a
// proposal/history reference or any competing operation is not identity.
function originalValueIdentity(form:AssistForm,field:string,part:string):boolean {
  const clean=part.trim().replace(/[.!。]+$/,"");
  const label=labelPattern(form).exec(clean);
  if(!label||label.index!==0||labelFields(form,label[0]).size!==1||
      !labelFields(form,label[0]).has(field))return false;
  return /^\s*(?:은|는|을|를|이|가|만|도)?\s*(?:원래\s*(?:그대로|대로)|수정\s*전\s*(?:값|상태)\s*(?:그대로|대로))\s*(?:두세요|둡니다|두겠습니다|둘게요|놔두세요|유지(?:해\s*(?:주세요|줘요|주십시오)|합니다|하겠습니다|할게요))$/.test(clean.slice(label[0].length));
}
function currentIdentityClause(form:AssistForm,field:string,part:string):"linked"|"complete"|undefined {
  const labels=labelFields(form,part), clean=part.replace(/[.!。]+$/,"");
  if(originalValueIdentity(form,field,clean))return "complete";
  if(labels.size!==1||!labels.has(field)||!ACTUAL_IDENTITY.test(clean)||PRIOR.test(clean)||
      /후보|변경안/.test(clean)||RISK.test(clean)||CONDITION_SCOPE.test(clean)||CANCEL.test(clean)||
      MONEY.test(clean)||CREW.test(clean)||DATE.test(clean)||VALUE_DENIAL.test(clean))return;
  if(IDENTITY_LINK.test(clean)&&!NEGATIVE.test(clean))return "linked";
  if(IDENTITY_END.test(clean)&&!NEGATIVE.test(clean))return "complete";
}

// A stated final total and its explanatory increment/decrement are not two
// competing totals. Similarly, an expressly rejected upper bound can precede
// a new independently labelled total. Keep the actual final span; never
// compute a total from the delta or infer a value from an omitted baseline.
export function crewFinalAssignment(part:string):{body:string;first:number;next:number;kind:"delta"|"bound";direction:1|-1;reference:"current"|"previous"|null}|undefined {
  const quantity="(?<![가-힣\\d.])([1-9]\\d?|"+COUNTER_WORD+")\\s*(?:명(?!\\d|분|당|씩|단|짜리|치|함|칭)|인(?=$|[\\s,.;!?]|으로|로|까지))";
  const hits=[...part.matchAll(new RegExp(quantity,"g"))];
  if(hits.length!==2)return;
  const [first,next]=hits;
  const prefix=part.slice(0,first.index);
  if(RISK.test(prefix)||NEGATIVE.test(prefix)||CANCEL.test(prefix)||CONDITION_SCOPE.test(prefix)||DATE.test(prefix)||MONEY.test(prefix))return;
  const gap=part.slice(first.index+first[0].length,next.index);
  const total=gap.match(/^\s*(?:더\s*)?(?:붙여서|추가해서|추가해|늘려서|증원해서|줄여서|빼서|감원해서)\s*(?:[,，]\s*)?((?:총|합계|모두)\s*)$/);
  const bound=gap.match(new RegExp("^\\s*까지(?:는|도)?\\s*필요\\s*없(?:고|으니)\\s*(?:[,，]\\s*)?((?:"+CREW_LABEL+")\\s*(?:은|는|을|를|만|이|가|:)??\\s*)$"));
  const lead=total?.[1]??bound?.[1];
  if(!lead)return;
  const body=part.slice(next.index-lead.length);
  const polite=POLITE_EXECUTION.exec(body), riskText=polite?body.slice(0,polite.index):body;
  if(!(CHANGE_ASSIGNMENT.test(body.replace(/[.!。]+$/,""))||polite)||
    RISK.test(riskText)||NEGATIVE.test(body)||CANCEL.test(body)||CONDITION_SCOPE.test(body)||BOUNDS.test(body)||CREW_LIMIT.test(body))return;
  const a=counterValue(first[1])??Number(first[1]), b=counterValue(next[1])??Number(next[1]);
  if(!Number.isSafeInteger(a)||!Number.isSafeInteger(b)||a<1||b<1)return;
  const previous=PRIOR.test(prefix),current=CURRENT.test(prefix);
  if(previous&&current)return;
  return {body,first:a,next:b,kind:total?"delta":"bound",direction:/줄여서|빼서|감원해서/.test(gap)?-1:1,
    reference:previous?"previous":current?"current":null};
}
export function crewFinalAssignmentBody(part:string):string|undefined {
  return crewFinalAssignment(part)?.body;
}

// 변경행위와 그 요청 객체를 정확한 소유 대상에 결합해요. 단지
// '취소'가 끝에 있고 필드명 하나가 들어 있다고 업무 취소/보고를
// 미확정 후보 철회로 바꾸지 않아요.
const WITHDRAW_OPERATION = "(?:"+EXECUTION_NOUN+"|기재|작성|기록|표기|표시|명시|저장|사용|채택|갱신|증액|감액|증원|감원|이동|대체|(?:재\\s*)?배치|삭제|제거)";
const WITHDRAW_ADNOMINAL = "(?:(?:"+WITHDRAW_OPERATION+"|기재|작성|기록|표기|표시|명시|저장|사용|채택|갱신|정)\\s*하|바꾸|바꿔|바꿀|고치|고쳐|고칠|옮기|옮겨|옮길|미루|미뤄|미룰|당기|당겨|당길|올리|내리|넣)(?:는|려던|려고\\s*했던|기로\\s*했던|던|려는)";
const WITHDRAW_OBJECT = "(?:(?:"+WITHDRAW_OPERATION+"\\s*|"+WITHDRAW_ADNOMINAL+"\\s*)?(?:요청|제안|변경안|정정안|후보|선택|내용|건|거|것|안)|"+WITHDRAW_OPERATION+")(?:[은는을를이가도만])?";
const WITHDRAW_OWNED_APPLICATION = "(?:"+WITHDRAW_OPERATION+"\\s*신청)(?:[은는을를이가도만])?";
const WITHDRAW_PREDICATE = "(?:(?:취소|철회)(?:\\s*(?:해\\s*(?:주세요|줘요|주십시오)|합니다|할게요|하겠습니다|해요|하고요?|하며|할래요))?|"+WITHDRAW_IDIOM+"\\s*(?:할게요|하겠습니다|합니다|해요|하고요?|해\\s*(?:주세요|줘요|주십시오)))";
const WITHDRAW_VALUE = new RegExp("^(?<value>.+?)\\s*(?:으로|로|이라고|라고)\\s*(?<operation>"+WITHDRAW_OBJECT+")\\s*(?:전부\\s*|모두\\s*|다\\s*)?(?<predicate>"+WITHDRAW_PREDICATE+")$");
const WITHDRAW_CANDIDATE = new RegExp("^(?<value>.+?)\\s*(?<operation>(?:후보(?:안)?|제안(?:안)?|선택|변경안|정정안)[은는을를이가도만]?)\\s*(?<predicate>"+WITHDRAW_PREDICATE+")$");
const WITHDRAW_SCALAR_OBJECT = /^(?:(?:변경|수정|정정|확정|입력|설정|조정|증액|감액)\s*하|올리|내리|바꾸|고치|넣)(?:는|려던|려고\s*했던|기로\s*했던)\s*(?:요청|제안|변경안|정정안|건|것|거)[은는을를이가만]?$/;
const WITHDRAW_GROUP = new RegExp("^\\s*(?:(?:"+WITHDRAW_OBJECT+"|"+WITHDRAW_OWNED_APPLICATION+")\\s*)?(?:과|와|및|이랑|랑|하고|[,，·/])\\s*$");
export type WithdrawalProof = {
  scope:string; fields:string[]; operation:string; predicate:string;
  reference:"pending"|"previous"|"current"; valueText?:string; valueKind?:"candidate"|"operation"; selectedMode?:VatMode;
  currentRetention?: CurrentRetentionProof;
};

type CurrentRetentionProof = {
  scope: string;
  fields: string[];
  reference: "current";
};

function withdrawalObject(form:AssistForm,part:string,fields:string[],mode:ReturnType<typeof vatModeWithdrawal>):WithdrawalProof|undefined{
  const masked=maskExtraQuotes(part);
  const cancellation=CANCEL.exec(masked);
  if(!cancellation)return;
  if(mode){
    if(!mode.bound||!new RegExp("^(?:"+VAT_MODE_CANCEL_END.source+")$").test(part.slice(cancellation.index)))return;
    return {scope:part,fields:["vat"],operation:mode.span.slice(0,cancellation.index),predicate:part.slice(cancellation.index),reference:"pending",selectedMode:mode.mode};
  }
  const marks=[...masked.matchAll(labelPattern(form))].filter(hit=>hit.index+hit[0].length<=cancellation.index);
  let offset=0;
  let reference:WithdrawalProof["reference"]="pending";
  if(marks.length){
    let prefix=part.slice(0,marks[0].index);
    // 견적 가격의 앞 견적 명사는 가격 칸의 수식어예요.
    if(form==="quote"&&marks[0].groups?.amount)prefix=prefix.replace(/견적(?:서)?\s*$/,"");
    const lead=new RegExp("^"+WITHDRAW_LEAD+"$").exec(prefix);
    if(!lead)return;
    reference=withdrawalReference(lead.groups?.reference);
    if(marks.slice(1).some((hit,i)=>!WITHDRAW_GROUP.test(part.slice(marks[i].index+marks[i][0].length,hit.index))))return;
    const tail=part.slice(marks.at(-1)!.index+marks.at(-1)![0].length);
    const lastField=Object.entries(marks.at(-1)!.groups??{}).find(([,v])=>v!==undefined)?.[0];
    // A list is an object of the already owned date-list field, not
    // another operation noun. Use the same typed reference vocabulary.
    const listQualifier=lastField&&compatibleReferenceNouns(form,lastField).includes("목록")&&
      (lastField==="excluded"||form==="dates"&&lastField==="days") ? "(?:목록\\s*)?" : "";
    offset=part.length-tail.length+(tail.match(new RegExp("^\\s*"+listQualifier+"[은는이가을를만도]?\\s*"))?.[0].length??0);
  }else if(fields.length){
    // 명시적 금액 표지 '견적'은 기존 견적 폼에만 한정해요.
    const quote=form==="quote"&&fields.length===1&&fields[0]==="amount"?part.match(/^견적(?:서)?\s*[은는을를]?\s*/):null;
    if(quote)offset=quote[0].length;
    else if(fields.length===1&&["amount","crew"].includes(fields[0])){
      const lead=new RegExp("^"+WITHDRAW_LEAD).exec(part);
      if(!lead)return;
      offset=lead[0].length;
      reference=withdrawalReference(lead.groups?.reference);
    }else return;
  }
  let body=part.slice(offset).trim();
  const linked=body.match(CANCEL_CURRENT_LINK)?.[1];
  const owned=[...labelFields(form,part.slice(0,cancellation.index))];
  const currentRetention=linked&&owned.length&&owned.length===fields.length&&owned.every(f=>fields.includes(f))
    ? currentRetentionProof(form,linked,owned) : undefined;
  if(currentRetention){
    body=body.slice(0,body.length-linked!.length).trim();
  }
  let match=(fields.length?WITHDRAW_BODY:WITHDRAW_GENERIC_BODY).exec(body);
  let valueKind:WithdrawalProof["valueKind"];
  if(!match&&fields.length===1){
    const operation=WITHDRAW_VALUE.exec(body);
    if(operation&&["amount","crew"].includes(fields[0])&&WITHDRAW_SCALAR_OBJECT.test(operation.groups?.operation??"")){
      match=operation;valueKind="operation";
    }else if(reference==="previous"&&marks.length){
      match=WITHDRAW_CANDIDATE.exec(body);if(match)valueKind="candidate";
    }
  }
  if(!match)return;
  if(!marks.length&&fields.length&&valueKind!=="operation")return;
  const predicate=match.groups?.predicate??"";
  if(offset+part.slice(offset).length-part.slice(offset).trimStart().length+body.length-predicate.length!==cancellation.index)return;
  const valueText=match.groups?.value?.trim();
  if(valueText&&(/(?:^|\s)(?:중|가운데)(?:\s|$)/.test(maskExtraQuotes(valueText))||RISK.test(maskExtraQuotes(valueText))||NEGATIVE.test(maskExtraQuotes(valueText))))return;
  if(currentRetention)reference="current";
  else if(reference==="pending")reference=withdrawalReference(match.groups?.reference);
  return {scope:part,fields,operation:match.groups?.operation??"",predicate,reference,
    ...(valueText?{valueText,valueKind}:{}),...(currentRetention?{currentRetention}:{})};
}

/** 독립 경계 대입에서는 절 첫머리의 명시적 필드명만 사용해요. */
export function explicitPeriodBoundaryTail(clause: string): { field: "start" | "end"; tail: string } | undefined {
  const hit = labelPattern("period").exec(clause);
  if (!hit || hit.index !== 0) return;
  const field = hit.groups?.start ? "start" : hit.groups?.end ? "end" : undefined;
  const tail = clause.slice(hit[0].length).match(/^\s*(?:[은는이가을를]|[:=])\s*(.+)$/)?.[1];
  return field && tail ? { field, tail } : undefined;
}

// 원래 후보의 값과 출처가 맞아도 다른 종류의 값을 참조한 지시는
// 해당 칸의 유지 근거가 아니에요. 모든 업무 폼에서 같은 명사 타입을 검사해요.
const REFERENCE_NOUNS: Record<string, readonly string[]> = {
  start: ["날짜", "일자", "일정", "날"], end: ["날짜", "일자", "일정", "날"],
  excluded: ["날짜", "일자", "목록", "날"], amount: ["금액", "가격", "비용", "총액", "견적액"],
  vat: ["조건", "방식", "구분", "설정"], days: ["날짜", "일자", "일정", "기간", "일수", "공기", "소요일", "목록", "날"],
  crew: ["인원", "인력"], title: ["항목", "제목", "작업명", "항목명"],
  reason: ["사유", "이유"], valid: ["날짜", "일자", "기간", "기한", "날"],
};
// 값의 출처를 가리키는 시간 표현과 과거 선택 관형형을 공통으로 읽어요.
// 임의의 과거형 또는 현재 입력값을 이전 후보로 승격하지 않아요.
export const RECENT_REFERENCE_MARK = /(?:아까|방금|앞(?:서|에(?:서)?)|직전까지|직전(?:에)?|이전(?:에)?|지난번(?:에)?|그때|(?:좀|조금)\s*전(?:에)?|전(?:에)?|지금까지)/.source;
const EXTRA_TEXT_HISTORICAL_REPORT = new RegExp("(?:" + RECENT_REFERENCE_MARK + "|예전에|과거에)\\s*(?:(?:제안|확정|변경|수정|정정|입력|기재|작성|기록|설정|반영|등록|선택|결정|지정|조정|표기|표시|명시|교체|대체|저장|사용|채택|갱신)\\s*(?:했|하였)|정했|적었|썼|말했|얘기했|말씀드렸)(?:습니다|어요|죠|다)(?:[.!。]\\s*)?$");
export function isExtraHistoricalReport(text: string): boolean { return EXTRA_TEXT_HISTORICAL_REPORT.test(maskExtraQuotes(text).trim()); }
const FIRST_REFERENCE_MARK = /(?:(?:맨\s*)?처음(?:에)?|최초(?:에)?)/.source;
const SELECTION_PAST = /(?:(?:선택|결정|채택)(?:한|했던)|고른|골랐던)/.source;
const TYPED_REFERENCE = new RegExp("^\\s*[은는이가을를만도]?\\s*(?:(?:모두|전부|다)\\s*)?(?:" + RECENT_REFERENCE_MARK + "|" + FIRST_REFERENCE_MARK + ")\\s+(?:(?:말씀드린|말한|얘기한|알려드린|요청한|요청했던|정한|정정한|수정한|변경한|잡은|확정한|제안한|제시한|제시했던|정리한|적은|전달한|바꾼|고친|옮긴|" + SELECTION_PAST + ")\\s+)?(" +
  [...new Set(Object.values(REFERENCE_NOUNS).flat())].sort((a,b) => b.length-a.length).join("|") +
  ")\\s*(?:으로|로|대로|[을를])?\\s*(?:유지|사용|반영|적용|진행|써|쓰|그대로|해|두|둬|부탁|돌려|" + PLACEMENT_KEEP + ")");

function incompatibleReferences(form: AssistForm, text: string): Set<string> {
  const blocked = new Set<string>();
  for (const clause of clauses(text)) {
    const labels = [...clause.matchAll(labelPattern(form))];
    for (const [index, label] of labels.entries()) {
      const end = label.index + label[0].length;
      const referent = TYPED_REFERENCE.exec(clause.slice(end))?.[1];
      if (!referent) continue;
      let first = index;
      while (first > 0 && /^\s*(?:과|와|랑|및|하고|·|\/)\s*$/.test(
        clause.slice(labels[first-1].index + labels[first-1][0].length, labels[first].index))) first--;
      const fields = labelFields(form, clause.slice(labels[first].index, end));
      if (referent === "기간" && fields.size === 2 && fields.has("start") && fields.has("end")) continue;
      if ([...fields].some(field => !REFERENCE_NOUNS[field]?.includes(referent))) {
        for (const field of fields) blocked.add(field);
      }
    }
  }
  return blocked;
}

type ReferenceKind = "first" | "previous" | "current";
type ReferencePart = { field: string; kind: ReferenceKind; compatible: boolean; complete: boolean; bare: boolean; risky: boolean; historical: boolean; identity: boolean; evidence: string };

// 이전에 무엇을 했는지와 이번 요청의 술어를 분리해요. 과거형 자체가
// 새 값 대입이나 확정 지시는 아니에요.
const REFERENCE_MARK = new RegExp(FIRST_REFERENCE_MARK + "|" + RECENT_REFERENCE_MARK + "|현재|지금");
const REFERENCE_PAST = new RegExp(/(?:말씀드렸|말했|얘기했|알려드렸|요청했|정했|정정했|수정했|변경했|잡았|확정했|제안했|제시했|정리했|적었|전달했|바꿨|고쳤|옮겼|연장했|단축했|늘렸|줄였|입력했|설정했|등록했)던|말씀드린|말한|얘기한|알려드린|요청한|정한|정정한|수정한|변경한|잡은|확정한|제안한|제시한|정리한|적은|전달한|바꾼|고친|옮긴|연장한|단축한|늘린|줄인|입력한|설정한|등록한|있는|입력된|등록된|저장된/.source + "|" + SELECTION_PAST);
const WITHDRAW_REFERENCE = "(?:(?:"+RECENT_REFERENCE_MARK+"|"+FIRST_REFERENCE_MARK+")\\s*(?:"+REFERENCE_PAST.source+")?|기존|앞선|지난|전|(?:현재|지금)(?:\\s*(?:폼|화면|입력란)(?:에|에서)?)?(?:\\s*(?:입력한|입력된|등록한|등록된|있는|적힌))?|그|이|전체|모든)";
const WITHDRAW_LEAD = "(?:(?:아|아니|잠깐)(?=$|\\s)\\s*)?(?<reference>"+WITHDRAW_REFERENCE+")?\\s*";
const WITHDRAW_BODY = new RegExp("^"+WITHDRAW_LEAD+"(?<operation>"+WITHDRAW_OBJECT+"|"+WITHDRAW_OWNED_APPLICATION+")?\\s*(?:전부\\s*|모두\\s*|다\\s*)?(?<predicate>"+WITHDRAW_PREDICATE+")$");
const WITHDRAW_GENERIC_BODY = new RegExp("^"+WITHDRAW_LEAD+"(?<operation>(?:(?:(?:변경|수정|정정)\\s*|(?:바꾸|고치|변경하|수정하|정정하)\\s*는\\s*)?(?:요청|제안|변경안|정정안|후보|선택|내용|건|거|것|안)|변경|수정|정정)(?:[은는을를이가도만])?)?\\s*(?:전부\\s*|모두\\s*|다\\s*)?(?<predicate>"+WITHDRAW_PREDICATE+")$");
function withdrawalReference(reference:string|undefined):WithdrawalProof["reference"]{
  if(reference&&/^(?:현재|지금)/.test(reference))return "current";
  if(reference&&reference!=="기존"&&new RegExp("^(?:"+RECENT_REFERENCE_MARK+"|"+FIRST_REFERENCE_MARK+"|앞선|지난|전)").test(reference))return "previous";
  return "pending";
}
const GENERIC_REFERENCE = ["입력값", "후보", "제안", "내용", "값", "안", "것", "거", "걸"];
const REFERENCE_NOUN = [...new Set([...Object.values(REFERENCE_NOUNS).flat(), ...GENERIC_REFERENCE])].sort((a,b) => b.length-a.length).map(noun=>noun==="입력값"?"입력\\s*값":noun).join("|");
const REFERENCE_HEAD = new RegExp("^\\s*[은는이가을를만도]?\\s*(?:(?:모두|전부|다)\\s*)?(" + REFERENCE_MARK.source + ")\\s*(" + REFERENCE_PAST.source + ")?\\s*(?:그\\s+)?(" + REFERENCE_NOUN + ")?\\s*(으로|로|대로|[을를])?\\s*(.*)$");
// 동일성을 서술하는 종결도 명시적 참조에 연결된 완결 유지 술어예요.
// 문의/부정/미완결 형태는 참조 검사 대상에는 남기되 확정하지 않아요.
const REFERENCE_IDENTITY = /^(?:(?:둘\s*다|모두|전부|함께|각각)\s*)?(?:그대로|이대로|대로)\s*(?:입니다|이에요|예요|이죠|죠)$/;
const REFERENCE_IDENTITY_HEAD = /^(?:(?:둘\s*다|모두|전부|함께|각각)\s*)?(?:그대로|이대로|대로)\s*(?:입|인|일|이|예|죠|(?:가\s*)?(?:아니|아닙|아님|아냐|맞))/;
const REFERENCE_DIRECTIVE = new RegExp(/^(?:(?:둘\s*다|모두|전부|함께|각각)\s*)?(?:(?:그대로|그렇게|이대로)\s*)?(?:(?:유지|사용|반영|적용|진행|채택|선택)(?:하겠습니다|합니다|할게요|해요|해\s*(?:주세요|줘요|주십시오)|부탁(?:합니다|드립니다|해요))|(?:써|넣어|해|가)\s*(?:주세요|줘요|주십시오)|하겠습니다|할게요|부탁(?:합니다|드립니다|해요)|쓰겠습니다|쓸게요|쓸래요|가죠|가겠습니다|갈게요|(?:그대로\s*)?(?:두세요|두겠습니다|둘게요|둬\s*주세요)|그대로)$/.source + "|" + REFERENCE_IDENTITY.source);
const REFERENCE_LINK = /^(?:(?:그대로|그렇게|이대로)\s*)?(?:(?:유지|사용|반영|적용|진행|채택|선택)하고|두고|쓰고)(?:요)?(?:\s+|$)/;

function compatibleReferenceNouns(form: AssistForm, field: string): readonly string[] {
  return field === "days" ? form === "quote"
    ? ["기간", "일수", "공기", "소요일"] : ["날짜", "일자", "일정", "목록", "날"]
    : REFERENCE_NOUNS[field] ?? [];
}

/** The current-value continuation of an owned withdrawal is a second
 * complete reference proof, not a favorable suffix or a new value.
 * Both the canonical field names and the existing typed reference grammar
 * participate; generic current input references inherit the explicit owner.
 */
function currentRetentionProof(form: AssistForm, text: string, owners: string[]): CurrentRetentionProof|undefined {
  const scope=text.trim();
  if (!owners.length || RISK.test(scope) || NEGATIVE.test(scope) || CANCEL.test(scope) ||
      CONDITION_SCOPE.test(scope) || PRIOR.test(scope) || MONEY.test(scope) || CREW.test(scope) || DATE.test(scope)) return;
  const sameOwners=(fields:string[])=>fields.length===owners.length&&fields.every(f=>owners.includes(f));
  const complete=(directive:string)=>directive.trim()!=="그대로"&&
    (REFERENCE_DIRECTIVE.test(directive.trim()) ||
      new RegExp("^(?:그대로\\s*)?(?:"+PLACEMENT_KEEP+")$").test(directive.trim()));
  const leading=referencePart(form,scope);
  if (leading?.kind==="current"&&leading.compatible&&leading.complete&&!leading.bare&&
      sameOwners([leading.field])&&!/그대로$/.test(scope)) return {scope,fields:owners,reference:"current"};
  const marker=/^(?:현재|지금)\s*/.exec(scope);
  if(!marker)return;
  let rest=scope.slice(marker[0].length);
  // A form/input container is not a new field. The reference below must
  // still denote its actual value, not a proposal or a work transaction.
  rest=rest.replace(/^(?:폼|화면|달력|입력란)(?:에|의|에서)?\s*/,"");
  rest=rest.replace(/^(?:있는|적힌|적은|입력한|입력된|등록한|등록된|저장된)\s*/,"");
  const label=labelPattern(form).exec(rest);
  if(label?.index===0){
    const fields=[...labelFields(form,label[0])];
    const directive=rest.slice(label[0].length).replace(/^\s*(?:대로|으로|로|[을를은는이가])?\s*/,"");
    if(sameOwners(fields)&&complete(directive))return {scope,fields:owners,reference:"current"};
  }
  // Use the same reference nouns, particles and finite directives as
  // ordinary current/previous retention. The inherited owner must be
  // type-compatible even when no form-specific label occurs in the tail.
  const ref=REFERENCE_HEAD.exec("현재 "+rest);
  if(!ref)return;
  const [, , past, noun, particle, directive]=ref;
  if(!noun||past||/제안|제시|후보/.test(noun))return;
  const generic=["입력값","값","내용","것","거","걸"].includes(noun.replace(/\s+/g,""));
  const typed=owners.length===1&&compatibleReferenceNouns(form,owners[0]).includes(noun);
  const ending=particle==="대로"&&REFERENCE_IDENTITY_HEAD.test(particle+directive)?particle+directive:directive;
  if((generic||typed)&&complete(ending))return {scope,fields:owners,reference:"current"};
}

type SharedHistory = {fields:string[];kind:"first"|"previous";complete:boolean;evidence:string;rest?:string};
// 과거 수식어 + 명시된 필드 목록 + 하나의 완결 유지 술어를 원문
// 한 구간에서 읽어요. 쉼표/접속어를 다른 필드의 값이나 술어로 재구성하지 않아요.
function sharedHistoryReference(form:AssistForm, sentence:string):SharedHistory|undefined{
  const raw=sentence.trim();
  if(reportedRetention(raw))return;
  const head=new RegExp("^(?:(?:가능하면|혹시|아마|조건부로|만약)\\s+)*(" +
    FIRST_REFERENCE_MARK+"|"+RECENT_REFERENCE_MARK+")\\s*("+REFERENCE_PAST.source+")\\s+").exec(raw);
  if(!head)return;
  let tail=raw.slice(head[0].length);
  const fields:string[]=[];
  let duplicate=false, unfinished=false;
  while(tail){
    const label=labelPattern(form).exec(tail);
    if(!label||label.index!==0){unfinished=true;break;}
    const field=Object.entries(label.groups??{}).find(([,value])=>value!==undefined)?.[0];
    if(!field||field==="period"){unfinished=true;break;}
    if(fields.includes(field))duplicate=true;
    fields.push(field);
    tail=tail.slice(label[0].length);
    const join=/^\s*(?:과|와|이랑|랑|및|하고|[,，·/])\s*/.exec(tail);
    if(!join)break;
    tail=tail.slice(join[0].length);
  }
  if(fields.length<2)return;
  const predicate=tail.replace(/^\s*[은는이가을를만도]?\s*/,"").trim();
  const directive=predicate.replace(/^(?:둘\s*다|모두|전부|함께|각각)\s*/,"");
  // 유지하고 뒤에 명시적으로 다른 칸을 지시할 때만 연결형을 완결해요.
  // 후속 질문/값을 앞선 유지의 근거에 넣지 않아요.
  const linked=/^(?:(?:그대로|이대로|그렇게)\s*)?(?:(?:유지|사용|반영|적용|진행|채택|선택)하고|두고|쓰고)(?:요)?(?=\s|[,，])/.exec(directive);
  const rest=linked?directive.slice(linked[0].length).replace(/^\s*[,，]?\s*/,""):undefined;
  const following=rest?labelPattern(form).exec(rest):null;
  const ownedTail=!!rest&&!!following&&following.index===0;
  const evidence=linked&&ownedTail?raw.slice(0,raw.length-directive.length+linked[0].length):raw;
  const kind=/처음|최초/.test(head[1])?"first":"previous";
  const complete=!duplicate&&!unfinished&&raw.startsWith(head[1])&&
    (ownedTail||/^(?:그대로|이대로|대로|유지|사용|반영|적용)/.test(directive)&&
      !/^(?:그대로|이대로|대로)$/.test(directive)&&REFERENCE_DIRECTIVE.test(predicate))&&
    !RISK.test(evidence)&&!NEGATIVE.test(evidence)&&!CANCEL.test(evidence)&&!CONDITIONAL.test(evidence);
  return {fields:[...new Set(fields)],kind,complete,evidence,...(ownedTail?{rest}: {})};
}

// 필드명 바로 뒤의 '후보'를 가리키는 유지 지시만 이전 후보 검증에
// 연결해요. 새 값의 후보 생성이나 일반적인 현재값 유지와 섞지 않아요.
function pendingCandidateReference(form: AssistForm, part: string): { field: string; complete: boolean } | undefined {
  const label = labelPattern(form).exec(part);
  if (!label) return;
  const prefix = part.slice(0,label.index).trim();
  if (prefix && !CONDITIONAL.test(prefix)) return;
  const field = Object.entries(label.groups ?? {}).find(([, value]) => value !== undefined)?.[0];
  if (!field || field === "period") return;
  const tail = part.slice(label.index + label[0].length).match(/^\s*후보(?:안)?\s*[은는이가을를만도]?\s*(.*)$/)?.[1];
  if (tail === undefined || !/^(?:(?:가능하면|혹시|아마)\s*)?(?:그대로|이대로|유지|사용|반영|적용|진행|채택|선택|써|쓰|두|둬|가죠|가겠|갈게|현재|지금)/.test(tail)) return;
  // 기존의 연결형은 별도 절에서 검증하고 새 확정 문법으로 오인하지 않아요.
  if (REFERENCE_LINK.test(tail)) return;
  const complete = tail.trim() !== "그대로" && REFERENCE_DIRECTIVE.test(tail.trim()) &&
    !RISK.test(part) && !NEGATIVE.test(part) && !CANCEL.test(part) && !CURRENT.test(part);
  return { field, complete };
}

function referencePart(form: AssistForm, part: string): ReferencePart | undefined {
  const label = labelPattern(form).exec(part);
  if (!label) return;
  const prefix = part.slice(0,label.index).trim();
  if (prefix && !CONDITIONAL.test(prefix)) return;
  const field = Object.entries(label.groups ?? {}).find(([, value]) => value !== undefined)?.[0];
  if (!field || field === "period") return;
  const match = REFERENCE_HEAD.exec(part.slice(label.index + label[0].length));
  if (!match) return;
  const [, marker, past, noun, particle, rest] = match;
  // '후보대로입니다'에서 조사와 종결을 한 술어로 검사해요.
  const tail = particle === "대로" && REFERENCE_IDENTITY_HEAD.test(particle + rest) ? particle + rest : rest;
  if (!noun && !REFERENCE_DIRECTIVE.test(tail)) return;
  // 날짜 하나 교체, 과거값을 근거로 하는 숫자 정정, 그리고 기존의
  // 독립 필드 연결형은 원래의 교체/절 검증 경로에서 확인해요.
  if (/^(?:중|가운데)\s*/.test(tail) && (DATE.test(tail) || MONEY.test(tail) || CREW.test(tail)) ||
    /^(?:\d|[한두세네다섯여섯일이삼사오육칠팔구십백천만억]+\s*(?:원|명|일))/.test(tail) ||
    REFERENCE_LINK.test(tail)) return;
  const proposal = /제안|제시/.test(past ?? "") || noun === "제안" || noun === "후보";
  const kind: ReferenceKind = /처음|최초/.test(marker) ? "first" :
    /^(?:현재|지금)$/.test(marker) && !proposal ? "current" : "previous";
  const compatibleNouns = compatibleReferenceNouns(form,field);
  const compatible = !noun || GENERIC_REFERENCE.includes(noun.replace(/\s+/g,"")) || compatibleNouns.includes(noun);
  const risky = RISK.test(part) || NEGATIVE.test(part) || CANCEL.test(part);
  const complete = REFERENCE_DIRECTIVE.test(tail.trim()) && !risky;
  // 기존 단독 유지 표현에는 기존 판정을 그대로 사용해요. 새 검사 대상은
  // 최초 참조, 새 과거형, 또는 독립된 참조들이 공유하는 확정 술어예요.
  const historical = REFERENCE_IDENTITY_HEAD.test(tail.trim()) || kind !== "current" && (kind === "first" ||
    /던$|^(?:연장|단축|늘린|줄인|입력|설정|등록|선택|결정|채택|고른)/.test(past ?? ""));
  return { field, kind, compatible, complete, bare: !tail.trim(), risky, historical, identity: REFERENCE_IDENTITY.test(tail.trim()), evidence: part };
}

/** 문자열 값의 범위 검사에서도 같은 참조 문법을 사용해요.
 * 아직 끝나지 않은 참조나 호환되지 않는 명사도 지시 검사에 남겨야 해요. */
export function isExtraScopedReference(clause: string): boolean {
  return !!sharedHistoryReference("extra",clause) || !!referencePart("extra", clause) || !!pendingCandidateReference("extra", clause);
}

/** 쉼표로 연결된 타입별 참조는 바로 뒤의 완결된 공통 술어만 공유해요.
 *  문장 경계, 다른 대입, 질문, 부정, 조건을 넘어 술어를 끌어오지 않아요. */
function typedReferences(form: AssistForm, text: string): ReferencePart[] {
  const result: ReferencePart[] = [];
  const sentences = text.normalize("NFKC").split(/(?<!\d)\.|\.(?!\d)|[;!\n。]|(?<=[?？])/).filter(s => s.trim());
  for (const [sentenceIndex, sentence] of sentences.entries()) {
    const group=sharedHistoryReference(form,sentence);
    if(group){
      for(const field of group.fields)result.push({field,kind:group.kind,compatible:true,complete:group.complete,
        bare:false,risky:!group.complete,historical:true,identity:false,evidence:group.evidence});
      if(!group.rest)continue;
    }
    const chunks = (group?.rest??sentence).split(/(?<!\d),|,(?!\d{3}(?!\d))/).map(part => part.trim()).filter(Boolean)
      .flatMap(chunk => {
        // 독립된 앞 필드의 연결형 술어 뒤에 온 역사 참조도
        // 원래 후보의 출처를 검사해야 해요. 쉼표 공유 술어와
        // 조건부 연결은 기존 경계를 유지해요.
        if (referencePart(form, chunk)) return [chunk];
        const pieces = parts(form, chunk);
        const ref = referencePart(form, pieces.at(-1) ?? "");
        return pieces.length > 1 && ref?.historical &&
          pieces.slice(0,-1).every(part => {
            const fields = mentioned(form, part);
            return fields.length > 0 && !fields.includes(ref.field) &&
              /(?:고|고요|며)$/.test(part) && !CONDITIONAL.test(part);
          }) ? pieces : [chunk];
      });
    let pending: ReferencePart[] = [];
    const distinctPending = () => pending.length > 1 &&
      new Set(pending.map(item => item.field)).size === pending.length &&
      pending.some(item => item.kind !== "current");
    const flush = (confirmed: boolean, shared = distinctPending()) => {
      const complete = confirmed && !pending.some(item => item.risky);
      for (const ref of pending) if (shared || ref.historical) result.push({ ...ref, complete });
      pending = [];
    };
    for (const [index, part] of chunks.entries()) {
      const final = sentenceIndex === sentences.length-1 && index === chunks.length-1;
      if(reportedRetention(part)){flush(false);continue;}
      const ref = referencePart(form, part);
      if (ref?.bare) { pending.push(ref); continue; }
      if (ref) {
        // 쉼표 양쪽이 각자의 참조 대상일 때만 새 공통 술어 검사를 해요.
        // 종전의 단독 '이전 제안 유지', '아까 금액으로 반영 바랍니다' 등은
        // 기존 판정과 검증된 carry 경로를 사용해요.
        const shared = pending.length > 0 &&
          new Set([...pending, ref].map(item => item.field)).size === pending.length + 1 &&
          [...pending, ref].some(item => item.kind !== "current");
        const groupRisk = shared && pending.some(item => item.risky);
        // 문장 안에서 완결된 동일성은 뒤의 명시적 독립 칸 때문에
        // 미완결이 되지 않아요. 무대상 철회/문의나 같은 칸의 후속 절은
        // 여전히 공통 술어의 확정을 막아요.
        const groupFields = new Set([...pending, ref].map(item => item.field));
        const independentLater = ref.identity && index === chunks.length - 1 &&
          sentences.slice(sentenceIndex + 1).every(sentence => clauses(sentence).flatMap(clause => parts(form, clause)).every(clause => {
            const fields = mentioned(form, clause);
            return fields.length > 0 && fields.every(field => !groupFields.has(field));
          }));
        flush(ref.complete && (final || independentLater), shared);
        if (shared || ref.historical) result.push({ ...ref, complete: ref.complete && !groupRisk });
      } else if (pending.length && REFERENCE_DIRECTIVE.test(part)) {
        flush(final);
      } else {
        flush(false);
        if (ref) result.push(ref);
      }
    }
    flush(false);
  }
  return result;
}
const PERIOD_DATE_ITEM = /(?:(?:\d{4}\s*(?:년|[-/.])\s*)?\d{1,2}\s*(?:월|[-/.])\s*\d{1,3}\s*일?|\d{1,3}\s*일|오늘|내일|모레)/.source;
const PERIOD_DATE_MEMBER = PERIOD_DATE_ITEM + "\\s*(?:은|는|이|가|을|를|도|만)?";
const PERIOD_DATE_CONNECTOR = /(?:[,，、·]|하고|이랑|랑|과|와|및|그리고)/.source;
const PERIOD_DATE_PREFIX = new RegExp("^" + PERIOD_DATE_MEMBER + "\\s*(?:방문|실측|접수)?\\s*$");
const PERIOD_DATE_LIST_PREFIX = new RegExp("^" + PERIOD_DATE_MEMBER + "(?:\\s*" + PERIOD_DATE_CONNECTOR + "\\s*" + PERIOD_DATE_MEMBER + ")*\\s*(?:방문|실측|접수)?\\s*$");
const PERIOD_DATE_LIST_HEAD = new RegExp("^" + PERIOD_DATE_ITEM);
const PERIOD_EXPLICIT_RANGE = new RegExp(PERIOD_DATE_ITEM + "\\s*(?:부터|에서|[~∼〜～])\\s*" + PERIOD_DATE_ITEM);
/** A dated recommendation plus a complete request to see that proposal
 * is a candidate operation, not permission to execute a conditional change.
 * The deliberative preface is recognized only within this closed pair. */
export function periodBoundaryPreview(text:string):{field:"start"|"end";part:string;body:string}|undefined {
  const sentences=text.normalize("NFKC").split(/(?<!\d)\.|\.(?!\d)|[;!\n。]|(?<=[?？])/).map(s=>s.trim()).filter(Boolean);
  if(sentences.length!==2||!/^(?:그\s*)?(?:변경안|수정안|정정안|제안|후보(?:안)?|초안)(?:을|를)?\s*(?:먼저\s*|우선\s*)?보여\s*(?:주세요|줘요|주십시오)$/.test(sentences[1]))return;
  const part=sentences[0];
  const dates=[...part.matchAll(new RegExp(PERIOD_DATE_ITEM,"g"))];
  if(dates.length!==1||PERIOD_EXPLICIT_RANGE.test(part))return;
  const head=part.slice(0,dates[0].index).trim(), tail=part.slice(dates[0].index).trim();
  if(head){
    const consideration=/^(.{1,60}?[을를])\s*(?:생각하면|고려하면|감안하면)$/.exec(head);
    if(!consideration||labelFields("period",consideration[1]).size||
       DATE.test(consideration[1])||/\d|승인|허가|조건|여부|가능|만약|계약|납부/.test(consideration[1])||
       RISK.test(consideration[1])||NEGATIVE.test(consideration[1])||CANCEL.test(consideration[1])||
       CONDITION_SCOPE.test(consideration[1])||PRIOR.test(consideration[1]))return;
  }
  const ending=/(?:하는\s*(?:게|것이)|하는\s*편이)\s*(?:좋겠어요|좋겠습니다|좋을\s*것\s*같아요)$/.exec(tail);
  if(!ending)return;
  const body=tail.slice(0,ending.index).trim();
  const owners=labelFields("period",body);
  if(owners.size!==1||!(owners.has("start")||owners.has("end"))||
      !/(?:변경|수정|정정|설정|지정|조정|이동|연장|단축)$/.test(body)||
      RISK.test(body)||INDIRECT_INQUIRY.test(body)||NEGATIVE.test(body)||CANCEL.test(body)||
      CONDITION_SCOPE.test(body)||PRIOR.test(body)||KEEP.test(body)||VALUE_DENIAL.test(body))return;
  return {field:owners.has("start")?"start":"end",part,body};
}
const PERIOD_COMPACT_LIST = /^(?:(?:\d{4}\s*(?:년|[-/.])\s*)?\d{1,2}\s*(?:월|[/.])\s*\d{1,3}\s*일?)(?:\s*,\s*\d{1,3})+(?:\s*,\s*\d{1,3}\s*일)(?:[은는이가을를도])?$/;
const PERIOD_EXCLUSION_LABEL = /(?:(?:방문|실측|접수|공사|작업|시공)\s*)?(?:제외\s*(?:날짜|일자|일|할\s*날)?|불가\s*(?:날짜|일자|일)|휴무(?:일자|일)?|쉬는\s*날|빼는\s*날|안\s*되는\s*날|못\s*가는\s*날|작업\s*(?:하지\s*않는|안\s*하는)\s*날)/.source;
const PERIOD_EXCLUSION_END = /(?:(?:\s*(?:전체|목록))?\s*(?:으로|로)\s*(?:(?:새로|다시)\s*)?(?:(?:제안|확정|설정|지정|결정|입력|등록|기재|반영|선택|정)(?:합니다|하겠습니다|할게요|해요|해\s*(?:주세요|줘요|주십시오)|\s*부탁(?:합니다|드립니다|드려요|해요))|(?:정해|넣어|잡아|적어|써)\s*(?:주세요|줘요|주십시오))|(?:입니다|이에요|예요|이요|요))\s*[.!。]?$/;
function periodDateListPrefix(text: string): boolean {
  return PERIOD_DATE_LIST_PREFIX.test(text.trim()) || PERIOD_COMPACT_LIST.test(text.trim());
}
// A demonstrative list reference binds only an immediately preceding,
// complete calendar enumeration. It is not a general historical/date
// inference: explicit boundaries, competing references and risk stay out.
const PERIOD_LIST_REFERENCE = /^(?:이|위|해당)\s*(?:날짜|일자|날)(?:들)?\s*(?:을|를|은|는|만)?\s*(?:(?:방문|작업|실측)\s*)?제외\s*(?:날짜|일자|일)?\s*(?:으로|로)\s*(?:(?:넣어|잡아|정해|적어)\s*(?:주세요|줘요|주십시오|주시겠어요|주실래요)|(?:지정|설정|등록|변경|입력|반영)\s*(?:해\s*(?:주세요|줘요|주십시오|주시겠어요)|합니다|하겠습니다))$/;
const PERIOD_ADD_REFERENCE = /^(?:(?:방문|작업|실측)\s*)?제외\s*(?:날짜|일자|일)?\s*(?:으로|로)\s*(?:그|해당|이)\s*(?:날짜|일자|날)(?:들)?\s*(?:을|를|만)?\s*(?:추가|등록|반영)\s*(?:해\s*(?:주세요|줘요|주십시오|주시겠어요|주실래요)|합니다|하겠습니다)$/;
export function referencedPeriodExclusion(text:string):{list:string;antecedent:number;command:number;kind:"replace"|"add"}|undefined {
  const sentences=text.normalize("NFKC").split(/(?<!\d)\.|\.(?!\d)|[;!\n。]|(?<=[?？])/).map(s=>s.trim()).filter(Boolean);
  const found:{list:string;antecedent:number;command:number;kind:"replace"|"add"}[]=[];
  for(let i=1;i<sentences.length;i++){
    const command=sentences[i].replace(/[?？]$/,"");
    const add=PERIOD_ADD_REFERENCE.test(command);
    if(!add&&!PERIOD_LIST_REFERENCE.test(command))continue;
    const prev=sentences[i-1];
    if(add){
      // A report supplies a concrete unavailable date only to an immediately
      // following explicit add instruction. Never turn the report itself into
      // an assignment or infer a calendar day from a quantity.
      const dates=[...prev.matchAll(new RegExp(PERIOD_DATE_ITEM,"g"))];
      if(dates.length!==1||START.test(prev)||END.test(prev)||PERIOD_EXPLICIT_RANGE.test(prev)||
          CONDITION_SCOPE.test(prev)||CANCEL.test(prev)||/[?？]|아마|혹시|미정|검토|모르|가능하면/.test(prev))continue;
      const hit=dates[0], tail=prev.slice(hit.index+hit[0].length);
      if(!/^(?:\s*하루)?\s*(?:은|는|이|가|만)?\s*(?:안\s*된|불가|못\s*한|어렵|힘들)[^,.!?]{0,24}(?:하네요|합니다|했어요|했습니다|하셨어요|들었어요|들었습니다)$/.test(tail))continue;
      found.push({list:hit[0],antecedent:i-1,command:i,kind:"add"});
      continue;
    }
    if(RISK.test(prev)||NEGATIVE.test(prev)||CANCEL.test(prev)||PRIOR.test(prev)||CONDITION_SCOPE.test(prev)||
        START.test(prev)||END.test(prev)||PERIOD_EXPLICIT_RANGE.test(prev)||dateCountTokens(prev).length)continue;
    const first=new RegExp(PERIOD_DATE_ITEM).exec(prev);
    if(!first)continue;
    const prefix=prev.slice(0,first.index).trim();
    if(prefix&&!/(?:은|는|이|가|:)$/.test(prefix))continue;
    const closed=prev.slice(first.index).match(/^(.*?)(?:입니다|이에요|예요|이요|입니다요)$/);
    if(!closed||!periodDateListPrefix(closed[1])||[...closed[1].matchAll(new RegExp(PERIOD_DATE_ITEM,"g"))].length<2)continue;
    found.push({list:closed[1],antecedent:i-1,command:i,kind:"replace"});
  }
  return found.length===1?found[0]:undefined;
}
function periodListBody(text:string):string|undefined{
  const raw=text.trim();
  if(periodDateListPrefix(raw))return raw;
  // A closed causal preface supplies background, not another date owner.
  // Retain it in the actual scope for risk checks; extract only the list.
  const causal=raw.match(/^([^,，;.!?！？。\n]{1,60}(?:때문에|탓에|덕분에|라서|해서|어서|아서|으므로|므로))\s+(.+)$/);
  if(causal&&!DATE.test(causal[1])&&!labelFields("period",causal[1]).size&&
      !RISK.test(causal[1])&&!NEGATIVE.test(causal[1])&&!CANCEL.test(causal[1])&&periodDateListPrefix(causal[2]))return causal[2];
}
function periodOwnedListPrefix(text:string):boolean{
  if(periodListBody(text))return true;
  // 별도 경계/날짜가 없는 조건 선행부는 목록의 소유권만 연결해요.
  // 조건 자체는 없애지 않아 이후 의도 검사에서 전체 목록을 보류해요.
  const conditional=text.trim().match(/^([^,，;.!?！？。\n]{0,48}(?:라면|다면|으면|이면|하면|되면|경우))\s+(.+)$/);
  return !!conditional&&!DATE.test(conditional[1])&&!labelFields("period",conditional[1]).size&&periodDateListPrefix(conditional[2]);
}
function postposedExclusionList(text: string): boolean {
  if (reportedPeriodExclusion(text)) return true;
  // 뒤 표지까지 날짜 목록만 이어져야 해요. 이후의 조건/다른 칸은
  // 기존 의도 검사에 남기고 앞쪽 날짜의 소유권만 결합해요.
  for (const hit of text.matchAll(new RegExp(PERIOD_EXCLUSION_LABEL + "|(?:(?:방문|실측|접수)\\s*)?(?:못\\s*(?:가|하|받)|안\\s*(?:돼|되|가)|쉽니다|쉬어요|빼\\s*(?:줘|주))","g"))) {
    if (periodOwnedListPrefix(text.slice(0,hit.index))) return true;
  }
  return false;
}
const PREPOSED_EXCLUSION = new RegExp("^" + PERIOD_EXCLUSION_LABEL + "\\s*(?:은|는|이|가|을|를|[:=])?\\s*");
function preposedExclusionList(text: string): boolean {
  const label = PREPOSED_EXCLUSION.exec(text);
  if (!label) return false;
  const body = text.slice(label[0].length);
  return periodDateListPrefix(body) || periodDateListPrefix(body.replace(PERIOD_EXCLUSION_END,""));
}
// 목록 마지막 날짜에 붙은 일수는 같은 제외일 목록의 수량이에요.
// 날짜/수량의 일치는 readDateListQuantity에서 검사하고, 원문 술어와
// 조건·문의·부정은 그대로 의도 검사에 전달해요.
function countedPeriodListContinuation(text: string): boolean {
  if(labelFields("period",text).size || PERIOD.test(text))return false;
  return dateCountTokens(text).some(token => {
    const prefix=text.slice(0,token.start).replace(/(?:총|모두|합계|일수는?)\s*$/,"").trim();
    return periodDateListPrefix(prefix) && !DATE.test(text.slice(token.end));
  });
}
const DATE_LIST_CONTINUATION = new RegExp("^" + PERIOD_DATE_MEMBER + "(?:\\s*(?:이에요|예요|입니다|이요|요))?[?？]?$");

// A completed unavailability report followed by an explicit exclusion request
// refers to the entire preceding enumeration, not the final calendar token.
// A declared cardinality is a constraint, not a separate calendar value.
export function reportedPeriodExclusion(text: string): {list:string;count?:number}|undefined {
  const raw=text.trim();
  const match=/^(.+?)(?:은|는|이|가)\s+(.{1,60}?)(?:수\s*없으니|불가능하니|어려우니|곤란하니|안\s*되니|못\s*하니)\s+(.+)$/.exec(raw);
  if(!match)return;
  const [,list,reason,tail]=match;
  if(!periodDateListPrefix(list)||DATE.test(reason)||PERIOD.test(reason)||
      labelFields("period",reason).size||RISK.test(reason)||NEGATIVE.test(reason)||
      CANCEL.test(reason)||PRIOR.test(reason)||CONDITION_SCOPE.test(reason))return;
  const command=new RegExp("^(?:(?:(\\d{1,3}|"+COUNTER_WORD+")\\s*(?:개의?\\s*)?(?:날짜|일자|날)(?:을|를|은|는|만)\\s+))?"+PERIOD_EXCLUSION_LABEL+"(.*)$").exec(tail);
  if(!command||!PERIOD_EXCLUSION_END.test(command[2])||
      command[2].replace(PERIOD_EXCLUSION_END,"").trim())return;
  const count=command[1]===undefined?undefined:counterValue(command[1])??Number(command[1]);
  return {list,count};
}

/** 명시적 제외일 대입의 실제 목록 구간. 숫자의 달력 유효성은
 * 호출자의 기존 날짜 파서에서 확인하며, 미지원 문법은 추측하지 않아요. */
export function explicitPeriodExclusionList(clause: string): string | undefined {
  const raw = clause.trim();
  const report=reportedPeriodExclusion(raw);
  if(report)return report.list;
  const pre = PREPOSED_EXCLUSION.exec(raw);
  if (pre) {
    const body = raw.slice(pre[0].length);
    const list = body.replace(PERIOD_EXCLUSION_END,"").trim();
    return list !== body && periodDateListPrefix(list) ? list : undefined;
  }
  for (const hit of raw.matchAll(new RegExp(PERIOD_EXCLUSION_LABEL,"g"))) {
    const list = periodListBody(raw.slice(0,hit.index));
    if (list && PERIOD_EXCLUSION_END.test(raw.slice(hit.index+hit[0].length)) &&
        raw.slice(hit.index+hit[0].length).replace(PERIOD_EXCLUSION_END,"").trim()==="") return list;
  }
  return undefined;
}

/** 목록 교체의 직접적인 두 날짜 관계. 날짜를 읽은 것 자체로는
 * 교체 후보를 만들 수 없으며 실제 이전/현재 목록과 대조해요. */
export function directDateMove(text: string): { old: string; next: string; prefix: string } | undefined {
  const relation = new RegExp("(" + PERIOD_DATE_ITEM + ")\\s*(?:을|를|은|는|만|에서)\\s*(" +
    PERIOD_DATE_ITEM + ")\\s*(?:으로|로)\\s*(?:(?:안|못)\\s*)?(?:옮기|옮겨|옮길|미루|미뤄|미룰|밀어|밀다|밀기|당기|당겨|당길|변경|수정|정정|교체|대체|이동|바꾸|바꿔|바꿀|고치|고쳐|고칠)","g");
  const matches = [...text.matchAll(relation)];
  if (matches.length!==1) return;
  const hit = matches[0];
  return {old:hit[1],next:hit[2],prefix:text.slice(0,hit.index)};
}
const VAT_DENIAL = /^(?:부가(?:가치)?세|세금|VAT)(?:를|을|는|은)?\s*(?:포함하지|제외하지|면세하지)\s*(?:말라는|않으라는)\s*(?:뜻|의미)(?:이|가|는|은)?\s*(?:아니었어요|아니었습니다|아니에요|아닙니다)$/i;
const VAT_CONFIRM = /^(?:(?:부가(?:가치)?세|세금|VAT)(?:는|은|를|을)?\s*)?(포함|별도|면세)(?:으로|로)\s*(?:확정(?:해\s*주세요|합니다|하겠습니다|할게요)|정해\s*주세요)$/i;
const VAT_EXCLUSION_CONFIRM = /^(?:부가(?:가치)?세|세금|VAT)(?:는|은|를|을)?\s*포함하지\s*않는\s*(?:방식|형태)(?:으로|로)\s*(?:확정(?:해\s*주세요|합니다|하겠습니다|할게요)|정해\s*주세요)$/i;
const DATE_PROTECT = /^(?:(현재|지금|앞서|아까|이전|방금)\s*)?(?:(?:화면|폼|달력|캘린더)(?:에|의)?\s*)?(?:(?:있는|입력한|입력된|등록한|등록된|선택한|제안한|정한|말한)\s*)?(?:(작업일|공사일|작업\s*날짜|공사\s*날짜|제외일|휴무일)\s*)?((?:\d{4}년\s*)?(?:\d{1,2}월\s*)?\d{1,2}일|\d{4}-\d{2}-\d{2}|\d{1,2}\/\d{1,2})(?:은|는|을|를|만|도)?\s*(?:건드리지|손대지|변경하지|수정하지|바꾸지)\s*(?:말고|마시고|마세요|말아\s*주세요)$/;

type DateProtection = { date: string; reference: "current" | "previous" | null };
type VatConfirmation = { value: "included" | "excluded" | "exempt"; evidence: string };

function confirmedVatCorrection(form: AssistForm, text: string): VatConfirmation | undefined {
  if (form !== "quote" && form !== "extra") return undefined;
  const segments = clauses(text);
  const excluded = segments.findIndex(part => VAT_EXCLUSION_CONFIRM.test(part));
  if (excluded >= 0 && !segments.some((part, index) => index !== excluded &&
    (mentioned(form, part).includes("vat") || !mentioned(form, part).length &&
      (RISK.test(part) || NEGATIVE.test(part) || CANCEL.test(part)) ||
      index < excluded && CONDITIONAL.test(part)))) {
    return { value: "excluded", evidence: segments[excluded] };
  }
  const denied = segments.findIndex(part => VAT_DENIAL.test(part));
  if (denied < 0 || denied + 1 >= segments.length) return undefined;
  const final = segments[denied + 1].match(VAT_CONFIRM);
  if (!final || segments.some((part, index) => index !== denied && index !== denied + 1 &&
    (mentioned(form, part).includes("vat") || !mentioned(form, part).length && (RISK.test(part) || NEGATIVE.test(part) || CANCEL.test(part))))) return undefined;
  // 앞 절에 조건이 걸린 경우 별도의 확정이라고 단정하지 않아요.
  if (denied > 0 && segments.slice(0, denied).some(part => CONDITIONAL.test(part))) return undefined;
  return { value: final[1] === "포함" ? "included" : final[1] === "별도" ? "excluded" : "exempt", evidence: segments[denied + 1] };
}

function clauses(text: string): string[] {
  return text.normalize("NFKC").split(/(?<!\d),|,(?!\d{3}(?!\d))|(?<!\d)\.|\.(?!\d)|[;!\n。]|(?<=[?？])/)
    .map((part) => part.trim()).filter(Boolean);
}

/** 쉼표로 이어진 제외일 목록만 같은 대상에 묶어요. 다른 요청 절은 합치지 않아요. */
function periodClauses(text: string): string[] {
  const sentences = text.normalize("NFKC").split(/(?<!\d)\.|\.(?!\d)|[;!\n。]|(?<=[?？])/);
  return sentences.flatMap(sentence => {
    const result: string[] = [];
    for (const raw of sentence.split(/,/)) {
      const part = raw.trim();
      if (!part) continue;
      const previous = result.at(-1);
      if (previous && EXCLUDED.test(previous) && !START.test(previous) && !END.test(previous) &&
          (DATE_LIST_CONTINUATION.test(part) ||
            preposedExclusionList(previous) && (countedPeriodListContinuation(part) ||
              periodDateListPrefix(part.replace(PERIOD_EXCLUSION_END,"")) &&
              part.replace(PERIOD_EXCLUSION_END,"")!==part))) result[result.length - 1] = previous + ", " + part;
      else {
        // 앞에 날짜만 열거하고 마지막 날짜 뒤에 불가/제외 술어를 붙이는
        // 요청에서는 그 목록 전체가 제외일이에요. 확정된 경계 절은 합치지 않아요.
        let joined = part;
        if (postposedExclusionList(part)) {
          // 명시 월의 압축 목록 중간에 숫자만 있는 경우에도 그
          // 연속 구간 전체가 문법을 만족할 때만 앞쪽을 합쳐요.
          let first=result.length;
          while (first>0 && (periodOwnedListPrefix(result[first-1]) || /^\d{1,3}$/.test(result[first-1]))) first--;
          for(let i=first;i<result.length;i++){
            const candidate=[...result.slice(i),part].join(", ");
            if(postposedExclusionList(candidate)){
              joined=candidate;
              result.splice(i);
              break;
            }
          }
        }
        result.push(joined);
      }
    }
    return result.flatMap(part => {
      // 범위 확정 뒤에 별도 불가 날짜를 붙인 경우는 경계와 제외 요청을
      // 각각 검사해요. 범위 자체를 못 받는다는 말은 분리하지 않아요.
      for (const join of part.matchAll(/(?:가능하|잡|정하|확정하|지정하|설정하|등록하)(?:고|며)\s*/g)) {
        const end = join.index + join[0].length;
        const prefix = part.slice(0,end).trim();
        const tail = part.slice(end).trim();
        if (PERIOD_EXPLICIT_RANGE.test(prefix) && !EXCLUDED.test(prefix) && !UNAVAILABLE.test(prefix) &&
            PERIOD_DATE_LIST_HEAD.test(tail) && (EXCLUDED.test(tail) || UNAVAILABLE.test(tail)) &&
            !START.test(tail) && !END.test(tail) && !dateCountTokens(tail).length) return [prefix,tail];
      }
      return [part];
    });
  });
}

function mentioned(form: AssistForm, text: string): string[] {
  // 일정이 맞으면 인원을 정하겠다는 말은 일정 자체의 변경 요청이 아니에요.
  if (form === "dates") {
    const consequent = text.match(/^(?:공사\s*|작업\s*|시공\s*)?(?:일정|날짜|일자)(?:이|가|은|는)?\s*(?:맞으면|맞는다면|가능하면|확정되면|정해지면)\s*(.+)$/)?.[1];
    if (consequent && new RegExp(CREW_LABEL).test(consequent) && !new RegExp(WORK_DATE_LABEL).test(consequent) && !DATE.test(consequent)) text = consequent;
  }
  const fields = labelFields(form, text);
  if (form === "quote" || form === "extra") {
    const modeWithdrawal = vatModeWithdrawal(form, text);
    if (modeWithdrawal?.bound) fields.add("vat");
    const tax = new RegExp(VAT_LABEL,"i").exec(text);
    if (tax || !fields.size && standaloneVatTokens(text).length) fields.add("vat");
    const taxContext = tax && VAT_CONTEXT.test(text.slice(0, tax.index)) && !MONEY.test(text);
    // '금액과 별개로 청구'의 금액은 세금 값 구간 내부의 관계 목적어예요.
    // 토큰 바깥에 별도 금액 명칭/수량이 있으면 그 칸은 그대로 유지해요.
    const vatTokens=scopedVatTokens(text);
    const otherAmount=[...text.matchAll(labelPattern(form))].some(hit=>hit.groups?.amount!==undefined&&
      !vatTokens.some(token=>hit.index>=token.start&&hit.index+hit[0].length<=token.end));
    if(vatTokens.length&&!otherAmount&&!MONEY.test(text))fields.delete("amount");
    if (taxContext) fields.delete("amount");
    else if (MONEY.test(modeWithdrawal?.bound ? text.slice(modeWithdrawal.span.length) : text) ||
      /견적\s*(?:[은는이가을를:]\s*)?(?:\d|그대로|유지|취소|미정)/.test(text)) fields.add("amount");
    if (form === "quote") {
      if (!fields.has("valid") && quoteDurationMention(text)) fields.add("days");
    } else {
      if (/때문|퍼져서|발견해서/.test(text)) fields.add("reason");
    }
  } else if (form === "dates") {
    if (CREW.test(text)) fields.add("crew");
    if (DATE.test(text)) fields.add("days");
  } else {
    const excluded = EXCLUDED.test(text) || DATE.test(text) && UNAVAILABLE.test(text) && !START.test(text) && !END.test(text);
    if (excluded) fields.add("excluded");
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
  if(form==="period"&&reportedPeriodExclusion(clause))return [clause];
  // 명시된 행위 객체들이 하나의 철회 술어를 공유하면 그 전체를
  // 공동 절로 유지해요. 일반 명사·독립 대입·조건은 이 경로로 합치지 않아요.
  const grouped=Array.from(labelFields(form,maskExtraQuotes(clause)));
  if(grouped.length>1&&withdrawalObject(form,clause,grouped,undefined)&&
    !RISK.test(maskExtraQuotes(clause))&&!NEGATIVE.test(maskExtraQuotes(clause)))return [clause];
  const targets = labelPattern(form);
  const cuts = [0];
  for (const hit of clause.matchAll(targets)) {
    let at = hit.index;
    const previous = cuts.at(-1)!;
    // '별도 세금'은 앞의 다른 칸에 별도를 남겨 두지 않아요.
    if(hit.groups?.vat!==undefined){
      const mode=scopedVatTokens(clause).find(token=>token.end<=at&&TAX_REVERSE_GAP.test(clause.slice(token.end,at)));
      if(mode)at=mode.start;
    }
    if (at <= previous) continue;
    const prefix = clause.slice(previous, at).trim();
    const before = mentioned(form, prefix);
    const next = Object.entries(hit.groups ?? {}).flatMap(([name, value]) =>
      value === undefined ? [] : name === "period" ? ["start","end"] : [name]);
    // 값·술어 없이 연결한 대상 목록은 뒤의 공통 술어를 같이 읽어요.
    if (/^(?:(?:은|는|을|를|이|가|과|와|및|하고|랑|도|만)\s*)*$/.test(prefix.replace(targets, "").trim())) continue;
    // "12월 11일은 방문 제외일"의 날짜는 뒤에 붙은 대상의 값이에요.
    if (form === "period" && (PERIOD_DATE_PREFIX.test(prefix) ||
        next.includes("excluded") && periodOwnedListPrefix(prefix.replace(/\s*(?:방문|실측|접수|공사|작업|시공)\s*$/,"")))) continue;
    if (next.includes("vat") && VAT_CONTEXT.test(prefix)) continue;
    if(next.includes("amount")&&scopedVatTokens(clause).some(token=>at>=token.start&&at+hit[0].length<=token.end))continue;
    // 세금 방식에 붙은 '포함된 금액이 아닙니다'의 금액은 독립
    // 대입 대상이 아니에요. 그 부정 술어를 세금 절에서 떼지 않아요.
    if(next.includes("amount")&&before.includes("vat")&&
        /포함된\s*$/.test(prefix)&&/^(?:금액|총액|비용)(?:이|은|는|가)?\s*(?:아니|아닙|아냐|아님)/.test(clause.slice(at)))continue;
    // 조건으로 연결한 두 절은 서로 독립된 확정 요청이 아니에요.
    if (CONDITION_LINK.test(prefix)) continue;
    if (before.length && next.some((field) => !before.includes(field))) cuts.push(at);
  }
  return cuts.map((start, i) => clause.slice(start, cuts[i + 1] ?? clause.length).trim()).filter(Boolean);
}

/** 일수라는 수량을 달력의 일자와 별도로 보존해요. */
export function dateCountTokens(text: string) {
  return durationTokens(text).filter(token => {
    const tail = text.slice(token.end);
    // 날짜가 하루 밀린다는 말은 전체 날짜 목록의 개수가 아니에요.
    if (/^\s*(?:만\s*)?(?:밀려|밀렸|밀리|미뤄|미루|당겨|당기|앞당|늦춰|늦추|연기|이동)/.test(tail)) return false;
    return token.native || /(?:총|모두|합계|일수는?)\s*$/.test(text.slice(0, token.start)) || /^\s*(?:간|동안)/.test(tail);
  });
}

type IntentSegment = {part:string;sentence:number};
function intentSegments(form: AssistForm, text: string): IntentSegment[] {
  const sentences=text.normalize("NFKC").split(/(?<!\d)\.|\.(?!\d)|[;!\n。]|(?<=[?？])/).map(part=>part.trim()).filter(Boolean);
  const split=(part:string)=>(form==="period"?periodClauses(part):clauses(part)).flatMap(clause=>parts(form,clause));
  const segments=sentences.flatMap((raw,sentence)=>{
    const group=sharedHistoryReference(form,raw);
    const pieces=group?[group.evidence,...(group.rest?split(group.rest):[])]:split(raw);
    return pieces.map(part=>({part,sentence}));
  });
  if (form === "quote" || form === "extra") return segments.flatMap(segment=>{
    const {part,sentence}=segment;
    // 명시 세금 대입 뒤 '나머지는 이전 제안 유지'는 그 세금
    // 칸의 유지 지시가 아니에요. 완결 연결형 경계에서 분리해요.
    for(const join of part.matchAll(/(?:하고요?|하며|이고|이며)\s+(?=나머지|다른\s*(?:값|후보|칸))/g)){
      const end=join.index+join[0].length;
      const prefix=part.slice(0,end).trim();
      const tail=part.slice(end).trim();
      if(scopedVatTokens(prefix).length&&KEEP.test(tail)&&!RISK.test(prefix)&&!NEGATIVE.test(prefix)&&!CANCEL.test(prefix))return [{part:prefix,sentence},{part:tail,sentence}];
    }
    return [segment];
  });
  if (form !== "period") return segments;
  const result: IntentSegment[] = [];
  for (const segment of segments) {
    const {part}=segment;
    const prior = result.at(-1);
    if (prior && EXCLUDED.test(prior.part) && !START.test(prior.part) && !END.test(prior.part) && dateCountTokens(prior.part).length &&
      !labelFields(form, part).size && DATE.test(part) && !PERIOD.test(part)) {
      result[result.length - 1] = {...prior,part:prior.part + ", " + part};
    } else result.push(segment);
  }
  return result;
}

// 견적에서 앞의 유일한 일수/유효기간 대상 뒤에 수량·날짜만
// 이어진 절은 같은 칸의 값 근거이자 경쟁/정정 지시로 검사해요.
function ownedFields(form: AssistForm, part: string, last: string[]): string[] {
  const group=sharedHistoryReference(form,part);
  if(group)return group.fields;
  const fields = mentioned(form,part);
  // 경계 뒤 명칭 없는 날짜 후보는 그 경계의 경쟁 값이에요.
  // 새 범위·제외일·명시 경계는 원래 소유권을 유지해요.
  if(form==="period"&&last.length===1&&["start","end"].includes(last[0])&&
      !labelFields(form,part).size&&DATE.test(part)&&!PERIOD.test(part)&&!UNAVAILABLE.test(part)&&
      /(?:후보|대안|안|정해|지정|변경|수정|확정|가능|미정|인지)/.test(part))return last;
  if (fields.length || form !== "quote" || last.length !== 1) return fields;
  const field = last[0];
  if (field !== "days" && field !== "valid") return fields;
  const continuation = /^(?:아니(?:요|오)?|정정|다시|→|->)/.test(part) ||
    (field === "days" ? classifyDurationQuantity(part).mentioned : durationTokens(part).length > 0) || DATE.test(part);
  return continuation ? [field] : fields;
}

/** 뒤의 철회/보류가 독립된 다른 칸인지 공통 의도 판정으로 확인해요.
 * 대상이 불명확하면 기존 후보를 안전하게 보류해요. */
export function scopedWithdrawalAffects(form: AssistForm, text: string, field: string): boolean {
  return intentSegments(form, text).some(({part}) => {
    if (!CANCEL.test(part) && !/보류/.test(part)) return false;
    const intent = requestIntent(form, part);
    return intent.cancelAll || intent.unboundWithdrawal || !intent.requested.size || intent.requested.has(field);
  });
}

/** 값 정규화와 의도 검사가 동일한 절과 연결된 필드 소유권을 사용해요. */
export function resolvedIntentSegments(form: AssistForm, text: string): (IntentSegment & {fields:string[];dependent:boolean})[] {
  let last: string[] = [];
  let previousSentence=-1, dependent=false;
  const reference=form==="period"?referencedPeriodExclusion(text):undefined;
  const boundaryPreview=form==="period"?periodBoundaryPreview(text):undefined;
  const segments=intentSegments(form,text);
  return segments.map((segment,index) => {
    const {part,sentence}=segment;
    if(sentence!==previousSentence)dependent=false;
    previousSentence=sentence;
    let fields = reference&&(sentence===reference.antecedent||sentence===reference.command)
      ? ["excluded"] : ownedFields(form,part,last);
    // 뒤의 명시적인 대입을 설명하는 연결형 원인은 사유 칸의
    // 독립 요청이 아니에요. 단독/후행 사유와 실제 사유 라벨은 유지해요.
    if(form==="extra"&&fields.length===1&&fields[0]==="reason"&&extraDependentBackground(segments,index))fields=[];
    if (fields.length && vatModeWithdrawal(form,part)?.bound !== false) last = fields;
    // 조건의 효력은 동일 문장의 모든 연결된 대입에 남아요.
    const preview=boundaryPreview?.part===part&&fields.length===1&&fields[0]===boundaryPreview.field?boundaryPreview.body:
      form==="dates"&&fields.length===1&&labelFields(form,part).size===1?previewAssignmentBody(part):undefined;
    dependent ||= CONDITION_SCOPE.test(preview??part);
    const result={...segment,fields,dependent};
    if(COMPLETED_INQUIRY.test(part)&&!POLITE_EXECUTION.test(part))dependent=false;
    return result;
  });
}

const EXTRA_DEPENDENT_NOMINAL=/(?:때문에?|탓에|덕분에|아서|어서|여서|해서|돼서|라서|으므로|므로|으니까|니까|아져|어져|여져|되어|되어서|퍼져서|발견해서)(?:요)?\s*$/;
function extraDependentBackground(segments:IntentSegment[],index:number):boolean {
  const {part,sentence}=segments[index];
  if(labelFields("extra",part).size||!EXTRA_DEPENDENT_NOMINAL.test(part))return false;
  const assignment=(s:IntentSegment)=> {
    const owners=labelFields("extra",s.part);
    return owners.size===1&&!owners.has("reason")&&
      (CHANGE_ASSIGNMENT.test(s.part)||POLITE_EXECUTION.test(s.part))&&!reportedRetention(s.part);
  };
  // 독립 대입 앞의 원인 절과, 독립 대입 뒤의 별개 미완결 문장을
  // 배경으로만 읽어요. 쉼표 목록의 후행 사유와 단독 사유는 보존해요.
  const next=segments[index+1];
  return !!next&&assignment(next)||segments.slice(0,index).some(s=>s.sentence<sentence&&assignment(s));
}
export function unownedExtraDependentClause(text:string,clause:string):boolean {
  const segments=intentSegments("extra",maskExtraQuotes(text));
  const needle=compactProof(maskExtraQuotes(clause));
  return segments.some((s,index)=>compactProof(s.part)===needle&&extraDependentBackground(segments,index));
}

/** 값 정규화에서 다른 필드에 속한 날짜/수량을 섞지 않아요. */
export function scopedFieldParts(form: AssistForm, text: string, field: string): string[] {
  return resolvedIntentSegments(form,text).filter(scope=>scope.fields.includes(field)).map(scope=>scope.part);
}

/** Independent scope facts for validated wire operations. Unlike requestIntent
 * this does not infer a positive operation or parse/normalize a value. */
export function operationScopeFacts(form: AssistForm, text: string) {
  return resolvedIntentSegments(form,text).map(scope => {
    const labels = labelFields(form,maskExtraQuotes(scope.part));
    // A typed calendar endpoint outranks the inherited last-field heuristic.
    if (form === "period") {
      if (/착수|개시/.test(scope.part)) labels.add("start");
      if (/완료|완공|마치/.test(scope.part)) labels.add("end");
    }
    const endpoint = form === "period" && labels.size === 1 &&
      (labels.has("start") || labels.has("end")) && !PERIOD_EXPLICIT_RANGE.test(scope.part);
    const fields = endpoint ? [...labels] : scope.fields;
    return { ...scope, fields, labels: [...labels] };
  });
}

/** Full original clause risk, including conditions, references and denial.
 * Only a completed execution-request ending may remove interrogative politeness. */
export function operationScopeRisk(form: AssistForm, part: string, field: string): boolean {
  const preview=form==="dates"?previewAssignmentBody(part):undefined;
  const labels=preview?labelFields(form,part):undefined;
  if(preview&&labels?.size===1&&labels.has(field))part=preview;
  if(form==="dates"&&field==="crew")part=crewFinalAssignmentBody(part)??part;
  const polite = POLITE_EXECUTION.exec(part) ?? (form === "extra" ? POLITE_TEXT_PROPOSAL.exec(part) : null);
  const riskText = polite ? part.slice(0,polite.index) : part;
  const date = form === "period" || form === "dates" || field === "valid";
  return reportedRetention(part) || RISK.test(riskText) || INDIRECT_INQUIRY.test(riskText) || CONDITION_SCOPE.test(part) ||
    CONDITION_LINK.test(part) || NEGATIVE.test(part) || CANCEL.test(part) || VALUE_DENIAL.test(part) ||
    /지\s*(?:마|말|않|못)|확인만|검토|문의|보고만|(?:계약|납부|주문|예약)\s*(?:요청|신청|취소|철회)/.test(part) ||
    !!polite && /(?:안|못)\s*$/.test(riskText) ||
    date && /(?:인지|일지|맞는지)[^,.!?]{0,35}(?:확인|검토|생각)\s*중|아직\s*확인\s*중|(?:할|정할|지정할|설정할|옮길|바꿀|변경할|확정할|넣을)\s*생각/.test(part) ||
    (field === "amount" || field === "crew" || form === "quote" && field === "days") &&
      (BOUNDS.test(part) || field === "crew" && CREW_LIMIT.test(part));
}

/** A typed VAT token must be adjacent to its tax label, independent of the
 * positive assignment ending. The caller checks the entire owned clause. */
export function operationVatModes(part: string): VatMode[] {
  const labels = [...part.matchAll(TAX_LABEL_TOKEN)];
  const tokens = [...part.matchAll(TAX_TOKEN)];
  // Unlabelled competing tax tokens in the same clause are not a unique
  // value. A value word used for checking rather than billing is not owned.
  if (tokens.length !== 1 || /확인|검토|비용을\s*보여/.test(part)) return [];
  return tokens.flatMap(hit => {
    const start=hit.index, end=start+hit[0].length;
    if (!labels.some(label=>label.index+label[0].length<=start&&TAX_GAP.test(part.slice(label.index+label[0].length,start)) ||
      end<=label.index&&TAX_REVERSE_GAP.test(part.slice(end,label.index))) &&
      !new RegExp("^"+TAX_LABEL,"i").test(hit[0])) return [];
    return [hit.groups?.negative!==undefined||hit.groups?.billing!==undefined||hit.groups?.excluded!==undefined
      ? "excluded" as const : hit.groups?.included!==undefined ? "included" as const : "exempt" as const];
  });
}

const compactProof = (value: string) => value.normalize("NFKC").replace(/\s+/g, "").replace(/[,.!;。]+$/, "");

// A speculative parser hold is not evidence of a request. Keep field-owned
// mentions and unresolved values, independently of the parser's action guess.
export function hasIndependentFieldSignal(form: AssistForm, text: string, field: string): boolean {
  const scopes = resolvedIntentSegments(form, text);
  return scopes.some(({part,fields}) => {
    const labels = labelFields(form, part);
    if (form === "period" && (field === "start" || field === "end")) {
      if ((field === "start" ? START : END).test(part)) return true;
      if (EXCLUDED.test(part) && !START.test(part) && !END.test(part) && !PERIOD_EXPLICIT_RANGE.test(part)) return false;
      return fields.includes(field) && (DATE.test(part) || /미정|보류|못\s*정|애매|모르겠/.test(part));
    }
    if (labels.has(field)) return true;
    if (!fields.includes(field)) return false;
    if (field === "amount") return MONEY.test(part);
    if (field === "crew") return CREW.test(part);
    if (field === "vat") return scopedVatTokens(part).length > 0;
    if (field === "days" && form === "quote") return classifyDurationQuantity(part).mentioned;
    if (field === "title" || field === "reason") return extraTextTargets(part).some(t => t.field === field);
    return DATE.test(part) || /미정|보류|못\s*정|애매|모르겠/.test(part);
  });
}

// For canonical VAT operations the actual adjacent tax token is sufficient.
// It must still own the field and survive competing/negative/conditional scope checks.
export function ownsLiteralVatEvidence(form: AssistForm, text: string, evidence: string, value: unknown): boolean {
  if (form !== "quote" && form !== "extra") return false;
  const needle = compactProof(evidence);
  const modes = scopedVatTokens(evidence);
  if (!needle || !compactProof(text).includes(needle) || !new RegExp(TAX_LABEL,"i").test(evidence) ||
      modes.length !== 1 || modes[0].mode !== value) return false;
  const scopes = resolvedIntentSegments(form,text);
  const owned = scopes.filter(s => labelFields(form,s.part).has("vat"));
  const actual = owned.find(s => compactProof(s.part).includes(needle));
  if (!actual || actual.dependent || owned.length !== 1) return false;
  if (RISK.test(actual.part) || NEGATIVE.test(actual.part) || /지\s*(?:마|말|않|못)|검토|확인만/.test(actual.part) || INDIRECT_INQUIRY.test(actual.part) ||
      CANCEL.test(actual.part) || PRIOR.test(actual.part) || VALUE_DENIAL.test(actual.part)) return false;
  // Inspect lexical tokens even when the positive predicate parser rejects
  // the full clause; do not accept a second, contrary tax choice.
  const tokens = [...actual.part.matchAll(TAX_TOKEN)];
  if (tokens.length !== 1) return false;
  const tail = actual.part.slice(tokens[0].index + tokens[0][0].length);
  return !/^\s*(?:비용|금액|공사|작업|항목|확인만|여부)/.test(tail) &&
    !scopes.some(s => s.sentence === actual.sentence && s !== actual && s.dependent);
}

// 필드 표지와 실제 근거 범위만 대조해요. 서버의 검증된 대입에
// 규칙의 완결 술어 문법을 다시 요구하지 않아요.
export function ownsPeriodExclusionEvidence(text: string, evidence: string): boolean {
  const needle = compactProof(evidence);
  if (!needle || !compactProof(text).includes(needle) ||
      !EXCLUDED.test(evidence) || START.test(evidence) || END.test(evidence) ||
      PERIOD_EXPLICIT_RANGE.test(evidence)) return false;
  const scopes = resolvedIntentSegments("period", text);
  const relevant = scopes.filter(s => s.fields.includes("start") || s.fields.includes("end") || s.fields.includes("excluded"));
  const reference=referencedPeriodExclusion(text);
  const linked=!!reference&&relevant.every(s=>s.fields.length===1&&s.fields[0]==="excluded"&&
    (s.sentence===reference.antecedent||s.sentence===reference.command));
  if (!relevant.length || !linked&&new Set(relevant.map(s => s.sentence)).size !== 1 ||
      relevant.some(s => s.dependent || START.test(s.part) || END.test(s.part) || PERIOD_EXPLICIT_RANGE.test(s.part) ||
        (RISK.test(s.part) && !POLITE_EXECUTION.test(s.part)) || NEGATIVE.test(s.part) ||
        INDIRECT_INQUIRY.test(s.part) || VALUE_DENIAL.test(s.part) || CANCEL.test(s.part))) return false;
  // 서버가 제시한 제외일 근거 바깥의 날짜는 제외일로 재해석하지 않아요.
  // 설명 접두어와 요청 종결은 잘릴 수 있지만 날짜 자체는 모두 있어야 해요.
  const dateTokens = (s: string) => [...s.matchAll(new RegExp(PERIOD_DATE_ITEM, "g"))].map(m => compactProof(m[0]));
  const all = dateTokens(relevant.map(s => s.part).join(", "));
  const grounded = dateTokens(evidence);
  return all.length > 0 && all.length === grounded.length && all.every((value, i) => value === grounded[i]);
}

// 명시한 현재값 참조의 소유권과 위험만 확인해요. 서버가 돌려준
// 근거가 요청 종결어 일부를 생략해도, 원문의 실제 절에서 확인합니다.
export function ownsCurrentRetentionEvidence(form: AssistForm, text: string, field: string, evidence: string): boolean {
  const needle = compactProof(evidence);
  const current = /현재|지금|기존(?:에)?|저장된|원래|처음\s*(?:상태|값|금액|설정)|수정\s*전/;
  if(needle&&compactProof(text).includes(needle)){
    const scopes=resolvedIntentSegments(form,text);
    const own=scopes.filter(s=>s.fields.includes(field));
    const actual=own.find(s=>compactProof(s.part)===needle);
    // Only a complete, uniquely owned restoration is self-proving. A
    // preceding unbound cancellation needs separate verified prior-value
    // evidence; this predicate does not decide that session relationship.
    if(actual&&!actual.dependent&&own.length===1&&actual.fields.length===1&&
        originalValueIdentity(form,field,actual.part)&&
        scopes.every(s=>s===actual||s.fields.length>0||
          !RISK.test(s.part)&&!NEGATIVE.test(s.part)&&!CANCEL.test(s.part)))return true;
  }
  if(needle&&compactProof(text).includes(needle)&&ACTUAL_IDENTITY.test(evidence)&&
      /고정|유지|대로|건드리지|손대지/.test(evidence)){
    const scopes=resolvedIntentSegments(form,text), owned=scopes.filter(s=>s.fields.includes(field));
    const actual=owned.find(s=>compactProof(s.part).includes(needle)&&!reportedRetention(s.part));
    const identity=actual&&currentIdentityClause(form,field,actual.part);
    const next=actual&&scopes[scopes.indexOf(actual)+1];
    const owners=labelFields(form,evidence);
    if(owned.length===1&&actual&&!actual.dependent&&actual.fields.length===1&&owners.size===1&&owners.has(field)&&
        (identity==="complete"||identity==="linked"&&next&&next.sentence===actual.sentence&&!next.dependent&&
          next.fields.length>0&&!next.fields.includes(field)))return true;
  }
  if (!needle || !compactProof(text).includes(needle) || !current.test(evidence) ||
      !KEEP.test(evidence) || PRIOR.test(evidence) || /후보|변경안/.test(evidence)) return false;
  const scopes = resolvedIntentSegments(form,text);
  const relevant = scopes.filter(s => s.fields.includes(field));
  const actual = relevant.find(s => compactProof(s.part).includes(needle)&&!reportedRetention(s.part));
  if (!actual || actual.fields.length !== 1 || actual.dependent) return false;
  const risk = (s: string) => RISK.test(s) || INDIRECT_INQUIRY.test(s) || CONDITION_SCOPE.test(s) ||
    NEGATIVE.test(s) || VALUE_DENIAL.test(s) || /(?:계약|납부|주문|예약)\s*(?:요청|신청|취소|철회)/.test(s);
  if (risk(actual.part) || /(?:후보|변경안)/.test(actual.part) || PRIOR.test(actual.part)) return false;
  // 근거 조각만이 아니라 원문 절 전체에서 대상 필드를 확인해요.
  const owners = labelFields(form,evidence);
  const ownedScope = labelFields(form,actual.part);
  if (!ownedScope.has(field) || [...owners].some(k => k !== field) ||
      [...ownedScope].some(k => k !== field)) return false;
  if (!owners.has(field) && !withdrawalObject(form,actual.part,[field],undefined)?.currentRetention) return false;
  if (MONEY.test(actual.part) || CREW.test(actual.part) || DATE.test(actual.part)) return false;
  // '현재 후보'는 현재 입력값이 아니에요. 완료된 유지 요청의
  // 연결형은 다음에 독립 필드가 이어지는 경우에만 허용해요.
  if (/(?:그대로|유지|두고)$/.test(actual.part) &&
      !scopes.slice(scopes.indexOf(actual)+1).some(s => s.sentence === actual.sentence &&
        s.fields.length > 0 && !s.fields.includes(field) && !s.dependent)) return false;
  const cancel = CANCEL.exec(actual.part);
  if (cancel && !withdrawalObject(form,actual.part,[field],undefined)) {
    const before = actual.part.slice(0,cancel.index);
    if (!labelFields(form,before).has(field) || !/(?:변경|수정|정정|조정)\s*[은는을를이가]?$/.test(before.trim()) ||
        !current.test(actual.part.slice(cancel.index))) return false;
  }
  // 다른 같은 필드 절은 독립적인 변경 요청의 철회인 경우에만
  // 함께 허용해요. 새 값·이전값·문의와 경쟁하면 해제하지 않아요.
  return relevant.every(s => s === actual || s.sentence<actual.sentence&&reportedRetention(s.part) || !s.dependent && !risk(s.part) &&
    !!withdrawalObject(form,s.part,[field],undefined) && !PRIOR.test(s.part));
}

/** Linked identity belongs to the full utterance, not the evidence fragment.
 * A truncated server evidence span must still point at its unique actual
 * owned scope; a dangling connector in isolation never establishes retention. */
export function ownsLinkedCurrentRetentionEvidence(form:AssistForm,text:string,field:string,evidence:string):boolean {
  const intent=requestIntent(form,text);
  const linked=intent.linkedCurrentKeep.get(field),needle=compactProof(evidence);
  if(!linked||!needle||!compactProof(text).includes(needle)||!needle.includes(compactProof(linked)))return false;
  if(compactProof(linked)===needle)return ownsCurrentRetentionEvidence(form,text,field,evidence);
  // Longer evidence may include the following owned instruction in the same
  // sentence, but never an unrelated sentence or a changed linked object.
  return requestIntent(form,evidence).linkedCurrentKeep.get(field)===linked;
}

export type RequestIntent = {
  requested: Set<string>;
  blocked: Set<string>;
  serverOnly: Set<string>;
  carryConflicts: Set<string>;
  durationReaffirmation?: string;
  priorReferences: Map<string, "first" | "previous">;
  vatWithdrawalModes: Set<VatMode>;
  unboundWithdrawal: boolean;
  replacements: Set<string>;
  currentKeep: Set<string>;
  currentKeepEvidence: Map<string, string[]>;
  linkedCurrentKeep: Map<string, string>;
  implicitKeepEvidence: Map<string, string[]>;
  cancelEvidence: Map<string, string[]>;
  withdrawalProofs: Map<string, WithdrawalProof>;
  protectedDates: Map<string, DateProtection[]>;
  vatConfirmation?: VatConfirmation;
  canceled: Set<string>;
  cleared: Set<string>;
  reassigned: Set<string>;
  cancelAll: boolean;
};

export function requestIntent(form: AssistForm, text: string): RequestIntent {
  const out: RequestIntent = { requested: new Set(), blocked: new Set(), serverOnly: new Set(), carryConflicts: new Set(),
    priorReferences: new Map(), vatWithdrawalModes: new Set(), unboundWithdrawal: false,
    replacements: new Set(), currentKeep: new Set(), currentKeepEvidence: new Map(), linkedCurrentKeep:new Map(),
    implicitKeepEvidence: new Map(), cancelEvidence: new Map(), withdrawalProofs:new Map(), protectedDates: new Map(),
    canceled: new Set(), cleared: new Set(), reassigned: new Set(), cancelAll: false };
  const vatConfirmation = confirmedVatCorrection(form, text);
  let last: string[] = [];
  let prefixRisk = false;
  const priorKeep = new Set<string>(), newAssignment = new Set<string>();
  const typed = typedReferences(form, text);
  const resolved = resolvedIntentSegments(form, text);
  const boundaryPreview=form==="period"?periodBoundaryPreview(text):undefined;
  const segments = resolved.map(scope=>scope.part);
  const reportedFields=new Set<string>();
  for (const [index, {part,fields,dependent}] of resolved.entries()) {
    if(reportedRetention(part)){
      for(const field of fields)reportedFields.add(field);
      continue;
    }
    const group=sharedHistoryReference(form,part);
    const pendingReference = pendingCandidateReference(form, part);
    const modeWithdrawal = vatModeWithdrawal(form, part);
    if (modeWithdrawal && !modeWithdrawal.bound) {
      // '포함 선택', '별도 후보'만으로 세금인지 다른 선택인지 단정하지
      // 않아요. 전체 취소도 아니므로 기존 후보를 되살리거나 보류를 풀지 않아요.
      out.unboundWithdrawal = true;
      continue;
    }
    if (modeWithdrawal) out.vatWithdrawalModes.add(modeWithdrawal.mode);
    const preview=boundaryPreview?.part===part&&fields.length===1&&fields[0]===boundaryPreview.field?boundaryPreview.body:
      form==="dates"&&fields.length===1&&labelFields(form,part).size===1?previewAssignmentBody(part):undefined;
    const crewRelation=form==="dates"&&fields.length===1&&fields[0]==="crew"?crewFinalAssignment(part):undefined;
    const semanticPart=preview??crewRelation?.body??part;
    const polite = POLITE_EXECUTION.exec(semanticPart) ??
      (form==="extra"&&/\uE000/.test(part)?POLITE_TEXT_PROPOSAL.exec(part):null);
    const riskText = polite ? semanticPart.slice(0,polite.index) : semanticPart;
    const dateField = form==="period"||form==="dates"||form==="quote"&&fields.includes("valid");
    const risk = dependent || RISK.test(riskText) || INDIRECT_INQUIRY.test(riskText) || CONDITION_LINK.test(semanticPart) ||
      dateField&&/(?:인지|일지|맞는지)[^,.!?]{0,35}(?:확인|검토|생각)\s*중|아직\s*확인\s*중/.test(semanticPart) ||
      dateField&&/(?:할|정할|지정할|설정할|옮길|바꿀|변경할|확정할|넣을)\s*생각/.test(semanticPart) ||
      (form==="extra"||dateField)&&/^만약\s/.test(semanticPart),
      // A field label describing non-working days is not a prohibition on
      // changing that field. Retain any real negative predicate after it.
      negative = NEGATIVE.test(form==="period"&&fields.length===1&&fields[0]==="excluded"
        ? part.replace(/^작업\s*하지\s*않는\s*날(?=\s|[은는이구가을를만도:])/u,"제외일") : part) ||
        !!polite&&/(?<![가-힣])(?:안|못)\s*$/.test(riskText), cancel = CANCEL.test(part)&&
        !(fields.length===1&&originalValueIdentity(form,fields[0],part));
    const following = segments[index + 1] ? mentioned(form, segments[index + 1]) : [];
    const proof=cancel&&!prefixRisk&&!risk&&!negative?withdrawalObject(form,part,fields,modeWithdrawal):undefined;
    const withdrawal=!!proof&&(!/(?:하고요?|하며)$/.test(proof.predicate)||!!proof.currentRetention||fields.length>0&&
      following.length>0&&following.every(field=>!fields.includes(field)));
    if (!fields.length) {
      if (withdrawal) {
        if (!last.length) out.cancelAll = true;
        for (const field of last) {
          out.blocked.add(field);
          out.canceled.add(field);
          out.cancelEvidence.set(field, [part]);
          // 앞선 같은 글의 새 대입과 결합한 무대상 철회는 기존
          // 후보를 막되 독립적인 현재값 복귀의 증명으로 쓰지 않아요.
        }
      } else if (risk || negative || cancel) {
        if (last.length) for (const field of last) {
          if (!out.cleared.has(field) || negative || cancel || CONDITIONAL.test(part)) out.blocked.add(field);
        }
        else {
          prefixRisk = CONDITIONAL.test(part) || risk;
          if(cancel&&!proof)out.unboundWithdrawal=true;
        }
      }
      continue;
    }
    // 날짜의 정상적인 범위 및 부정 가용일은 금액/인원 범위와 다르게 처리해요.
    for (const field of fields) {
      out.requested.add(field);
      if(preview||crewRelation)out.serverOnly.add(field);
      // 같은 칸에 다시 지시가 나오면 앞의 단독 철회/유지 근거로 풀지 않아요.
      if (!withdrawal) {
        out.canceled.delete(field);
        out.cancelEvidence.delete(field);
        out.withdrawalProofs.delete(field);
      }
      out.currentKeep.delete(field);
      out.currentKeepEvidence.delete(field);
      out.implicitKeepEvidence.delete(field);
      // 목록 원소의 보존 요청은 전체 필드의 이전값 유지가 아니에요.
      // 보호할 날짜와 목록 출처는 별도 교체 검증에 전달해요.
      const protection = (form === "dates" && field === "days" || form === "period" && field === "excluded")
        && fields.length === 1 && !prefixRisk && !risk && !cancel ? part.match(DATE_PROTECT) : null;
      if (protection && (form === "dates" ? !/제외|휴무/.test(protection[2] ?? "") : /제외|휴무/.test(protection[2] ?? ""))) {
        const refs = out.protectedDates.get(field) ?? [];
        const reference = protection[1] ? (/현재|지금/.test(protection[1]) ? "current" : "previous") : null;
        out.protectedDates.set(field, [...refs, { date: protection[3], reference }]);
        out.serverOnly.add(field);
        out.replacements.add(field);
        continue;
      }
      const duration = form === "quote" && field === "days" ? classifyDurationQuantity(part) : undefined;
      const hasValue = duration ? duration.mentioned : field==="vat"?scopedVatTokens(part).length>0:MONEY.test(part) || CREW.test(part) || DATE.test(part);
      // 정확한 문자열 구간을 가린 자리도 새 값의 존재 근거예요.
      // 참조/철회/문의 구절은 가리지 않으므로 기존 후보와 경쟁하면 보류해요.
      const explicitTextValue = form==="extra" && (field==="title"||field==="reason") &&
        extraTextTargets(part).some(hit=>hit.field===field&&/\uE000/.test(part.slice(hit.valueStart)));
      // 같은 칸의 명시적 이전값 유지와 별도 새 대입은 경쟁 지시예요.
      // 이력의 값/출처가 일치해도 옛 후보를 선택 가능하게 복원하지 않아요.
      const explicitPending = pendingReference?.field === field;
      const referencesPrior = !!group || PRIOR.test(part) && KEEP.test(part) || explicitPending;
      if (referencesPrior) priorKeep.add(field);
      else if (!PRIOR.test(part) && !KEEP.test(part) && !prefixRisk && !risk && !negative && !cancel &&
        !VALUE_DENIAL.test(part) && (hasValue || explicitTextValue || CHANGE_ASSIGNMENT.test(part) || CLEAR.test(part))) newAssignment.add(field);
      if (priorKeep.has(field) && newAssignment.has(field)) {
        out.carryConflicts.add(field);
      }
      if (explicitPending) {
        out.serverOnly.add(field);
        out.priorReferences.set(field, "previous");
        if (!pendingReference.complete || prefixRisk || risk || negative || cancel) out.blocked.add(field);
      }
      const bounds = (field === "amount" || field === "crew" || duration) &&
        (BOUNDS.test(semanticPart) || field === "crew" && CREW_LIMIT.test(semanticPart) || duration?.risk);
      const reassign = REASSIGN.test(part) && !risk && !negative &&
        hasValue;
      if (reassign) {
        out.reassigned.add(field);
        out.serverOnly.add(field);
      }
      if (out.cancelAll && !risk && !negative && !cancel && !bounds &&
          hasValue) {
        out.reassigned.add(field);
        out.serverOnly.add(field);
      }
      if (prefixRisk || risk || negative || cancel && !reassign || bounds ||
        form==="extra"&&(field==="title"||field==="reason")&&isExtraHistoricalReport(part) ||
        form === "quote" && (field === "days" || field === "valid") && VALUE_DENIAL.test(part)) out.blocked.add(field);
      if (!prefixRisk && !risk && !negative && !cancel && CLEAR.test(part)) out.cleared.add(field);
      if (withdrawal) {
        out.canceled.add(field);
        out.cancelEvidence.set(field, [part]);
        out.withdrawalProofs.set(field,proof!);
      }
      const currentReference = CURRENT.test(part) || fields.length === 1 && CURRENT_HANDS_OFF.test(part);
      const currentEllipse = currentReference && /(?:으로|로)$/.test(part);
      const identity=fields.length===1?currentIdentityClause(form,field,part):undefined;
      const next=resolved[index+1];
      const actualIdentity=identity==="complete"||identity==="linked"&&next&&next.sentence===resolved[index].sentence&&
        !next.dependent&&next.fields.length>0&&!next.fields.includes(field);
      if (((KEEP.test(part) || currentEllipse) && currentReference||actualIdentity) && !prefixRisk && !risk && !negative && !cancel &&
        !PRIOR.test(part) && !hasValue) {
        out.currentKeep.add(field);
        out.currentKeepEvidence.set(field, [...(out.currentKeepEvidence.get(field) ?? []), part]);
        out.serverOnly.add(field);
      } else if (KEEP.test(part) && PRIOR.test(part)) out.serverOnly.add(field);
      else if (!explicitPending && KEEP.test(part) && !currentReference && !PRIOR.test(part) && !prefixRisk && !risk && !negative && !cancel &&
          !hasValue) {
        out.implicitKeepEvidence.set(field, [part]);
        out.serverOnly.add(field);
      }
      if ((form === "dates" && field === "days" || form === "period" && field === "excluded") &&
        DATE.test(part) && (MOVE.test(part) || directDateMove(part))) {
        out.replacements.add(field);
        out.serverOnly.add(field);
      }
    }
    last = fields;
    prefixRisk = CONDITION_LINK.test(part);
  }
  if (form === "dates" || form === "period") {
    const field = form === "dates" ? "days" : "excluded";
    const scope = scopedFieldParts(form,text,field).join(", ");
    const counts = dateCountTokens(scope);
    if (counts.length && !DATE.test(counts.reduceRight((rest, token) => rest.slice(0,token.start) + " ".repeat(token.end-token.start) + rest.slice(token.end),scope))) out.blocked.add(field);
  }
  for (const field of incompatibleReferences(form, text)) {
    out.requested.add(field);
    out.blocked.add(field);
  }
  // 타입이 있는 참조는 새 값 대입으로 처리하지 않아요. 기존의 명시 유지와
  // 독립 필드 변경은 그대로 보존하되, 같은 칸의 경쟁 지시는 풀지 않아요.
  for (const ref of typed) {
    out.requested.add(ref.field);
    out.serverOnly.add(ref.field);
    if (!ref.complete || !ref.compatible) {
      out.blocked.add(ref.field);
      continue;
    }
    const sameFieldParts = segments.filter(part => !reportedRetention(part)&&mentioned(form, part).includes(ref.field) &&
      !ref.evidence.includes(part) && !part.includes(ref.evidence));
    if (sameFieldParts.some(part => !typed.some(other => other.field === ref.field &&
        (other.evidence.includes(part) || part.includes(other.evidence))) &&
      (MONEY.test(part) || CREW.test(part) || DATE.test(part) || CHANGE_ASSIGNMENT.test(part) ||
       form==="quote"&&ref.field==="days"&&classifyDurationQuantity(part).mentioned ||
       form==="extra"&&extraTextTargets(part).some(hit=>hit.field===ref.field&&/\uE000/.test(part.slice(hit.valueStart))) ||
       CANCEL.test(part) || NEGATIVE.test(part) || RISK.test(part) || KEEP.test(part)))) {
      out.carryConflicts.add(ref.field);
    }
    if (typed.some(other => other.field === ref.field && other.kind !== ref.kind)) out.blocked.add(ref.field);
    if (ref.kind === "current") {
      out.currentKeep.add(ref.field);
      out.currentKeepEvidence.set(ref.field, [...(out.currentKeepEvidence.get(ref.field) ?? []), ref.evidence]);
    } else {
      out.priorReferences.set(ref.field, ref.kind);
    }
  }
  // 보고만 있는 칸의 이전 후보를 되살리거나 보류를 해제하지 않아요.
  // 보고 후 독립적으로 다시 내린 지시는 기존 증명 경로를 사용해요.
  for(const field of reportedFields)if(!out.requested.has(field)){
    out.requested.add(field);
    out.blocked.add(field);
  }
  // 경쟁 관계 자체는 계속 보류해요. 완결된 독립 이전 유지절과
  // 별도 명시 일수 대입절이 있을 때만 세션이 같은 값인지 검증할
  // 근거를 남겨요. 무대상 연결절, 다른 위험, 첫 후보 참조는 제외해요.
  if (form === "quote" && out.carryConflicts.has("days") && !out.blocked.has("days") &&
      !out.cancelAll && !out.unboundWithdrawal && !out.currentKeep.has("days") &&
      out.priorReferences.get("days") !== "first") {
    const scoped = scopedFieldParts(form,text,"days");
    if (scoped.length === 2 && scoped.every(part => labelFields(form,part).size === 1 && labelFields(form,part).has("days"))) {
      const ref = referencePart(form,scoped[0]);
      const next = scoped[1];
      const quantity = classifyDurationQuantity(next);
      if (ref?.field === "days" && ref.kind === "previous" && ref.compatible && ref.complete &&
          !/(?:그대로|대로)$/.test(scoped[0]) && quantity.tokens.length === 1 && !quantity.risk &&
          CHANGE_ASSIGNMENT.test(next) && !PRIOR.test(next) && !KEEP.test(next)) {
        out.durationReaffirmation = next;
      }
    }
  }
  for (const field of out.carryConflicts) out.blocked.add(field);
  // 같은 칸에 다른 대입·문의·유지 등 경쟁 절이 있으면 철회가
  // 미확정 후보를 차단할 수는 있어도 보류 해제의 증명은 아니에요.
  for(const [field,proof] of out.withdrawalProofs){
    const at=resolved.findIndex(scope=>scope.part===proof.scope);
    if(out.unboundWithdrawal||out.carryConflicts.has(field)||out.priorReferences.has(field)||
        resolved.some(scope=>scope.fields.includes(field)&&scope.part!==proof.scope)||
        resolved.slice(at+1).some(scope=>!scope.fields.length&&
          (CANCEL.test(scope.part)||CURRENT.test(scope.part)&&KEEP.test(scope.part)||
           !COMPLETED_INQUIRY.test(scope.part)&&(CONDITIONAL.test(scope.part)||NEGATIVE.test(scope.part)||RISK.test(scope.part))))){
      out.withdrawalProofs.delete(field);
    }
  }
  // 앞 절의 유지/취소를 뒤의 경쟁 지시와 혼동하지 않아요.
  for (const field of out.currentKeep) if (out.blocked.has(field)) out.currentKeep.delete(field);
  for (const field of out.implicitKeepEvidence.keys()) if (out.blocked.has(field)) out.implicitKeepEvidence.delete(field);
  // 현재값 유지의 연결형은 뒤의 명시적인 다른 칸과 하나의 문장을
  // 이루는 경우에만 독립적인 완료 근거로 써요. 같은 칸을 다시
  // 언급했거나 전역 철회/조건이 있는 경우에는 새 보류를 풀지 않아요.
  if(!out.cancelAll&&!out.unboundWithdrawal)for(const field of out.currentKeep){
    const owned=resolved.filter(scope=>scope.fields.includes(field));
    if(owned.length!==1||owned[0].fields.length!==1||owned[0].dependent||
        out.carryConflicts.has(field)||out.canceled.has(field)||out.priorReferences.has(field))continue;
    const scope=owned[0],next=resolved[resolved.indexOf(scope)+1];
    if(!next||next.sentence!==scope.sentence||next.dependent||!next.fields.length||
        next.fields.includes(field)||!(CURRENT_KEEP_LINK.test(scope.part)||currentIdentityClause(form,field,scope.part)==="linked")||!/(?:고요?|며)$/.test(scope.part)||
        !out.currentKeepEvidence.get(field)?.includes(scope.part))continue;
    out.linkedCurrentKeep.set(field,scope.part);
  }
  if (vatConfirmation && !out.cancelAll && !out.canceled.has("vat") &&
      !resolved.some(scope=>scope.dependent&&scope.fields.includes("vat"))) {
    out.vatConfirmation = vatConfirmation;
    out.requested.add("vat");
    out.serverOnly.add("vat");
    out.blocked.delete("vat");
  }
  return out;
}

/** 다른 필드의 질문·미정은 명시적 한글 금액 자체를 무효화하지 않아요. */
export function amountRiskText(text: string): string {
  return resolvedIntentSegments("quote",text)
    .filter(({fields})=>!fields.length || fields.includes("amount"))
    .map(({part})=>part).join(" ");
}
