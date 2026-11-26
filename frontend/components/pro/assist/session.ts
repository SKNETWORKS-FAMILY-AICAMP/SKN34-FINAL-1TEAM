// 업체 도우미 한 화면의 후보 상태. 화면에는 미리보기만 보여 주고 확인 때만 폼 setter를 불러요.
import { AssistApiError, interpretAssist } from "./api";
import {
  type AssistCurrent, type AssistForm, type AssistRequest, type AssistResponse,
  type AssistValue, type CandidateField, type RuleOptions,
  copyCurrent, formKeys, freshPrevious, mergeKeptFields, previousCandidate, readResponse, sameValue, validCombinedPeriod, wireOperation, uniqueWireField,
} from "./protocol";
import { ruleFields, ruleResponse } from "./rules";
import { assistInputIssues, amountToConfirm, historicalAmountCorrection, historicalAmountReport } from "./guard";
import { calendarEvidenceDates, extraTextClearRequested, extraTextGrounding, extraTextScopes, foreignExtraTextScope, hasExtraAssignmentSuffix, hasExtraOtherFieldLabel, hasExtraTextLabelPrefix, isExtraClearCommandValue, isExtraReferenceValue, parseDates, parseQuote, positiveExtraTextValue, readCrew, readCrewReplacement, readDateListQuantity, readExplicitPeriodBoundary, readExplicitPeriodExclusions, readQuoteDuration, readQuoteValidity, readVatValue, uncertainCompoundAmount, vatEvidenceMatches, type ExtraTextGrounding } from "./parse";
import { writtenMoney } from "./writtenMoney";
import { crewFinalAssignment, directDateMove, explicitPeriodExclusionList, referencedPeriodExclusion, hasIndependentFieldSignal, operationScopeRisk, ownsCurrentRetentionEvidence, ownsLinkedCurrentRetentionEvidence, ownsLiteralVatEvidence, ownsPeriodExclusionEvidence, periodBoundaryPreview, previewAssignmentBody, RECENT_REFERENCE_MARK, reportedRetention, requestIntent, resolvedIntentSegments, scopedFieldParts, vatSemanticTokens, type RequestIntent, type WithdrawalProof } from "./requestIntent";
import { classifyDurationQuantity, COUNTER_WORD } from "./quantity";
import { activeHold, reduceFieldHold, type FieldHold, type HoldEvent, type HoldOutcome } from "./holdTransition";
import { analyzeOperations, literalPlan, verifySimpleLiteral } from "./canonicalOperation";
import { relativeQuantityOperation, referencedExclusionAddOperation } from "./fieldOperation";

export type InputIssues = ReturnType<typeof assistInputIssues>;
type FieldHoldDelta = NonNullable<InputIssues["fieldHolds"]>;
type LiveState = { current: AssistCurrent; manualVersions?: Readonly<Record<string, number>> };

const FIELD_HOLD = "말씀하신 값을 확인해 적용하거나 해당 칸을 직접 입력해 주세요.";

function actionable(field: CandidateField): boolean {
  return field.status === "valid" && (field.action === "set" || field.action === "clear") ||
    field.status === "conflict" && field.alternatives.length >= 2;
}

const proofText = (s: string) => s.normalize("NFKC").replace(/\s+/g, "");
const clauseProofText = (s: string) => proofText(s).replace(/[,.!;。]+$/, "");

const wireField = uniqueWireField;

function canonicalFresh(field: CandidateField, request: AssistRequest, raw: unknown,
    intent: string, source: string | null): boolean {
  const operation = wireOperation(raw,request,field);
  return operation?.kind === intent && operation.valueSource === source;
}

// Compute first, then compare with the intact wire. An operand is never an
// absolute set and an unresolved transform must not fall back to literal proof.
function computedFieldOperation(request:AssistRequest,field:string,opts:RuleOptions){
  const prior=request.previous_candidate?.fields.some(f=>f.field===field&&(f.action==="set"||f.action==="clear"))??false;
  return relativeQuantityOperation(request.form,request.text,field,request.current,opts,prior)??
    referencedExclusionAddOperation(request.form,request.text,field,request.current,opts,prior);
}

function verifiedComputedField(field:CandidateField,request:AssistRequest,opts:RuleOptions,raw:unknown):boolean|undefined {
  const operation=computedFieldOperation(request,field.field,opts);
  if(!operation)return;
  const item=wireField(raw,field.field);
  return operation.value!==undefined&&field.status==="valid"&&field.action==="set"&&sameValue(field.value,operation.value)&&
    canonicalFresh(field,request,raw,"explicit_change","derived")&&item?.correction==null&&
    clauseProofText(field.evidence)===clauseProofText(operation.evidence);
}

// A final total is not a licence to ignore the stated delta or rejected
// quantity. Independently verify the real current/prior baseline and the
// exact new-value evidence before either positive candidate path runs.
function verifiedCrewFinalRelation(field:CandidateField,request:AssistRequest,intent:RequestIntent,raw:unknown):boolean|undefined {
  if(request.form!=="dates"||field.field!=="crew")return;
  const parts=scopedFieldParts("dates",request.text,"crew");
  const matches=parts.flatMap(part=>{const relation=crewFinalAssignment(part);return relation?[{part,relation}]:[];});
  if(!matches.length)return;
  if(matches.length!==1||parts.length!==1||intent.cancelAll||intent.unboundWithdrawal||intent.blocked.has("crew")||
    intent.currentKeep.has("crew")||intent.carryConflicts.has("crew")||field.action!=="set"||field.status!=="valid")return false;
  const {part,relation}=matches[0], evidence=proofText(field.evidence), body=clauseProofText(relation.body);
  if(field.value!==relation.next||readCrew(field.evidence).value!==relation.next||
      !proofText(part).includes(evidence)||!evidence||
      !(body.includes(clauseProofText(field.evidence))||evidence.includes(body)))return false;
  const current=request.current.crew, previous=request.previous_candidate;
  const prior=previous?.fields.find(f=>f.field==="crew"&&f.action==="set");
  const priorValue=prior&&typeof prior.value==="number"&&Number.isSafeInteger(prior.value)&&
    (previous?.sources?.crew==="rule"||previous?.sources?.crew==="llm")&&!!(previous.origins?.crew??previous.request_id)&&
    Object.prototype.hasOwnProperty.call(previous.baselines??{},"crew")&&
    sameValue(previous.baselines?.crew??null,current??null)&&!!proofText(prior.evidence)&&
    proofText(previous.text).includes(proofText(prior.evidence))&&readCrew(prior.evidence).value===prior.value?prior.value:undefined;
  if(relation.kind==="bound"){
    // Scalar corrections are deliberately not retained by candidateFor.
    // Check the exact raw relation here, including the independently
    // verified pending value, instead of treating the normalized set as proof.
    const item=wireField(raw,"crew"), correction=item?.correction as Record<string,unknown>|undefined;
    if(!item||item.intent_kind!=="explicit_change"||item.value_source!=="utterance"||item.status!=="valid"||
        item.action!=="set"||item.value!==field.value||item.source!==field.source||
        item.origin_request_id!==request.request_id||item.evidence!==field.evidence||
        !Array.isArray(item.alternatives)||item.alternatives.length||!correction||correction.kind!=="replace"||
        correction.rejected_value!==relation.first||typeof correction.rejected_evidence!=="string"||
        typeof correction.accepted_evidence!=="string")return false;
    const old=clauseProofText(correction.rejected_evidence),next=clauseProofText(correction.accepted_evidence);
    const at=part.lastIndexOf(relation.body);
    return relation.next<relation.first&&relation.reference!=="current"&&priorValue===relation.first&&at>=0&&
      !!old&&!!next&&proofText(part.slice(0,at)).includes(old)&&body.includes(next)&&
      readCrew(correction.rejected_evidence).value===relation.first&&readCrew(correction.accepted_evidence).value===relation.next;
  }
  return !prior&&relation.reference!=="previous"&&canonicalFresh(field,request,raw,"explicit_change","utterance")&&
    typeof current==="number"&&Number.isSafeInteger(current)&&current+relation.direction*relation.first===relation.next;
}

function verifiedDatePreview(field:CandidateField,request:AssistRequest,opts:RuleOptions,intent:RequestIntent,raw:unknown):boolean|undefined {
  if(request.form!=="dates")return;
  const parts=scopedFieldParts("dates",request.text,field.field);
  if(!parts.some(part=>previewAssignmentBody(part)))return;
  if(parts.length!==1||intent.blocked.has(field.field)||intent.cancelAll||intent.unboundWithdrawal||
      intent.currentKeep.has(field.field)||intent.priorReferences.has(field.field)||
      field.action!=="set"||!canonicalFresh(field,request,raw,"explicit_change","utterance")||
      operationScopeRisk("dates",parts[0],field.field))return false;
  if(field.field==="days")return verifiedLiteralList(field,request,opts,intent,raw);
  return field.field==="crew"&&readCrew(parts[0]).value===field.value&&
    readCrew(field.evidence).value===field.value&&proofText(parts[0]).includes(proofText(field.evidence));
}

/** A complete literal replacement needs no derived old->new relation.
 * Do not waive a relation check for a partial move or competing dates. */
function verifiedLiteralList(field: CandidateField, request: AssistRequest, opts: RuleOptions,
    intent: RequestIntent, raw: unknown): boolean {
  if (!(request.form === "period" && field.field === "excluded" ||
        request.form === "dates" && field.field === "days") ||
      field.action !== "set" || !Array.isArray(field.value) ||
      !canonicalFresh(field,request,raw,"explicit_change","utterance") ||
      intent.blocked.has(field.field) || intent.protectedDates.has(field.field) ||
      intent.currentKeep.has(field.field) || intent.priorReferences.has(field.field)) return false;
  const scopes = scopedFieldParts(request.form,request.text,field.field);
  if (scopes.some(clause => directDateMove(clause))) return false;
  const proof = calendarEvidenceDates(field.evidence,opts.today,opts.baseMonth);
  if (!proof || !sameValue(proof,[...field.value].sort())) return false;
  if (request.form === "period") return ownsPeriodExclusionEvidence(request.text,field.evidence);
  const all = calendarEvidenceDates(scopes.join(", "),opts.today,opts.baseMonth);
  return !!all && sameValue(all,proof) && !!proofText(field.evidence) &&
    proofText(request.text).includes(proofText(field.evidence));
}

// 완결된 소유 객체까지 확인해요. 기존 연결형 관용구는 원문에
// 후속 독립 대입이 있는 경우에만 '하고' 앞의 전체 객체를 허용해요.
function withdrawalEvidenceMatches(proof:WithdrawalProof,evidence:string,field:string):boolean{
  const scoped=clauseProofText(proof.scope),actual=clauseProofText(evidence);
  const current=proof.currentRetention;
  return !!actual&&(actual===scoped||
    current?.reference==="current"&&current.fields.includes(field)&&actual===clauseProofText(current.scope)||
    /없(?:던|었던)(?:걸로|일로|것으로)하고(?:요)?$/.test(scoped)&&
    actual===scoped.replace(/하고(?:요)?$/,""));
}

function withdrawalScalar(field:string,value:string,request:AssistRequest,options:RuleOptions):number|undefined{
  if(field==="amount"){
    if(!/^[\d,일이삼사오육칠팔구영공십백천만억.\s]+\s*(?:원)?$/.test(value))return;
    return parseQuote(value,options.today).amount??amountToConfirm(value,options.today,request.form);
  }
  if(field==="crew"){
    if(!new RegExp("^(?:\\d+|"+COUNTER_WORD+")\\s*(?:명|인)$").test(value))return;
    return readCrew(value).value;
  }
  return undefined;
}

function withdrawalValueMatches(proof:WithdrawalProof,field:string,request:AssistRequest,options:RuleOptions|undefined):boolean{
  // 변경하려던 행위 자체와 특정 이전 후보 선택은 별개예요.
  // 전자는 실제 이전 후보가 없어도 완결된 숫자·행위로 철회할 수 있어요.
  if(proof.valueKind==="operation"){
    if(!options||!proof.valueText)return false;
    const values=proof.valueText.split(/\s*(?:을|를|에서|말고|대신)\s*/);
    return values.length<=2&&values.every(value=>withdrawalScalar(field,value.trim(),request,options)!==undefined);
  }
  if(!proof.selectedMode&&proof.valueKind!=="candidate")return true;
  const p=request.previous_candidate,old=p?.fields.find(item=>item.field===field&&item.action==="set");
  if(!old||!old.evidence.trim()||!p?.sources?.[field]||!(p.origins?.[field]??p.request_id)||
      !Object.prototype.hasOwnProperty.call(p.baselines??{},field)||
      !sameValue(p.baselines?.[field]??null,request.current[field]??null))return false;
  if(proof.selectedMode)return old.value===proof.selectedMode;
  if(!proof.valueText)return true;
  const value=proof.valueText.normalize("NFKC").trim();
  if(!options)return false;
  if(field==="amount"||field==="crew"){
    const parsed=withdrawalScalar(field,value,request,options);
    return parsed!==undefined&&old.value===parsed;
  }
  if(request.form==="quote"&&field==="days"){
    const q=classifyDurationQuantity(value);
    return !q.risk&&q.tokens.length===1&&q.tokens[0].start===0&&q.tokens[0].end===value.length&&q.tokens[0].value===old.value;
  }
  if(field==="title"||field==="reason"){
    const unquoted=value.replace(/^(?:"([^"]*)"|'([^']*)'|“([^”]*)”|‘([^’]*)’)$/,"$1$2$3$4");
    return !/\uE000/.test(value)&&typeof old.value==="string"&&unquoted.normalize("NFKC").trim()===old.value.normalize("NFKC").trim();
  }
  if(!/^[\d\s년월일,./과와및-]+$/.test(value))return false;
  if(request.form==="quote"&&field==="valid"){
    if(!/^(?:\d{4}\s*(?:년|[-/.])\s*)?\d{1,2}\s*(?:월|[-/.])\s*\d{1,2}\s*일?$/.test(value))return false;
    const q=readQuoteValidity("유효기간은 "+value,options.today);
    return !q.ambiguous&&q.value===old.value;
  }
  if(request.form==="period"&&(field==="start"||field==="end")){
    if(!/^(?:\d{4}\s*(?:년|[-/.])\s*)?\d{1,2}\s*(?:월|[-/.])\s*\d{1,2}\s*일?$/.test(value))return false;
    const q=readExplicitPeriodBoundary((field==="start"?"시작일은 ":"종료일은 ")+value+"로 지정합니다",field,options.today,options.baseMonth);
    return !!q&&!q.ambiguous&&q.value===old.value;
  }
  if(field==="days"||field==="excluded"){
    const q=field==="days"?parseDates(value,options.today,options.baseMonth,options.holidays,options.noWeekend)?.days:
      readExplicitPeriodExclusions("제외일은 "+value+"로 지정합니다",options.today,options.baseMonth)?.dates;
    return !!q&&Array.isArray(old.value)&&sameValue([...q].sort(),[...old.value].sort());
  }
  return false;
}

function dateMatchesProof(day: string, evidence: string): boolean {
  const [year, month, number] = day.split("-").map(Number);
  const tokens = evidence.normalize("NFKC").matchAll(/(?<!\d)(?:(\d{4})\s*(?:년|[-/.])\s*)?(\d{1,2})\s*(?:월|[-/.])\s*(\d{1,2})\s*일?(?!\d)|(?<![\d/.-])(\d{1,2})\s*일(?!\s*(?:간|동안|째))/g);
  return [...tokens].some(token => token[4] ? Number(token[4]) === number :
    (!token[1] || Number(token[1]) === year) && Number(token[2]) === month && Number(token[3]) === number);
}

// The local date parser can provisionally assign a single literal to both
// endpoints. Only retract the unrelated reservation if the already validated
// sibling owns every such literal and the original has no contrary endpoint.
const BOUNDARY_ROLE = {
  start: /시작|착공|착수|개시|첫\s*(?:날|일|날짜)/,
  end: /종료|마감|완료|완공|준공|끝(?:나|내|날)|마치/,
};
function siblingOwnsBoundary(field: string, response: AssistResponse, request: AssistRequest,
    intent: RequestIntent, opts: RuleOptions, raw: unknown): boolean {
  if (request.form !== "period" || (field !== "start" && field !== "end") ||
      intent.blocked.has(field) || intent.priorReferences.has(field) || intent.currentKeep.has(field)) return false;
  const sibling = field === "start" ? "end" : "start";
  const candidate = response.fields.find(f => f.field === sibling);
  if (!candidate || candidate.action !== "set" || typeof candidate.value !== "string" ||
      !canonicalFresh(candidate,request,raw,"explicit_change","utterance") ||
      !proofText(request.text).includes(proofText(candidate.evidence)) ||
      !BOUNDARY_ROLE[sibling].test(candidate.evidence) || BOUNDARY_ROLE[field].test(request.text)) return false;
  const literal = calendarEvidenceDates(candidate.evidence,opts.today,opts.baseMonth);
  if (literal?.length !== 1 || literal[0] !== candidate.value) return false;
  const scopes = resolvedIntentSegments(request.form,request.text).filter(s => s.fields.includes(field));
  return scopes.length === 1 && scopes.every(s => !s.dependent &&
    BOUNDARY_ROLE[sibling].test(s.part) && !/부터|까지|[~∼〜～]|미정|보류|아직|취소|철회/.test(s.part) &&
    clauseProofText(s.part).includes(clauseProofText(candidate.evidence)) &&
    sameValue(calendarEvidenceDates(s.part,opts.today,opts.baseMonth) ?? [],literal));
}

// No-change is distinct from negating a withdrawal or negating retention.
// It is a field-local relation, not a positive candidate interpretation.
const NO_CHANGE = /(?:변경|수정|정정|조정|교체)(?:은|는|을|를)?\s*(?:하)?지\s*말(?:고|아\s*(?:주세요|줘요|주십시오)|아요|아\s*주세요|겠습니다)|(?:변경|수정|정정|조정|교체)(?:은|는|을|를)?\s*하지\s*마세요/;
const CURRENT_ANCHOR = /현재|지금|기존|원래|저장된|수정\s*전/;
const KEEP_END = /(?:유지(?:해\s*주세요|하세요|합니다|하겠습니다|할게요)|그대로\s*(?:두세요|둡니다|두겠습니다|둘게요|놔두세요|남겨\s*주세요))$/;
const KEEP_OPPOSITION = /(?:유지|보존|두|남기|취소|철회)하지\s*(?:마|말|않|못)|(?:두|남기)지\s*(?:마|말|않|못)|미정|보류|아마|혹시|조건부|가능하면|만약|검토\s*중|결정하지\s*못|계약|납부|주문|예약/;

// A previously proposed scalar is withdrawn by value and an independent,
// completed instruction restores the actual field. Resolve the unlabelled
// withdrawal only if it uniquely refers to that verified pending candidate.
function linkedScalarWithdrawal(candidate:CandidateField,request:AssistRequest,opts:RuleOptions|undefined,raw:unknown):boolean {
  const field=candidate.field;
  if(!opts||!(field==="days"&&request.form==="quote"||field==="crew"&&request.form==="dates"||field==="amount"&&(request.form==="quote"||request.form==="extra"))||
    !canonicalFresh(candidate,request,raw,"retain_current","current")||candidate.action!=="keep"||candidate.value!==null)return false;
  const prior=request.previous_candidate, old=prior?.fields.find(f=>f.field===field&&f.action==="set");
  if(!prior||!old||typeof old.value!=="number"||!prior.sources?.[field]||!(prior.origins?.[field]??prior.request_id)||
    !Object.prototype.hasOwnProperty.call(prior.baselines??{},field)||!sameValue(prior.baselines?.[field]??null,request.current[field]??null)||
    !proofText(old.evidence)||!proofText(prior.text).includes(proofText(old.evidence)))return false;
  const scopes=resolvedIntentSegments(request.form,request.text),own=scopes.filter(s=>s.fields.includes(field));
  const evidence=clauseProofText(candidate.evidence);
  if(own.length!==1||own[0].fields.length!==1||own[0].dependent||clauseProofText(own[0].part)!==evidence||
    !hasIndependentFieldSignal(request.form,own[0].part,field)||KEEP_OPPOSITION.test(own[0].part)||
    !/(?:현재|지금|기존|원래|저장된|수정\s*전)(?:\s*값|\s*상태)?\s*(?:그대로|대로)\s*(?:두세요|둡니다|둘게요|두겠습니다|유지(?:해\s*주세요|합니다|하겠습니다))$/.test(own[0].part))return false;
  const others=scopes.filter(s=>s!==own[0]);
  if(others.length!==1||others[0].sentence>=own[0].sentence||others[0].dependent||others[0].fields.some(f=>f!==field))return false;
  const cancel=others[0].part.match(/^(?:아까|앞서|방금|직전|이전|지난번)\s*(?:말씀드린|말한|제안한|정한|잡은|입력한)\s*(.+?)(?:은|는|을|를)?\s*(?:취소|철회)(?:할게요|하겠습니다|합니다|해\s*주세요)$/);
  if(!cancel)return false;
  const value=cancel[1].trim();
  let operand:number|undefined;
  if(field==="days"){
    const q=classifyDurationQuantity(value);if(!q.risk&&q.tokens.length===1&&q.tokens[0].start===0&&q.tokens[0].end===value.length)operand=q.tokens[0].value;
  }else operand=withdrawalScalar(field,value,request,opts);
  return operand!==undefined&&operand===old.value;
}

function earlierReadOnly(part: string): boolean {
  return /[?？]$/.test(part) && !/주세요|주십시오|주시겠|부탁/.test(part) ||
    /확인만\s*(?:하고\s*싶|하겠습니다|할게|해\s*주세요)/.test(part);
}

// Additional independent retention proof is confined to the hold lifecycle.
// A complete current identity may resolve a prior obligation; a mere "do not
// change" can dismiss only a newly provisional hold when there is no prior.
// Candidate validation still uses the unchanged checkedIntentFields path.
function scopedRetentionProof(candidate: CandidateField, request: AssistRequest, intent: RequestIntent,
    opts: RuleOptions | undefined, raw: unknown, prior: boolean): "current" | "provisional" | undefined {
  const field = candidate.field, evidence = clauseProofText(candidate.evidence);
  if (!canonicalFresh(candidate,request,raw,"retain_current","current") ||
      candidate.action !== "keep" || candidate.value !== null || !evidence ||
      !proofText(request.text).includes(proofText(candidate.evidence)) || intent.unboundWithdrawal ||
      intent.carryConflicts.has(field) || intent.priorReferences.has(field) ||
      intent.protectedDates.has(field)) return;
  const text = request.form === "extra" ? extraTextScopes(request.text).intentText : request.text;
  const scopes = resolvedIntentSegments(request.form,text);
  const actual = scopes.find(s => s.fields.length === 1 && s.fields[0] === field &&
    clauseProofText(s.part).includes(evidence)&&!reportedRetention(s.part));
  if (!actual || !hasIndependentFieldSignal(request.form,candidate.evidence,field) ||
      /[?？]|후보|변경안/.test(actual.part) || KEEP_OPPOSITION.test(actual.part) ||
      scopes.some(s => s !== actual && s.fields.includes(field) &&
        !(s.sentence < actual.sentence && (reportedRetention(s.part)||!prior && earlierReadOnly(s.part))))) return;

  const current = request.current[field] ?? null;
  const part = actual.part.trim();
  if (field === "vat") {
    const modes = vatSemanticTokens(part);
    if (modes.some(token => token.mode !== current)) return;
  } else if (/\d|(?:[일이삼사오육칠팔구영십백천만억]+)\s*원/.test(part) ||
      readCrew(part).mentioned || (request.form === "quote" && field === "days" && classifyDurationQuantity(part).mentioned)) {
    // The date-list case has an existing date and a separate causal statement.
    // Confirm every named date is already in the actual list before recognizing
    // the current identity, never treat a hypothetical condition as a release.
    if (request.form !== "period" || field !== "excluded" || !Array.isArray(current) || !opts) return;
    const marker = part.search(CURRENT_ANCHOR);
    if (marker <= 0) return;
    const context = part.slice(0,marker), identity = part.slice(marker);
    if (!/(?:안\s*되(?:는|어서)|불가(?:인|라서)|할\s*수\s*없(?:는|어서))[^,.!?]*(?:날이라서|때문에|어서|라서)\s*$/.test(context)) return;
    const dates = calendarEvidenceDates(context,opts.today,opts.baseMonth);
    if (!dates?.length || dates.some(day => !current.includes(day)) ||
        !ownsCurrentRetentionEvidence(request.form,identity,field,identity) ||
        !evidence.includes(clauseProofText(identity))) return;
    return "current";
  }
  if (actual.dependent) return;
  if (ownsCurrentRetentionEvidence(request.form,part,field,candidate.evidence.replace(/[.!。]+$/,"")))
    return "current";

  // Split a linked withdrawal at its action boundary. Reuse the existing
  // withdrawal object/value verifier for the prefix and current-value
  // ownership verifier for the suffix; the whole original span remains proof.
  const link = /(?:취소|철회)하고\s*/.exec(part);
  if (link) {
    const prefix = part.slice(0,link.index) + link[0].trim().replace(/하고$/,"합니다");
    const identity = part.slice(link.index+link[0].length);
    const withdrawal = requestIntent(request.form,prefix).withdrawalProofs.get(field);
    if (withdrawal && withdrawalValueMatches(withdrawal,field,request,opts) &&
        ownsCurrentRetentionEvidence(request.form,identity,field,identity) &&
        evidence.includes(clauseProofText(part.slice(0,link.index+link[0].length))) &&
        evidence.includes(clauseProofText(identity))) return "current";
    return;
  }
  const noChange = NO_CHANGE.exec(part);
  if (!noChange || !evidence.includes(clauseProofText(noChange[0]))) return;
  const tail = part.slice(noChange.index+noChange[0].length).trim();
  if (tail && !KEEP_END.test(tail) || /취소|철회|아니|말고.*말고/.test(tail) ||
      /취소|철회/.test(part.slice(0,noChange.index))) return;
  // An explicit current marker after the no-change relation distinguishes
  // keeping the actual form value from retaining a pending proposal.
  if (tail && CURRENT_ANCHOR.test(tail) && KEEP_END.test(tail) &&
      (ownsCurrentRetentionEvidence(request.form,tail,field,tail) ||
       field === "vat" && vatSemanticTokens(tail).length === 1 &&
         vatSemanticTokens(tail)[0].mode === current)) return "current";
  // A bare prohibition can resolve only a provisional reservation. A linked
  // tail may not introduce another value or object under the word "keep".
  const bareIdentity = /^(?:그대로\s*)?(?:유지(?:해\s*주세요|하세요|합니다|하겠습니다|할게요)|두세요|둡니다|두겠습니다|둘게요|놔두세요)$/;
  if (!prior && (!tail || bareIdentity.test(tail))) return "provisional";
}

// A complete request to show a proposed boundary is not an execution
// condition. Only one owned dated recommendation and a separate explicit
// preview confirmation qualify; reject genuine conditions and other values.
function verifiedBoundaryRecommendation(field:CandidateField,request:AssistRequest,opts:RuleOptions,raw:unknown):boolean {
  if(request.form!=="period"||(field.field!=="start"&&field.field!=="end")||field.action!=="set"||typeof field.value!=="string"||
    !canonicalFresh(field,request,raw,"explicit_change","utterance"))return false;
  const preview=periodBoundaryPreview(request.text);
  if(!preview||preview.field!==field.field)return false;
  const needle=field.evidence.replace(/[.!。]+$/,"");
  if(!needle||!proofText(preview.part).includes(proofText(needle))||
    !BOUNDARY_ROLE[field.field].test(needle)||!/(?:변경|수정|정정|설정|지정|조정|이동|연장|단축)/.test(needle))return false;
  if(operationScopeRisk("period",needle,field.field))return false;
  const full=calendarEvidenceDates(preview.body,opts.today,opts.baseMonth),proof=calendarEvidenceDates(needle,opts.today,opts.baseMonth);
  return full?.length===1&&proof?.length===1&&full[0]===field.value&&proof[0]===field.value;
}

// 보호 절을 무조건 긍정으로 바꾸지 않아요. 이번 서버의 완전한 교체 근거와
// 현재/이전 목록, 실제 보호 원소, 새 원소를 확인한 경우에만 적용 후보로 받습니다.
function verifiedProtectedDates(field: CandidateField, intent: RequestIntent, request: AssistRequest): boolean {
  const protections = intent.protectedDates.get(field.field);
  const relation = field.correction;
  if (!protections?.length || field.status !== "valid" || field.action !== "set" ||
    field.origin_request_id !== request.request_id || field.intent_kind !== "explicit_change" ||
    field.value_source !== "derived" || !Array.isArray(field.value) ||
    relation?.kind !== "replace" || !Array.isArray(relation.rejected_value)) return false;
  const before = relation.rejected_value, after = field.value;
  if (before.length !== after.length) return false;
  const current = request.current[field.field];
  const prior = request.previous_candidate;
  const old = prior?.fields.find(item => item.field === field.field && item.action === "set");
  const currentMatches = sameValue(before, current);
  const priorMatches = !!old && sameValue(before, old.value) &&
    sameValue(prior?.baselines?.[field.field] ?? null, current);
  if (!currentMatches && !priorMatches ||
    protections.some(p => p.reference === "current" && !currentMatches ||
      p.reference === "previous" && !priorMatches)) return false;
  const removed = before.filter(day => !after.includes(day)), added = after.filter(day => !before.includes(day));
  const utterance = proofText(request.text);
  if (removed.length !== 1 || added.length !== 1 ||
    ![field.evidence, relation.rejected_evidence, relation.accepted_evidence].every(
      value => !!proofText(value) && utterance.includes(proofText(value))) ||
    !dateMatchesProof(removed[0], relation.rejected_evidence) || !dateMatchesProof(added[0], relation.accepted_evidence)) return false;
  return protections.every(protection => {
    const matching = before.filter(day => dateMatchesProof(day, protection.date));
    return matching.length === 1 && after.includes(matching[0]);
  });
}

/** 직접 명시된 날짜 교체는 파생 메타데이터의 존재만으로 믿지 않아요.
 * 실제 선택된 목록과 한 원소 교체 방향/근거를 독립적으로 검증해요.
 * 이전/현재 참조가 있는 경우 다른 목록을 대신 사용하지 않아요. */
function verifiedDirectDateMove(field: CandidateField, request: AssistRequest): boolean | undefined {
  if(!(request.form==="dates"&&field.field==="days"||request.form==="period"&&field.field==="excluded"))return;
  const scopes=scopedFieldParts(request.form,request.text,field.field);
  const moves=scopes.flatMap(clause=>{const move=directDateMove(clause);return move?[{clause,move}]:[];});
  if(!moves.length)return;
  if(moves.length!==1 || field.status!=="valid" || field.action!=="set" || field.origin_request_id!==request.request_id ||
      field.value_source!=="derived" || !Array.isArray(field.value) || field.correction?.kind!=="replace")return false;
  const {clause,move}=moves[0], correction=field.correction;
  const previousRef=new RegExp(RECENT_REFERENCE_MARK).test(move.prefix);
  const currentRef=/(?:현재|지금|직접\s*(?:입력|수정|변경))/.test(move.prefix);
  if(previousRef&&currentRef)return false;
  const current=request.current[field.field];
  const prior=request.previous_candidate;
  const old=prior?.fields.find(item=>item.field===field.field&&item.action==="set");
  const priorBaseline=prior?.baselines?.[field.field];
  const priorMatches=!!old&&Array.isArray(old.value)&&!!prior?.sources?.[field.field]&&
    !!(prior?.origins?.[field.field]??prior?.request_id)&&
    Object.prototype.hasOwnProperty.call(prior?.baselines??{},field.field)&&priorBaseline!==undefined&&sameValue(priorBaseline,current);
  const bases:string[][]=[];
  if(!previousRef&&Array.isArray(current))bases.push(current);
  if(!currentRef&&priorMatches&&Array.isArray(old?.value)&&!bases.some(base=>sameValue(base,old.value)))bases.push(old.value);
  if(previousRef&&!priorMatches)return false;
  const proof=proofText(field.evidence), from=proofText(correction.rejected_evidence),to=proofText(correction.accepted_evidence);
  const normalizedClause=proofText(clause);
  if(!proof||!from||!to||!proofText(request.text).includes(proof)||
      !proof.includes(proofText(move.old))||!proof.includes(proofText(move.next))||
      !normalizedClause.includes(from)||!normalizedClause.includes(to))return false;
  const after=field.value;
  const matches=bases.filter(before=>{
    if(before.length!==after.length)return false;
    const removed=before.filter(day=>!after.includes(day)),added=after.filter(day=>!before.includes(day));
    if(removed.length!==1||added.length!==1)return false;
    if(before.filter(day=>dateMatchesProof(day,move.old)).length!==1||
      !dateMatchesProof(removed[0],move.old)||!dateMatchesProof(added[0],move.next)||
      !dateMatchesProof(removed[0],correction.rejected_evidence)||!dateMatchesProof(added[0],correction.accepted_evidence))return false;
    // 월/연도가 생략된 새 날짜는 검증된 옛 원소의 달을 사용해요.
    if(!/[월/.-]/.test(move.next)&&removed[0].slice(0,7)!==added[0].slice(0,7))return false;
    if(/(?:미루|미뤄|미룰|밀어|밀기)/.test(clause)&&added[0]<=removed[0]||/(?:당기|당겨|당길)/.test(clause)&&added[0]>=removed[0])return false;
    const ordinal=/(첫(?:\s*번째)?|두\s*번째|둘째|세\s*번째|셋째|네\s*번째|넷째|\d+\s*번째)\s*(?:방문|작업|공사|시공|날짜|날|일정)(?:\s*(?:일|날짜))?\s*(?:[은는이가을를]\s*)?$/.exec(move.prefix);
    if(ordinal){
      const token=ordinal[1].replace(/\s/g,"");
      const index=/^\d/.test(token)?Number(token.replace(/번째$/,"")):({첫:1,두:2,둘:2,세:3,셋:3,네:4,넷:4} as Record<string,number>)[token[0]];
      if(before[index-1]!==removed[0])return false;
    }
    return sameValue(correction.rejected_value,before)||sameValue(correction.rejected_value,removed)||sameValue(correction.rejected_value,removed[0]);
  });
  return matches.length===1;
}

function verifiedCarry(field: CandidateField, request: AssistRequest, rawResponse: unknown): boolean {
  const prior = request.previous_candidate;
  const previous = prior?.fields.find(item => item.field === field.field);
  if (!prior || !previous || !field.origin_request_id || field.origin_request_id === request.request_id ||
      field.origin_request_id !== (prior.origins?.[field.field] ?? prior.request_id) ||
      field.source !== prior.sources?.[field.field] || field.action !== previous.action ||
      !sameValue(field.value, previous.value) || proofText(field.evidence) !== proofText(previous.evidence) ||
      !proofText(previous.evidence) ||
      !Object.prototype.hasOwnProperty.call(prior.baselines ?? {}, field.field) ||
      !sameValue(prior.baselines?.[field.field] ?? null, request.current[field.field] ?? null)) return false;
  const before = previous.correction, after = field.correction;
  // 날짜 목록의 계승은 정정 관계도 근거로 보존해요. 정규화가 버린
  // 잘못된 목록 관계를 정상 계승으로 바꾸지 않아요. 스칼라 관계는
  // 기존 계약대로 이력에 전달하지 않고 버려요.
  if (request.form === "dates" && field.field === "days" || request.form === "period" && field.field === "excluded") {
    const rawFields = (rawResponse as {fields?: unknown})?.fields;
    const rawField = Array.isArray(rawFields)
      ? rawFields.find(item => item && typeof item === "object" && item.field === field.field)
      : undefined;
    if (!rawField || (rawField.correction != null) !== !!before) return false;
  }
  return (!before && !after || !!before && !!after && before.kind === after.kind &&
    sameValue(before.rejected_value, after.rejected_value) &&
    proofText(before.rejected_evidence) === proofText(after.rejected_evidence) &&
    proofText(before.accepted_evidence) === proofText(after.accepted_evidence)) &&
    (previous.value_source !== "derived" || field.value_source === "derived");
}

function safeTextValue(value: AssistValue, text: string, field: string): boolean {
  if (typeof value !== "string" || !hasExtraAssignmentSuffix(value) && !isExtraReferenceValue(value) && !hasExtraTextLabelPrefix(value) && !isExtraClearCommandValue(value) && !(field === "title" && hasExtraOtherFieldLabel(value))) return true;
  return [["\"", "\""], ["'", "'"], ["“", "”"], ["‘", "’"]].some(([left, right]) =>
    proofText(text).includes(proofText(left + value + right)));
}

const normalizedExtraText = (text: string) => text.normalize("NFKC").trim().replace(/\s+/g, " ");
function groundedExtraValue(value: AssistValue, evidence: string, text: string, field: string,
    grounding: ExtraTextGrounding | undefined, conflict: boolean): boolean {
  if (!safeTextValue(value, text, field)) return false;
  if ((field==="title"||field==="reason")&&foreignExtraTextScope(text,field,evidence,value))return false;
  if (typeof value === "string" && (field === "title" || field === "reason") && extraTextClearRequested(text, field)) return false;
  if (!grounding) return true;
  if (grounding.incomplete || typeof value !== "string") return false;
  // 일반 후보는 유일한 값을 증명해야 하고, 충돌 대안은 각자의 완전한
  // 값을 증명해야 해요. 충돌 자체를 하나의 후보로 승격하지 않아요.
  if (!conflict && new Set(grounding.values.map(item => normalizedExtraText(item.value))).size !== 1) return false;
  const proof = proofText(evidence), clauseProof = clauseProofText(evidence);
  return !!clauseProof && proofText(text).includes(proof) && grounding.values.some(item =>
    normalizedExtraText(item.value) === normalizedExtraText(value) && proof.includes(proofText(item.value)) &&
    (proofText(item.clause).includes(clauseProof) || proof.includes(proofText(item.clause))));
}

function checkedIntentFields(response: AssistResponse, intent: RequestIntent, localOnly: boolean, request?: AssistRequest, options?: RuleOptions, firstOrigins: Readonly<Record<string, string>> = {}, rawResponse?: unknown): AssistResponse {
  const duration = request?.form === "quote" ? readQuoteDuration(request.text) : undefined;
  const validity = request?.form === "quote" && options ? readQuoteValidity(request.text, options.today) : undefined;
  const listQuantity = request && options && (request.form === "dates" || request.form === "period")
    ? readDateListQuantity(request.form, request.text, options.today, options.baseMonth, options.holidays, options.noWeekend) ??
      (request.form==="period"&&!intent.requested.has("start")&&!intent.requested.has("end")?
        readExplicitPeriodExclusions(request.text,options.today,options.baseMonth):undefined) : undefined;
  const extraScope = request?.form === "extra" ? extraTextScopes(request.text) : undefined;
  const scopedQuote = !!extraScope && extraScope.intentText !== request?.text;
  const scalarValues = scopedQuote && options && extraScope ? parseQuote(extraScope.scalarText,options.today) : undefined;
  const vatValue=request&&(request.form==="quote"||request.form==="extra")?
    readVatValue(extraScope?.scalarText??request.text):undefined;
  const boundaryProofs=request?.form==="period"&&options?{
    start:readExplicitPeriodBoundary(request.text,"start",options.today,options.baseMonth),
    end:readExplicitPeriodBoundary(request.text,"end",options.today,options.baseMonth),
  }:undefined;
  return { ...response, fields: response.fields.map((initialField) => {
    let field = initialField;
    const carried = request && (field.intent_kind === "retain_previous" || field.value_source === "previous" ||
      !!field.origin_request_id && field.origin_request_id !== request.request_id);
    // 인용 문자열 안에서만 생긴 다른 필드의 후보는 적용도 새 보류도
    // 만들지 않아요. 기존 보류 및 실제 이전값 계승은 그대로 유지해요.
    if (scopedQuote && !carried && !intent.requested.has(field.field) &&
      !request?.rule_fields.some(item=>item.field===field.field && item.action==="set") &&
      (actionable(field) || field.status !== "valid" && !!field.evidence.trim())) {
      return { ...field, status: "uncertain", action: "uncertain", value: null, evidence: "", alternatives: [] };
    }
    if (!actionable(field)) return field;
    const rawField = wireField(rawResponse,field.field);
    // A legacy scalar mock may have a nonliteral label in its evidence.
    // Only an independently equivalent local rule is eligible in that case;
    // use its actual evidence and provenance, never the fabricated span.
    if (request && !carried && field.status === "valid" && field.action === "set" &&
        field.field === "amount" && !!proofText(field.evidence) &&
        !proofText(request.text).includes(proofText(field.evidence))) {
      const own = scopedFieldParts(request.form,request.text,field.field);
      const equivalent = request.rule_fields.find(rule => rule.field === field.field &&
        rule.action === field.action && sameValue(rule.value,field.value) &&
        !!proofText(rule.evidence) && proofText(request.text).includes(proofText(rule.evidence)));
      const alias = proofText(field.evidence).replace(/^견적(?=금액)/,"");
      if (equivalent && alias !== proofText(field.evidence) &&
          own.some(part => proofText(part).includes(alias))) {
        field = {...field,source:"rule",evidence:equivalent.evidence};
      }
    }
    // Legacy responses without intent metadata keep their pre-R12 conservative
    // checks. They cannot enable any new canonical state transition.
    const wireFields = (rawResponse as {fields?:unknown})?.fields;
    const canonicalWire = Array.isArray(wireFields) &&
      wireFields.some(item => item?.intent_kind != null ||
        item?.origin_request_id != null && item?.value_source != null);
    const invalidEvidence = !!request && !!canonicalWire && field.status === "valid" && !carried &&
      (!proofText(field.evidence) || !proofText(request.text).includes(proofText(field.evidence)) ||
       !!rawField && rawField.source !== "rule" && rawField.source !== "llm");
    const literalList = !!request && !!options && verifiedLiteralList(field,request,options,intent,rawResponse);
    const replacement = intent.replacements.has(field.field);
    const vatConfirmation = field.field === "vat" ? intent.vatConfirmation : undefined;
    const vatEvidence = vatConfirmation ? proofText(field.evidence) : "";
    const vatClauseEvidence = clauseProofText(vatEvidence);
    const invalidVatProof = vatConfirmation && (!request || field.status !== "valid" ||
      field.action !== "set" || field.value !== vatConfirmation.value ||
      field.origin_request_id !== request.request_id || !vatClauseEvidence ||
      !proofText(request.text).includes(vatEvidence) ||
      !proofText(vatConfirmation.evidence).includes(vatClauseEvidence));
    // 이전 응답 형식에는 의도 메타데이터가 없어요. 이 경우에도
    // 서버 근거를 신뢰하는 대신 동일한 값의 독립 규칙 근거를 확인해요.
    const rawFields = (rawResponse as {fields?:unknown})?.fields;
    const rawVat = Array.isArray(rawFields) ? rawFields.find(item=>item?.field==="vat") : undefined;
    const legacyVatRule = field.field==="vat"&&!!request&&!!rawVat&&rawVat.intent_kind==null&&rawVat.value_source==null&&
      request.rule_fields.some(rule=>rule.field==="vat"&&rule.action==="set"&&rule.value===field.value&&
        vatEvidenceMatches(field.value,rule.evidence,request.text,vatValue?.scopes??[]));
    const literalVat = !!request && field.field === "vat" && field.action === "set" &&
      canonicalFresh(field,request,rawResponse,"explicit_change","utterance") &&
      !intent.blocked.has("vat") && !intent.currentKeep.has("vat") && !intent.priorReferences.has("vat") &&
      (vatValue?.value === undefined || vatValue.value === field.value) &&
      ownsLiteralVatEvidence(request.form,extraScope?.scalarText??request.text,field.evidence,field.value);
    const invalidVatValue=!!request&&field.field==="vat"&&!!vatValue&&!vatConfirmation&&!carried&&!literalVat&&
      (field.status==="valid"&&field.action==="set"&&
        (field.value!==vatValue.value||!legacyVatRule&&!vatEvidenceMatches(field.value,field.evidence,request.text,vatValue.scopes))||
        field.status==="conflict"&&field.alternatives.some(alt=>alt.action==="set"&&
          (alt.value!==vatValue.value||!vatEvidenceMatches(alt.value,alt.evidence,request.text,vatValue.scopes))));
    const invalidDateProof = intent.protectedDates.has(field.field) &&
      (!request || !verifiedProtectedDates(field, intent, request));
    const invalidDirectDateProof = request && replacement && !carried &&
      verifiedDirectDateMove(field,request)===false;
    const boundary=field.field==="start"||field.field==="end"?boundaryProofs?.[field.field]:undefined;
    const invalidBoundary=!!request&&!!boundaryProofs&&!carried&&(field.field==="start"||field.field==="end")&&(
      // 명시된 경계가 하나라면 같은 날짜를 다른 경계로 복제하지 않아요.
      !intent.requested.has(field.field)&&Object.values(boundaryProofs).some(Boolean)||
      !!boundary&&(boundary.ambiguous||field.action!=="set"||field.status!=="valid"||
        field.value!==boundary.value||typeof field.value!=="string"||
        !clauseProofText(field.evidence)||!proofText(request.text).includes(proofText(field.evidence))||
        !boundary.evidence.some(part=>proofText(part).includes(clauseProofText(field.evidence))||
          proofText(field.evidence).includes(proofText(part)))||
        !dateMatchesProof(field.value,field.evidence)));
    const priorReference = intent.priorReferences.get(field.field);
    // 별도 완결 대입절의 정확한 수량이 검증된 이전 값과 같을 때만
    // 새 대입으로 재확인해요. 계승·충돌·잘린 근거·stale은 면제하지 않아요.
    const priorDuration = request?.previous_candidate?.fields.find(old => old.field === "days");
    const durationReaffirmation = request?.form === "quote" && field.field === "days" &&
      !!intent.durationReaffirmation && !carried && field.status === "valid" && field.action === "set" &&
      field.origin_request_id === request.request_id && duration?.value === field.value &&
      !duration?.ambiguous && duration?.previousOld === undefined && priorDuration?.action === "set" &&
      sameValue(priorDuration.value,field.value) && !!priorDuration.evidence.trim() &&
      !!request.previous_candidate?.sources?.days &&
      !!(request.previous_candidate?.origins?.days ?? request.previous_candidate?.request_id) &&
      Object.prototype.hasOwnProperty.call(request.previous_candidate?.baselines ?? {},"days") &&
      sameValue(request.previous_candidate?.baselines?.days ?? null,request.current.days ?? null) &&
      !!clauseProofText(field.evidence) && proofText(request.text).includes(proofText(field.evidence)) &&
      proofText(intent.durationReaffirmation).includes(clauseProofText(field.evidence)) &&
      (() => {
        const proof = classifyDurationQuantity(field.evidence);
        return !proof.risk && proof.tokens.length === 1 && proof.tokens[0].value === field.value;
      })();
    const invalidCarry = (carried || priorReference && !durationReaffirmation) && (!request || field.status !== "valid" ||
      !verifiedCarry(field, request, rawResponse) || intent.carryConflicts.has(field.field) ||
      priorReference === "first" && firstOrigins[field.field] !== field.origin_request_id);
    const unboundPrior = intent.unboundWithdrawal && (localOnly || carried || priorReference ||
      request?.previous_candidate?.fields.some(old => old.field === field.field));
    // 이번 글의 실제 수량과 검증된 이전값 참조는 별도의 근거예요.
    // 참조뿐인 글에서 새 일수를 만들거나 기존 수량의 위험을 무시하지 않아요.
    const durationMismatch = field.field === "days" && duration &&
      (duration.ambiguous || duration.value !== undefined &&
        (field.action === "clear" || field.action === "set" && field.value !== duration.value ||
          field.alternatives.some(alt => alt.action !== "set" || alt.value !== duration.value)) ||
        !carried && duration.value === undefined && intent.requested.has("days") &&
          (field.action === "set" || field.alternatives.some(alt => alt.action === "set")));
    const durationOld = field.field === "days" ? duration?.previousOld : undefined;
    const invalidDurationHistory = durationOld !== undefined && (!request ||
      field.action !== "set" || field.origin_request_id !== request.request_id ||
      !field.evidence.trim() || !proofText(request.text).includes(proofText(field.evidence)) ||
      !request.previous_candidate?.fields.some(old => old.field === "days" && old.action === "set" && old.value === durationOld) ||
      !sameValue(request.previous_candidate.baselines?.days ?? null, request.current.days ?? null));
    const validityMismatch = field.field === "valid" && validity &&
      (validity.ambiguous || validity.value !== undefined &&
        (field.action === "clear" || field.action === "set" && field.value !== validity.value ||
          field.alternatives.some(alt => alt.action !== "set" || alt.value !== validity.value)));
    const listMismatch = !literalList && listQuantity && field.field === (request?.form === "dates" ? "days" : "excluded") &&
      (listQuantity.invalid || field.action === "clear" || field.action === "set" && !sameValue(field.value,listQuantity.dates) ||
        field.alternatives.some(alt => alt.action !== "set" || !sameValue(alt.value,listQuantity.dates)));
    const textField = field.field === "title" || field.field === "reason";
    const textGrounding = request && textField && !carried
      ? extraTextGrounding(request.text, field.field as "title" | "reason") : undefined;
    const scopedScalar = !carried && scalarValues && (field.field === "amount" || field.field === "vat")
      ? scalarValues[field.field] : undefined;
    const invalidScopedScalar = scopedScalar !== undefined &&
      (field.status === "valid" && (field.action !== "set" || field.value !== scopedScalar) ||
       field.status === "conflict" && field.alternatives.some(alt=>alt.action !== "set" || alt.value !== scopedScalar));
    const alternatives = request && field.status === "conflict" ? field.alternatives.filter(alt =>
      proofText(request.text).includes(proofText(alt.evidence)) &&
      (!textField || groundedExtraValue(alt.value, alt.evidence, request.text, field.field, textGrounding, true))) : field.alternatives;
    // 이미 검증된 이전 후보의 인용 값은 원래 문맥에서 확인해요.
    // 새 대입으로 가장한 출처는 위 verifiedCarry가 계속 차단해요.
    const invalidText = request && textField && field.status !== "conflict" &&
      !groundedExtraValue(field.value, field.evidence,
        carried ? request.previous_candidate?.text ?? request.text : request.text, field.field, textGrounding, false);
    const unsafe = intent.cancelAll && !intent.reassigned.has(field.field) || intent.blocked.has(field.field) && !durationReaffirmation || intent.currentKeep.has(field.field) ||
      invalidEvidence || invalidVatProof || invalidVatValue || invalidDateProof || invalidDirectDateProof || invalidBoundary || invalidCarry || unboundPrior || invalidText || invalidScopedScalar || durationMismatch || invalidDurationHistory || validityMismatch || listMismatch ||
      field.status === "conflict" && alternatives.length < 2 ||
      localOnly && intent.serverOnly.has(field.field) ||
      replacement && !literalList && (localOnly || field.value_source !== "derived" || !field.correction);
    return unsafe ? { ...field, status: "uncertain", action: "uncertain", value: null, alternatives: [] } : { ...field, alternatives };
  }) };
}

/** Normal validated literal operations never traverse the whole-text rule
 * candidate vetoes. Legacy, corrections, carries and complex scope forms
 * are routed before proof evaluation, retaining their original constraints. */
function checkedServerFields(raw: unknown, request: AssistRequest, opts: RuleOptions, intent: RequestIntent,
    issues: InputIssues, confirmedAmount: number | undefined, verifiedCrew: number | undefined,
    crewReplacement: boolean, firstOrigins: Readonly<Record<string,string>>, intentText: string): AssistResponse {
  const response=readResponse(raw,request,opts);
  const analysis=analyzeOperations(request,intentText);
  const scopedQuote=request.form==="extra"&&intentText!==request.text;
  const routes=new Map<string,CandidateField>();
  const legacy: CandidateField[]=[];
  const periodScopes=request.form==="period"?resolvedIntentSegments("period",request.text):[];
  const exclusiveExclusions=request.form==="period"&&(!!referencedPeriodExclusion(request.text)||periodScopes.some(s=>!!explicitPeriodExclusionList(s.part)))&&
    !periodScopes.some(s=>s.fields.some(f=>f==="start"||f==="end")&&BOUNDARY_ROLE.start.test(s.part)||s.fields.includes("end")&&BOUNDARY_ROLE.end.test(s.part));
  for(const field of response.fields){
    const item=wireField(raw,field.field);
    const operation=wireOperation(raw,request,field);
    const declaredSimple=item?.intent_kind==="explicit_change"&&item.value_source==="utterance"&&item.correction==null&&
      item.action==="set"&&item.status==="valid";
    const carried=field.intent_kind==="retain_previous"||field.value_source==="previous"||
      !!field.origin_request_id&&field.origin_request_id!==request.request_id;
    if(!carried&&actionable(field)&&exclusiveExclusions&&(field.field==="start"||field.field==="end")){
      routes.set(field.field,{...field,action:"uncertain",status:"uncertain",value:null,evidence:"",alternatives:[]});
      continue;
    }
    const computed=verifiedComputedField(field,request,opts,raw);
    if(!carried&&actionable(field)&&computed!==undefined){
      routes.set(field.field,computed?field:{...field,action:"uncertain",status:"uncertain",value:null,alternatives:[]});
      continue;
    }
    if(!carried&&actionable(field)&&verifiedBoundaryRecommendation(field,request,opts,raw)){
      routes.set(field.field,field);
      continue;
    }
    if(!carried&&field.status==="valid"&&field.action==="set"&&
      (field.field==="title"||field.field==="reason")&&
      !positiveExtraTextValue(request.text,field.field,field.value,field.evidence,opts.today)){
      routes.set(field.field,{...field,action:"uncertain",status:"uncertain",value:null,evidence:"",alternatives:[]});
      continue;
    }
    if(actionable(field)&&verifiedCrewFinalRelation(field,request,intent,raw)===false){
      routes.set(field.field,{...field,action:"uncertain",status:"uncertain",value:null,alternatives:[]});
      continue;
    }
    // The same scope proof protects both producer paths and prevents an
    // unrelated background fragment from creating a new text-field hold.
    // Prior obligations are still preserved by settleHolds.
    if(!carried&&field.action!=="keep"&&field.status!=="conflict"&&
        (field.field==="title"||field.field==="reason")&&
        foreignExtraTextScope(request.text,field.field,field.evidence,field.value)){
      routes.set(field.field,{...field,action:"uncertain",status:"uncertain",value:null,evidence:"",alternatives:[]});
      continue;
    }
    const hiddenScalar=scopedQuote&&!carried&&!intent.requested.has(field.field)&&
      !request.rule_fields.some(rule=>rule.field===field.field&&rule.action==="set");
    // Passive keep/ambiguous fields don't need any positive parser at all.
    // Preserve the previous numeric status and quoted-value masking behavior.
    if(!actionable(field)&&!declaredSimple){
      const numeric=checkedNumericFields({...response,fields:[field]},issues,confirmedAmount,verifiedCrew).fields[0];
      routes.set(field.field,hiddenScalar&&numeric.status!=="valid"&&!!numeric.evidence.trim()
        ? {...numeric,action:"uncertain",status:"uncertain",value:null,evidence:"",alternatives:[]} : numeric);
      continue;
    }
    if(declaredSimple&&operation?.kind!=="explicit_change"){
      if(hiddenScalar){
        routes.set(field.field,{...field,action:"uncertain",status:"uncertain",value:null,evidence:"",alternatives:[]});
        continue;
      }
      // Historical test/response compatibility: a local amount proof may
      // replace the old synthetic label, but only through its old exact
      // equivalent-rule check. No failed canonical proof gets a new route.
      const equivalentAlias=field.field==="amount"&&item?.origin_request_id===request.request_id&&
        item.source==="llm"&&/^견적금액/.test(proofText(field.evidence))&&
        request.rule_fields.some(rule=>rule.field===field.field&&rule.action==="set"&&sameValue(rule.value,field.value))&&
        scopedFieldParts(request.form,request.text,field.field).some(part=>proofText(part).includes(proofText(field.evidence).replace(/^견적(?=금액)/,"")));
      if(!equivalentAlias){
        routes.set(field.field,{...field,action:"uncertain",status:"uncertain",value:null,alternatives:[]});
        continue;
      }
    }
    if(operation?.kind==="explicit_change"&&operation.valueSource==="utterance"&&field.action==="set"&&item?.correction==null){
      const plan=literalPlan(request,field,intent,analysis);
      if(plan.kind==="simple"){
        const independent=verifySimpleLiteral(request,field,opts,analysis,plan);
        const numeric=field.field!=="amount"||(!issues.amount&&
          (confirmedAmount===undefined||confirmedAmount===field.value));
        routes.set(field.field,independent&&numeric?field:{...field,action:"uncertain",status:"uncertain",value:null,alternatives:[]});
        continue;
      }
    }
    // The simple route already establishes full positive literal proof.
    // Only the remaining complex preview needs the exact-list/owned-value
    // proof here; do not re-veto an already proven independent operation.
    if(verifiedDatePreview(field,request,opts,intent,raw)===false){
      routes.set(field.field,{...field,action:"uncertain",status:"uncertain",value:null,alternatives:[]});
      continue;
    }
    legacy.push(field);
  }
  if(legacy.length){
    let old=checkedIntentFields(checkedNumericFields({...response,fields:legacy},issues,confirmedAmount,verifiedCrew),
      intent,false,request,opts,firstOrigins,raw);
    const crew=request.form==="dates"&&!crewReplacement?readCrew(request.text).value:undefined;
    if(crew!==undefined&&intent.requested.has("crew"))old={...old,fields:old.fields.map(f=>f.field==="crew"&&
      f.status==="valid"&&f.action==="set"&&f.value!==crew?{...f,action:"uncertain",status:"invalid",value:null,alternatives:[]}:f)};
    for(const field of old.fields)routes.set(field.field,field);
  }
  return {...response,fields:response.fields.map(f=>routes.get(f.field)??f)};
}

function hasCrewCorrectionProof(raw: unknown, request: AssistRequest, replacement: NonNullable<ReturnType<typeof readCrewReplacement>>): boolean {
  if (!raw || typeof raw !== "object") return false;
  const fields = (raw as Record<string, unknown>).fields;
  if (!Array.isArray(fields)) return false;
  const matches = fields.filter((field): field is Record<string, unknown> =>
    !!field && typeof field === "object" && field.field === "crew");
  if (matches.length !== 1) return false;
  const field = matches[0];
  if (field.origin_request_id !== request.request_id || field.source !== "llm" ||
    field.status !== "valid" || field.action !== "set" || field.value !== replacement.next ||
    !field.correction || typeof field.correction !== "object") return false;
  const proof = field.correction as Record<string, unknown>;
  const matchesSpan = (value: unknown, span: string, token: string) =>
    typeof value === "string" && !!clauseProofText(value) &&
    proofText(request.text).includes(proofText(value)) &&
    proofText(span).includes(clauseProofText(value)) && clauseProofText(value).includes(proofText(token));
  return proof.kind === "replace" && proof.rejected_value === replacement.old &&
    matchesSpan(proof.rejected_evidence, replacement.oldSpan, replacement.oldToken) &&
    matchesSpan(proof.accepted_evidence, replacement.nextSpan, replacement.nextToken);
}

function checkedNumericFields(response: AssistResponse, issues: InputIssues, amount: number | undefined, confirmedCrew?: number): AssistResponse {
  return { ...response, fields: response.fields.map((field) => {
    if (field.field !== "amount" && field.field !== "crew") return field;
    if (field.action === "keep" && field.status === "valid") return field;
    if (field.field === "crew" && confirmedCrew !== undefined && field.status === "valid" &&
      field.action === "set" && field.value === confirmedCrew) return field;
    const wrongAmount = field.field === "amount" && amount !== undefined &&
      (field.action === "set" && field.value !== amount ||
       field.action === "clear" || field.alternatives.some((a) => a.action !== "set" || a.value !== amount));
    if (!issues[field.field] && !wrongAmount) return field;
    return { ...field, status: "invalid", action: "uncertain", value: null, alternatives: [] };
  }) };
}

// Scalar correction metadata is deliberately not carried into previousCandidate.
// For a reported amount, check it on this server response before allowing Apply.
function serverVerifiedAmount(
  raw: unknown, request: AssistRequest, correction: { old: number; next: number },
): boolean {
  if (!raw || typeof raw !== "object") return false;
  const fields = (raw as Record<string, unknown>).fields;
  if (!Array.isArray(fields)) return false;
  const amounts = fields.filter((field): field is Record<string, unknown> =>
    !!field && typeof field === "object" && (field as Record<string, unknown>).field === "amount");
  if (amounts.length !== 1) return false;
  const field = amounts[0];
  if (field.status !== "valid" || field.action !== "set" || field.value !== correction.next ||
    field.origin_request_id !== request.request_id || !field.correction || typeof field.correction !== "object") return false;
  const relation = field.correction as Record<string, unknown>;
  const normalized = (value: string) => value.normalize("NFKC").replace(/\s+/g, "");
  const utterance = normalized(request.text);
  return relation.kind === "replace" && relation.rejected_value === correction.old &&
    [relation.rejected_evidence, relation.accepted_evidence].every((evidence) =>
      typeof evidence === "string" && !!normalized(evidence) && utterance.includes(normalized(evidence)));
}

export type FieldChoice = { selected: boolean; alternative: number | null };
export type AssistPhase = "idle" | "loading" | "ready" | "applied" | "denied";
export type AssistView = {
  phase: AssistPhase;
  utterance: string;
  response: AssistResponse | null;
  choices: Record<string, FieldChoice>;
  error: string | null;
  appliedFields: string[];
};
export type AssistRequester = (request: AssistRequest, signal: AbortSignal) => Promise<unknown>;

function requestId(): string {
  if (typeof crypto !== "undefined" && "randomUUID" in crypto) return crypto.randomUUID();
  return String(Date.now()) + "-" + Math.random().toString(16).slice(2);
}

function choicesFor(
  response: AssistResponse, previous: AssistResponse | null, oldChoices: Record<string, FieldChoice>,
): Record<string, FieldChoice> {
  return Object.fromEntries(response.fields.map((field) => {
    const old = previous?.fields.find((item) => item.field === field.field);
    const retained = old && field.origin_request_id && field.origin_request_id !== response.request_id;
    if ((old === field || retained) && field.status === "valid" && old?.status === "valid" &&
      actionable(field) && sameValue(old.value, field.value) && oldChoices[field.field]) {
      return [field.field, { ...oldChoices[field.field] }];
    }
    return [field.field, {
      selected: field.status === "valid" && field.action === "set" &&
        !(Array.isArray(field.value) && field.value.length === 0),
      alternative: null,
    }];
  }));
}

function baselinesFor(
  incoming: AssistResponse, previous: AssistResponse | null,
  oldBaselines: AssistCurrent | null, latest: AssistCurrent,
): AssistCurrent {
  const snapshot = copyCurrent(incoming.form, latest);
  if (!previous || !oldBaselines) return snapshot;
  for (const field of incoming.fields) {
    const old = previous.fields.find((item) => item.field === field.field);
    const retained = field.origin_request_id && field.origin_request_id !== incoming.request_id;
    if ((field.action === "keep" || retained) && old?.status === "valid" &&
      (old.action === "set" || old.action === "clear")) {
      const value = oldBaselines[field.field];
      snapshot[field.field] = Array.isArray(value) ? [...value] : value;
    }
  }
  return snapshot;
}

function proposedAction(field: CandidateField, choice: FieldChoice | undefined): { value: AssistValue } | null {
  if (!choice?.selected) return null;
  if (field.status === "valid" && (field.action === "set" || field.action === "clear")) {
    return { value: field.action === "clear" ? null : field.value };
  }
  if (field.status === "conflict" && choice.alternative !== null) {
    const option = field.alternatives[choice.alternative];
    if (option && (option.action === "set" || option.action === "clear")) {
      return { value: option.action === "clear" ? null : option.value };
    }
  }
  return null;
}

export class AssistSession {
  readonly form: AssistForm;
  readonly resourceId: string;
  private requester: AssistRequester;
  private state: AssistView = {
    phase: "idle", utterance: "", response: null, choices: {}, error: null, appliedFields: [],
  };
  private sequence = 0;
  private controller: AbortController | null = null;
  private listener: ((value: AssistView) => void) | null = null;
  private contextText = "";
  // 후보가 다른 칸의 요청을 거쳐 이어져도 처음 확인된 필드별 출처를 기억해요.
  private firstOrigins: Record<string, string> = {};
  private baselines: AssistCurrent | null = null;
  private validationOptions: RuleOptions | null = null;
  private disposed = false;
  private issuesCallback?: (issues: InputIssues) => void;
  private liveReader?: () => LiveState;
  private manualBaselines: Readonly<Record<string, number>> = {};
  private holds: Record<string, FieldHold> = {};
  private legacyIssues: InputIssues = {};

  constructor(
    form: AssistForm,
    resourceId: string,
    requester: AssistRequester = interpretAssist,
  ) {
    this.form = form;
    this.resourceId = resourceId;
    this.requester = requester;
  }

  get view(): AssistView { return this.state; }

  subscribe(listener: (value: AssistView) => void): () => void {
    // React 개발 모드의 effect 재실행에서는 같은 세션 인스턴스를 다시 구독해요.
    this.disposed = false;
    this.listener = listener;
    listener(this.state);
    return () => { if (this.listener === listener) this.listener = null; };
  }

  private publish(value: AssistView): void {
    if (this.disposed) return;
    this.state = value;
    this.listener?.(value);
  }

  private transitionHold(field: string, event: HoldEvent): string | null | undefined {
    const transition = reduceFieldHold(this.holds[field] ?? {}, event);
    if (activeHold(transition.state) === undefined) delete this.holds[field];
    else this.holds[field] = transition.state;
    return transition.delta;
  }

  private emitHolds(delta: FieldHoldDelta, pending: boolean): void {
    this.issuesCallback?.({ ...this.legacyIssues, fieldHolds: delta, pending });
  }

  private matchesManual(field: string): boolean {
    return (this.manualBaselines[field] ?? 0) === (this.liveReader?.().manualVersions?.[field] ?? 0);
  }

  private settleHolds(response: AssistResponse | null, request: AssistRequest, intent: RequestIntent, raw?: unknown): void {
    const delta: FieldHoldDelta = {};
    const now = this.liveReader?.().current ?? request.current;
    const excluded = this.form === "period" ? response?.fields.find(f => f.field === "excluded") : undefined;
    const exclusionOwnsDates = !!excluded && !!this.validationOptions &&
      verifiedLiteralList(excluded,request,this.validationOptions,intent,raw);
    for (const field of formKeys(this.form)) {
      if (!this.matchesManual(field)) {
        this.transitionHold(field, { kind: "manual" });
        continue;
      }
      if (!sameValue(now[field] ?? null, request.current[field] ?? null)) {
        // No manual acknowledgement: an obsolete response cannot settle a hold.
        this.transitionHold(field, { kind: "cancel" });
        continue;
      }
      const state = this.holds[field] ?? {};
      const priorHeld = state.settled !== undefined;
      const priorCandidate = !!request.previous_candidate?.fields.some(item => item.field === field);
      const provisional = state.provisional !== undefined;
      const candidate = response?.fields.find((f) => f.field === field);
      const evidence = proofText(candidate?.evidence ?? "");
      const clauseEvidence = clauseProofText(evidence);
      const keepScopes = [
        ...(intent.currentKeep.has(field) ? intent.currentKeepEvidence.get(field) ?? [] : []),
        ...(!priorHeld && !priorCandidate
          ? intent.implicitKeepEvidence.get(field) ?? [] : []),
      ];
      const withdrawal=intent.withdrawalProofs.get(field);
      const verifiedWithdrawal=!!withdrawal&&withdrawalEvidenceMatches(withdrawal,evidence,field)&&
        withdrawalValueMatches(withdrawal,field,request,this.validationOptions??undefined);
      const linkedWithdrawal = !!candidate && linkedScalarWithdrawal(candidate,request,this.validationOptions??undefined,raw);
      const ambiguousPending = intent.unboundWithdrawal && (priorHeld || priorCandidate) && !linkedWithdrawal;
      // 같은 문장의 완결된 연결형 유지절은 서버가 전체 문장을 근거로
      // 반환하더라도 해당 칸의 실제 절을 독립적으로 확인할 수 있어요.
      // 별개 문장의 전체 근거, 다른 칸의 근거만 있는 경우는 그대로 막아요.
      const linkedProof=!!candidate&&ownsLinkedCurrentRetentionEvidence(this.form,
        this.form==="extra"?extraTextScopes(request.text).intentText:request.text,field,
        this.form==="extra"?extraTextScopes(candidate.evidence).intentText:candidate.evidence);
      const verifiedKeep = candidate?.status === "valid" && candidate.action === "keep" &&
        candidate.intent_kind === "retain_current" && candidate.value_source === "current" &&
        candidate.origin_request_id === request.request_id &&
        !!clauseEvidence && proofText(request.text).includes(evidence) &&
        (linkedWithdrawal || verifiedWithdrawal || keepScopes.some(scope => proofText(scope).includes(clauseEvidence)) || linkedProof ||
          canonicalFresh(candidate,request,raw,"retain_current","current") &&
          ownsCurrentRetentionEvidence(this.form,request.text,field,candidate.evidence));
      const scopedKeep = candidate && !verifiedKeep
        ? scopedRetentionProof(candidate,request,intent,this.validationOptions??undefined,raw,priorHeld||priorCandidate)
        : undefined;
      const unchanged = candidate?.status === "valid" && candidate.action === "set" &&
        sameValue(candidate.value, request.current[field] ?? null);
      const noMention = candidate?.status === "valid" && candidate.action === "keep" &&
        candidate.intent_kind === "no_mention" && candidate.origin_request_id === request.request_id &&
        !candidate.evidence.trim();
      const canonicalNoMention = !!candidate && noMention && candidate.value === null &&
        canonicalFresh(candidate,request,raw,"no_mention",null);
      // Distinguish a hold created by this Send's parser from a pre-existing
      // unresolved hold. A canonical no-mention can retract only the former,
      // and only when the original text contains no independent field signal.
      const dismissProvisional = provisional && noMention && !priorCandidate && !ambiguousPending &&
        (!intent.requested.has(field) && !intent.blocked.has(field) && !this.legacyIssues[field as "amount" | "crew" | "days"] ||
         canonicalNoMention && !intent.cancelAll && !intent.unboundWithdrawal &&
          (!hasIndependentFieldSignal(this.form,request.text,field) ||
           (field === "start" || field === "end") && !intent.blocked.has(field) &&
            (exclusionOwnsDates || !!response && !!this.validationOptions &&
             siblingOwnsBoundary(field,response,request,intent,this.validationOptions,raw))));
      let outcome: HoldOutcome = { kind: "preserve" };
      if (!ambiguousPending && (verifiedKeep || scopedKeep === "current")) outcome = { kind: "verified-current" };
      else if (!ambiguousPending && unchanged) outcome = { kind: "unchanged" };
      else if (!ambiguousPending && response && intent.cancelAll && !intent.requested.size)
        outcome = { kind: "withdrawn" };
      else if (noMention || scopedKeep === "provisional")
        outcome = { kind: "no-mention", dismissProvisional: !!(dismissProvisional || scopedKeep === "provisional" && !ambiguousPending) };
      else if (!provisional && !priorHeld && candidate &&
        !this.validationOptions?.immutableFields?.includes(field) &&
        (actionable(candidate) || candidate.status !== "valid" && !!candidate.evidence.trim())) {
        // 원문과 대조했을 때 독립된 다른 칸의 근거만 가진 거부된
        // 문자열 후보는 미언급 문자열 칸에 새 보류를 만들지 않아요.
        // 기존 보류나 실제 명시 요청은 이 분기 밖에서 그대로 보존해요.
        if(this.form==="extra"&&(field==="title"||field==="reason")&&!actionable(candidate)&&
          proofText(request.text).includes(evidence)){
          const owner=requestIntent("extra",extraTextScopes(candidate.evidence).intentText).requested;
          if(owner.size>0&&!owner.has(field))continue;
        }
        // 규칙에 없던 새 서버 후보도 적용 전에는 옛 값으로 저장하지 않아요.
        outcome = { kind: "review", message: FIELD_HOLD };
      }
      const changed = this.transitionHold(field, { kind: "settle", outcome });
      if (changed !== undefined) delta[field] = changed;
    }
    this.emitHolds(delta, false);
  }

  send(text: string, current: AssistCurrent, options: RuleOptions, onInputIssues?: (issues: InputIssues) => void,
    getLive?: () => LiveState): boolean {
    if (this.disposed || this.state.phase === "loading") return false;
    const utterance = text.trim();
    if (!utterance || utterance.length > 1000) {
      this.publish({ ...this.state, error: "메시지를 1~1000자로 적어 주세요." });
      return false;
    }
    const before = this.state.phase === "ready" ? this.state.response : null;
    const priorChoices = this.state.choices;
    const priorBaselines = this.baselines;
    const priorContext = before ? this.contextText : "";
    // 새 후보는 이번 Send 순간의 폼을 기준으로 삼아요. 이전 후보는 최초 Send의 기준값과 비교해요.
    const snapshot = copyCurrent(this.form, current);
    // 직전 후보 원문과 새 글을 함께 보낼 수 있는 길이 안에서만 문맥을 잇습니다.
    const keepPrior = !!before && priorContext.length + utterance.length + 1 <= 1000;
    const historyOverflow = !!before && !keepPrior && before.fields.some(actionable);
    const historyWarning = "이전 후보와 이번 메시지가 1000자를 넘어 이전 후보를 이번 결과에 포함하지 않았어요. 다시 확인해 주세요.";
    const versions = { ...getLive?.().manualVersions };
    // 폼에서 이미 수동으로 해제한 보류를 다음 응답의 보류 판정에 끌고 오지 않아요.
    for (const field of Object.keys(this.holds)) {
      if ((this.manualBaselines[field] ?? 0) !== (versions[field] ?? 0)) this.transitionHold(field, { kind: "manual" });
    }
    const fresh = keepPrior ? freshPrevious(before, priorBaselines, snapshot) : null;
    const freshFields = fresh?.fields.filter((field) =>
      (this.manualBaselines[field.field] ?? 0) === (versions[field.field] ?? 0)) ?? [];
    const previous = fresh && freshFields.length ? { ...fresh, fields: freshFields } : null;
    const firstOrigins = previous ? { ...this.firstOrigins } : {};
    const context = previous ? priorContext + "\n" + utterance : utterance;
    const opts: RuleOptions = {
      ...options, holidays: [...(options.holidays ?? [])], vatModes: [...(options.vatModes ?? [])],
      immutableFields: [...(options.immutableFields ?? [])],
    };
    const id = requestId();
    const extraScope = this.form === "extra" ? extraTextScopes(utterance) : undefined;
    const intentText = extraScope?.intentText ?? utterance, scalarText = extraScope?.scalarText ?? utterance;
    const intent = requestIntent(this.form, intentText);
    if (extraScope && /(?:라면|다면|으면|이면|하면|되면|경우)\s*(?:[,;]|\s)/.test(scalarText)) {
      // 연결된 조건은 유지하되 독립 문자열의 철회를 견적 전체 철회로 보지 않아요.
      const scalarIntent = requestIntent("quote",scalarText);
      for (const field of ["amount","vat"]) if (intent.requested.has(field) && scalarIntent.blocked.has(field)) intent.blocked.add(field);
    }
    const reported = (this.form === "quote" || this.form === "extra") && historicalAmountReport(scalarText);
    const historical = reported ? historicalAmountCorrection(scalarText, opts.today, this.form) : undefined;
    const oldMatches = historical && previous?.fields.some((field) =>
      field.field === "amount" && field.status === "valid" && field.action === "set" && field.value === historical.old);
    const confirmedAmount = this.form === "quote" || this.form === "extra" ? amountToConfirm(scalarText, opts.today, this.form) : undefined;
    const issues = assistInputIssues(this.form, scalarText, opts, confirmedAmount);
    if (reported && !oldMatches) issues.amount = "이전 금액을 확인하지 못했어요. 금액을 직접 적어 주세요.";
    const crewReplacement = this.form === "dates" ? readCrewReplacement(utterance) : undefined;
    const priorCrew = previous?.fields.find((field) => field.field === "crew" && field.action === "set")?.value;
    const confirmedCrew = crewReplacement && priorCrew === crewReplacement.old &&
      crewReplacement.old <= (opts.crewMax ?? 6) && crewReplacement.next <= (opts.crewMax ?? 6)
      ? crewReplacement.next : undefined;
    if (crewReplacement) issues.crew = "인원 후보를 확인해 적용하거나 보낼 인원을 다시 골라 주세요.";
    // 확인되지 않은 금액으로 다른 칸만 저장되는 것을 요청을 보내는 순간부터 막아요.
    const hold = (reported || confirmedAmount !== undefined && confirmedAmount !== snapshot.amount) && !issues.amount
      ? { ...issues, amount: "금액 후보를 확인해 적용하거나 금액을 직접 입력해 주세요." } : issues;
    const parsed = ruleFields(this.form, utterance, opts, snapshot, previous?.fields??[]);
    const requested = new Set(intent.requested);
    for (const field of parsed) if (field.action !== "keep") requested.add(field.field);
    for (const field of ["amount", "crew", "days"] as const) if (hold[field]) requested.add(field);
    if (this.form === "period" && hold.period && !["start", "end", "excluded"].some(f => requested.has(f))) {
      if (hold.periodScope === "excluded") requested.add("excluded");
      else { requested.add("start"); requested.add("end"); }
    }
    if (intent.cancelAll) for (const field of Object.keys(this.holds)) requested.add(field);
    if (intent.unboundWithdrawal) {
      for (const field of Object.keys(this.holds)) requested.add(field);
      for (const field of previous?.fields ?? []) requested.add(field.field);
    }
    for (const field of opts.immutableFields ?? []) requested.delete(field);
    for (const field of requested) if (!formKeys(this.form).includes(field)) requested.delete(field);
    this.issuesCallback = onInputIssues;
    this.liveReader = getLive;
    this.manualBaselines = versions;
    this.legacyIssues = hold;
    const initial: FieldHoldDelta = {};
    for (const field of requested)
      initial[field] = this.transitionHold(field, { kind: "begin", message: hold[field as "amount" | "crew" | "days"] ?? FIELD_HOLD });
    this.emitHolds(initial, true);
    const fallbackIssues = reported ? hold : issues;
    const fallback = checkedIntentFields(checkedNumericFields(
      ruleResponse(this.form, id, utterance, opts, parsed), fallbackIssues, confirmedAmount), intent, true);
    const preview = checkedIntentFields(checkedNumericFields(
      mergeKeptFields(previous, fallback), fallbackIssues, confirmedAmount), intent, true);
    const token = ++this.sequence;
    this.controller?.abort();
    const abort = new AbortController();
    this.controller = abort;
    this.contextText = context;
    this.firstOrigins = firstOrigins;
    this.baselines = baselinesFor(fallback, previous, priorBaselines, snapshot);
    this.validationOptions = opts;
    this.publish({
      phase: "loading", utterance, response: preview,
      choices: choicesFor(preview, previous, priorChoices),
      error: null, appliedFields: [],
    });
    const request: AssistRequest = {
      request_id: id,
      form: this.form,
      resource_id: this.resourceId,
      text: utterance,
      current: snapshot,
      previous_candidate: previous && priorBaselines
        ? previousCandidate(previous, priorContext, priorBaselines) : null,
      rule_fields: parsed,
    };
    void (async () => {
      try {
        const raw = await this.requester(request, abort.signal);
        if (this.disposed || abort.signal.aborted || token !== this.sequence) return;
        const verifiedCrew = confirmedCrew !== undefined && crewReplacement &&
          hasCrewCorrectionProof(raw, request, crewReplacement) ? confirmedCrew : undefined;
        const responseIssues = { ...(reported && (!historical || !oldMatches || !serverVerifiedAmount(raw, request, historical))
          ? hold : issues) };
        // 읽지 못한 것과 원문에서 독립적으로 확인한 모순을 구분해요.
        if (responseIssues.amount && !reported && !writtenMoney(scalarText).mentioned && !intent.blocked.has("amount") &&
          !uncertainCompoundAmount(scalarText) && confirmedAmount === undefined &&
          !/잘못\s*(?:말씀드렸|말했|적었|썼)|정확히는/.test(scalarText) &&
          parseQuote(scalarText, opts.today).amount === undefined) delete responseIssues.amount;
        const response = checkedServerFields(raw,request,opts,intent,responseIssues,confirmedAmount,verifiedCrew,!!crewReplacement,firstOrigins,intentText);
        if (historyOverflow) response.warnings = [...response.warnings, historyWarning];
        // 서버의 keep/보류가 이전 후보를 제외했다면 화면에서 다시 되살리지 않아요.
        this.baselines = baselinesFor(response, previous, priorBaselines, snapshot);
        for (const field of response.fields) {
          if (field.status === "valid" && actionable(field) && !this.firstOrigins[field.field]) {
            this.firstOrigins[field.field] = field.origin_request_id ?? request.request_id;
          }
        }
        this.legacyIssues = responseIssues;
        this.settleHolds(response, request, intent, raw);
        this.publish({
          phase: "ready", utterance, response,
          choices: choicesFor(response, previous, priorChoices),
          error: null, appliedFields: [],
        });
      } catch (error) {
        if (this.disposed || abort.signal.aborted || token !== this.sequence) return;
        if (error instanceof AssistApiError && error.status >= 400 && error.status < 500) {
          this.baselines = null;
          this.validationOptions = null;
          this.settleHolds(null, request, intent);
          this.publish({ phase: "denied", utterance, response: null, choices: {}, error: error.message, appliedFields: [] });
          return;
        }
        // 실패한 서버 응답으로는 이전 후보를 재검증할 수 없어요. 이번 글의 규칙 후보만 남겨요.
        const warning = error instanceof AssistApiError ? error.message :
          error instanceof Error ? error.message : "해석 요청에 문제가 생겼어요.";
        this.contextText = utterance;
        this.firstOrigins = Object.fromEntries(fallback.fields.filter(field => field.status === "valid" && actionable(field))
          .map(field => [field.field, field.origin_request_id ?? request.request_id]));
        this.baselines = copyCurrent(this.form, snapshot);
        this.settleHolds(fallback, request, intent);
        this.publish({
          phase: "ready", utterance,
          response: {
            ...fallback,
            warnings: [
              ...fallback.warnings,
              warning + " 규칙으로 읽은 이번 메시지의 후보만 보여 드려요.",
              ...(previous ? ["이전 후보는 서버에서 다시 확인하지 못해 이번 결과에 포함하지 않았어요."] : []),
              ...(historyOverflow ? [historyWarning] : []),
            ],
          },
          choices: choicesFor(fallback, null, {}),
          error: null, appliedFields: [],
        });
      } finally {
        if (token === this.sequence) this.controller = null;
      }
    })();
    return true;
  }

  select(fieldName: string, selected: boolean): void {
    const field = this.state.response?.fields.find((item) => item.field === fieldName);
    if (this.state.phase !== "ready" || !field ||
      !((field.status === "valid" && (field.action === "set" || field.action === "clear")) ||
        (field.status === "conflict" && this.state.choices[fieldName]?.alternative !== null))) return;
    this.publish({
      ...this.state, error: null,
      choices: { ...this.state.choices, [fieldName]: { ...this.state.choices[fieldName], selected } },
    });
  }

  chooseAlternative(fieldName: string, index: number): void {
    const field = this.state.response?.fields.find((item) => item.field === fieldName);
    if (this.state.phase !== "ready" || field?.status !== "conflict" || !field.alternatives[index]) return;
    this.publish({
      ...this.state, error: null,
      choices: { ...this.state.choices, [fieldName]: { selected: true, alternative: index } },
    });
  }

  apply(current: AssistCurrent, onApply: (changes: AssistCurrent) => void): boolean {
    if (this.disposed || this.state.phase !== "ready" || !this.state.response || !this.baselines) return false;
    const selected: AssistCurrent = {};
    const now = copyCurrent(this.form, current);
    const changedManually: string[] = [];
    for (const field of this.state.response.fields) {
      const proposed = proposedAction(field, this.state.choices[field.field]);
      if (!proposed) continue;
      if (!sameValue(this.baselines[field.field], now[field.field]) || !this.matchesManual(field.field)) changedManually.push(field.field);
      else selected[field.field] = proposed.value;
    }
    if (changedManually.length) {
      this.publish({ ...this.state, error: "입력 칸을 직접 고친 뒤 값이 달라졌어요. 새 메시지로 후보를 다시 확인해 주세요." });
      return false;
    }
    const names = formKeys(this.form).filter((field) => Object.prototype.hasOwnProperty.call(selected, field));
    if (!names.length) {
      this.publish({ ...this.state, error: "적용할 필드를 선택해 주세요." });
      return false;
    }
    if (this.form === "period" && this.validationOptions && !validCombinedPeriod(now, selected, this.validationOptions)) {
      this.publish({ ...this.state, error: "선택한 날짜를 현재 달력과 합치면 기간이나 제외일이 맞지 않아요. 필드 선택을 확인해 주세요." });
      return false;
    }
    // 먼저 완료 상태로 바꿔 두 번 누르거나 늦게 온 응답이 다시 적용되지 않아요.
    const resolved: FieldHoldDelta = {};
    for (const field of this.state.response.fields) {
      // 명시적 적용과 유효 후보의 의식적인 제외만 해결된 것으로 처리해요.
      if (this.matchesManual(field.field) && sameValue(this.baselines[field.field], now[field.field]) &&
          (field.field in selected || field.status === "valid" && actionable(field))) resolved[field.field] = this.transitionHold(field.field, { kind: "apply" });
    }
    this.publish({ ...this.state, phase: "applied", error: null, appliedFields: names });
    this.emitHolds(resolved, false);
    onApply(selected);
    return true;
  }

  cancel(): void {
    ++this.sequence;
    this.controller?.abort();
    this.controller = null;
    this.contextText = "";
    this.firstOrigins = {};
    this.baselines = null;
    this.validationOptions = null;
    for (const field of Object.keys(this.holds)) this.transitionHold(field, { kind: "cancel" });
    this.emitHolds({}, false);
    this.publish({ phase: "idle", utterance: "", response: null, choices: {}, error: null, appliedFields: [] });
  }

  dispose(): void {
    this.cancel();
    this.disposed = true;
    this.listener = null;
    this.issuesCallback = undefined;
    this.liveReader = undefined;
  }
}
