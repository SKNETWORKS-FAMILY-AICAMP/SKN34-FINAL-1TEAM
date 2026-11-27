import { sameValue, normalizedWireEvidence, type AssistRequest, type CandidateField, type RuleOptions } from "./protocol";
import { operationScopeFacts, operationScopeRisk, operationVatModes, vatSemanticTokens, type RequestIntent } from "./requestIntent";
import { readCrew, calendarEvidenceDates, extraTextGrounding, sharedScalarTextValue,
  parseQuote, uncertainCompoundAmount } from "./parse";
import { classifyDurationQuantity } from "./quantity";
import { writtenMoney } from "./writtenMoney";

type Scope = ReturnType<typeof operationScopeFacts>[number];
export type OperationAnalysis = {
  scopes: Scope[];
  originalScopes: Scope[];
};
export function analyzeOperations(request: AssistRequest, text: string): OperationAnalysis {
  const scopes=operationScopeFacts(request.form,text);
  return {scopes,originalScopes:text===request.text?scopes:operationScopeFacts(request.form,request.text)};
}
const compact = (s: string) => normalizedWireEvidence(s).replace(/[.!。]+$/,"");
const explicitAction = /(?:주세요|줘요|주십시오|주시겠어요|주실래요|부탁드립니다|부탁드려요|부탁해요|하겠습니다|합니다|할게요|해요|바랍니다|됩니다|됩니다요|주시면\s*됩니다)[.!。?？]*$/;
const assignment = /변경|수정|정정|확정|등록|입력|기재|설정|지정|결정|조정|제안|올려|줄여|늘려|바꿔|고쳐|잡아|정해|넣어|적어|써|청구|발행|후보|초안/;
const dangerousContext = /미정|보류|취소|철회|하지\s*(?:마|말|않)|하지\s*못|(?:안|못)\s*(?:하|넣|바꾸)|가능하면|조건부|만약|아마|혹시|아니면|또는|검토\s*중|확인\s*중|[?？]/;
const contextualReason = /(?:것\s*같아서|때문에|라서|어서|아서|으니까|니까)\s*$/;
const closedObservation = /(?:것\s*같습니다|것\s*같아요|것\s*같네요|이면\s*됩니다|면\s*됩니다|어떨지\s*보고\s*싶어요)$/;
const risk = (request: AssistRequest, text: string, field: string) => operationScopeRisk(request.form,text,field);

type LiteralPlan = { kind: "complex" } | { kind: "simple"; scope: Scope; command: string; proof: string; observations: Scope[] };
const complex: LiteralPlan = {kind:"complex"};

/** Choose the route by operation structure, not by whether its proof passes.
 * Complex operations retain the established relation/range/legacy validator.
 * A selected simple route is terminal: a failed proof is never retried there. */
export function literalPlan(request: AssistRequest, field: CandidateField, intent: RequestIntent, analysis: OperationAnalysis): LiteralPlan {
  const name=field.field, proof=compact(field.evidence);
  if (intent.cancelAll || intent.unboundWithdrawal || intent.carryConflicts.has(name) ||
      intent.currentKeep.has(name) || intent.priorReferences.has(name) || intent.protectedDates.has(name) ||
      intent.vatConfirmation && name==="vat") return complex;
  if (name==="excluded" || request.form==="dates"&&name==="days" || name==="valid") return complex;
  const textField=name==="title"||name==="reason";
  const scopes=textField?analysis.originalScopes:analysis.scopes;
  const owned=scopes.filter(s=>s.fields.includes(name));
  // Text values may be masked by the shared resolver; recover its original
  // corresponding clause from the actual contiguous evidence when necessary.
  const match=owned.filter(s=>compact(s.part).includes(proof));
  if (match.length!==1 || !proof || match[0].fields.length!==1) return complex;
  const scope=match[0];
  // Named explicit single-field operations, with a complete action or a
  // comma-linked nominal sharing the next complete independent predicate.
  // Unnamed shorthand and multi-field/range forms keep their old route.
  if (!scope.labels.includes(name)) return complex;
  if (name==="start" || name==="end") {
    if (/부터|까지|[~∼〜～]|(?:\d{1,2}월\s*)?\d{1,2}일\s*(?:을|를|에서)\s*\d/.test(scope.part)) return complex;
  }
  const command=scope.part;
  const suffix=command.indexOf(field.evidence.replace(/[.!。]+$/,""));
  let actual=command;
  // A descriptive causal lead may be separated only at the exact explicit
  // target in the original clause, with no command, quantity or condition in it.
  if(suffix>0){
    const prefix=command.slice(0,suffix).trim(), tail=command.slice(suffix);
    const labels=operationScopeFacts(request.form,tail).flatMap(s=>s.labels);
    if(contextualReason.test(prefix)&&!dangerousContext.test(prefix)&&
      !/변경|수정|정정|확정|지정|요청|취소|철회|유지|그대로|(?:라면|다면|으면|이면|하면|되면|경우)/.test(prefix)&&
      !/\d|(?:[한두세네]|다섯|여섯)\s*명/.test(prefix)&&
      labels.length===1&&labels[0]===name) actual=tail;
  }
  const linked=textField && /(?:으로|로|이라고|라고)$/.test(actual) &&
    scopes.some(s=>s.sentence===scope.sentence&&s!==scope&&s.fields.length===1&&!s.fields.includes(name)&&
      (s.fields[0]==="amount"||s.fields[0]==="vat")&&explicitAction.test(s.part)&&assignment.test(s.part));
  // Ordinary text clauses use the established complete-span validator. Only
  // the cross-type shared predicate needs a distinct literal boundary proof.
  if(textField&&!linked)return complex;
  if (!linked&&(!explicitAction.test(actual)||!assignment.test(actual))) return complex;
  // Multiple independently complete same-field assignments, historical
  // relations, and explicit competing/correction syntax remain complex.
  // Closed explanatory observations are checked in the simple route instead.
  const others=owned.filter(s=>s!==scope);
  if (others.some(s=>s.sentence===scope.sentence||s.sentence>scope.sentence||!closedObservation.test(s.part))) return complex;
  if (intent.replacements.has(name)||/에서\s*(?:\d|[한두세네])|아니라|아니고|말고|대신|→|->|(?:라고|이라고|이라|라)\s*(?:했|말했|적었|입력했|말씀드렸)/.test(actual)) return complex;
  return {kind:"simple",scope,command:actual,proof,observations:others};
}

function scalarValue(request: AssistRequest, field: string, text: string, opts: RuleOptions): unknown {
  if(field==="crew") return readCrew(text).value;
  if(field==="days"&&request.form==="quote"){
    const q=classifyDurationQuantity(text);
    return !q.risk&&q.tokens.length===1?q.tokens[0].value:undefined;
  }
  if(field==="amount"){
    if(uncertainCompoundAmount(text))return;
    const written=writtenMoney(text);
    return written.mentioned?written.amount:parseQuote(text,opts.today).amount;
  }
  if(field==="start"||field==="end"){
    const dates=calendarEvidenceDates(text,opts.today,opts.baseMonth);
    return dates?.length===1?dates[0]:undefined;
  }
  if(field==="vat"){
    const modes=operationVatModes(text);
    return modes.length===1?modes[0]:undefined;
  }
  return undefined;
}

export function verifySimpleLiteral(request: AssistRequest, field: CandidateField, opts: RuleOptions,
    analysis: OperationAnalysis, plan: Extract<LiteralPlan,{kind:"simple"}>): boolean {
  const name=field.field, {scope,command,observations}=plan;
  if(scope.dependent || risk(request,command,name) || !compact(command).includes(plan.proof))return false;
  // A separate completed inquiry may not bind a later independent command;
  // open or unbound conditional/negative/cancel instructions still veto it.
  for(const s of analysis.scopes){
    if(s===scope||s.fields.length)continue;
    if(s.sentence<scope.sentence&&(/[?？]$/.test(s.part)||closedObservation.test(s.part))&&
        !/취소|철회|하지\s*(?:마|말|않)|주세요|주십시오/.test(s.part))continue;
    if(s.dependent||risk(request,s.part,name))return false;
  }
  for(const s of observations){
    if(dangerousContext.test(s.part)||!sameValue(scalarValue(request,name,s.part,opts) as CandidateField["value"],field.value))return false;
  }
  if(name==="title"||name==="reason"){
    if(typeof field.value!=="string"||!compact(field.evidence).includes(compact(field.value)))return false;
    const grounding=extraTextGrounding(request.text,name);
    if(grounding?.incomplete||grounding?.values.length!==1)return false;
    const exact=grounding.values[0];
    if(exact.value===field.value)return compact(exact.clause).includes(plan.proof);
    const following=analysis.originalScopes.find(s=>s.sentence===scope.sentence&&s!==scope&&s.fields.length===1&&
      !s.fields.includes(name)&&explicitAction.test(s.part)&&assignment.test(s.part));
    return !!following&&!following.dependent&&!risk(request,following.part,following.fields[0])&&
      sharedScalarTextValue(request.text,name,scope.part,field.value,following.part);
  }
  if(name==="vat"){
    const modes=operationVatModes(command), own=vatSemanticTokens(field.evidence);
    return modes.length===1&&modes[0]===field.value&&
      (own.length===1&&own[0].mode===field.value||operationVatModes(field.evidence).length===1&&
       operationVatModes(field.evidence)[0]===field.value);
  }
  const expected=scalarValue(request,name,command,opts), proved=scalarValue(request,name,field.evidence,opts);
  return expected!==undefined&&proved!==undefined&&sameValue(expected as CandidateField["value"],field.value)&&
    sameValue(proved as CandidateField["value"],field.value);
}
