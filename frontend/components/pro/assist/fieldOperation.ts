import { quantityDeltaSyntax } from "./quantity";
import { resolvedIntentSegments, requestIntent, operationScopeRisk, referencedPeriodExclusion } from "./requestIntent";
import { calendarEvidenceDates } from "./parse";
import { validSetValue, type AssistForm, type AssistCurrent, type RuleOptions } from "./protocol";

/** A delta is resolved only from the actual current baseline. The lexical
 * operand is never offered as an absolute value. An unresolved delta remains
 * an owned operation with no value, not an unrelated literal parse. */
export type RelativeQuantityOperation = Readonly<{kind:"delta";value?:number;evidence:string}>;
export function relativeQuantityOperation(form:AssistForm,text:string,field:string,current:AssistCurrent|undefined,
    opts:RuleOptions,hasPrior=false):RelativeQuantityOperation|undefined {
  if(!(form==="quote"&&field==="days"||form==="dates"&&field==="crew"))return;
  const scopes=resolvedIntentSegments(form,text);
  const owned=scopes.filter(s=>s.fields.includes(field));
  const matches=owned.flatMap(s=>{
    const syntax=quantityDeltaSyntax(s.part,field as "days"|"crew");
    return syntax?[{s,syntax}]:[];
  });
  if(!matches.length)return;
  const {s,syntax}=matches[0], evidence=s.part;
  const intent=requestIntent(form,text);
  const base=current?.[field];
  const fail:RelativeQuantityOperation={kind:"delta",evidence};
  if(matches.length!==1||owned.length!==1||s.fields.length!==1||s.dependent||
      intent.cancelAll||intent.unboundWithdrawal||intent.blocked.has(field)||intent.currentKeep.has(field)||
      intent.priorReferences.has(field)||intent.carryConflicts.has(field)||operationScopeRisk(form,s.part,field)||
      syntax.operand===undefined||syntax.priorReference||hasPrior&&!syntax.explicitCurrent&&syntax.base===undefined||
      typeof base!=="number"||!Number.isSafeInteger(base)||syntax.base!==undefined&&base!==syntax.base)return fail;
  const value=base+syntax.direction*syntax.operand;
  return validSetValue(form,field,value,opts)?{kind:"delta",value,evidence}:fail;
}

/** Anaphoric add is an update to the current list, not a replacement with
 * every mentioned date or a new boundary. Require the unique linked report
 * and preserve every existing member. */
export function referencedExclusionAddOperation(form:AssistForm,text:string,field:string,current:AssistCurrent|undefined,
    opts:RuleOptions,hasPrior=false):{kind:"add";value?:string[];evidence:string}|undefined {
  if(form!=="period"||field!=="excluded")return;
  const reference=referencedPeriodExclusion(text);
  if(reference?.kind!=="add")return;
  const fail={kind:"add" as const,evidence:text};
  const intent=requestIntent(form,text);
  const scopes=resolvedIntentSegments(form,text).filter(s=>s.fields.includes(field));
  if(hasPrior||intent.blocked.has(field)||intent.cancelAll||intent.unboundWithdrawal||
      scopes.some(s=>s.dependent||s.fields.length!==1||
        (s.sentence!==reference.antecedent&&s.sentence!==reference.command)))return fail;
  const dates=calendarEvidenceDates(reference.list,opts.today,opts.baseMonth),base=current?.excluded;
  if(!dates||dates.length!==1||!Array.isArray(base))return fail;
  const value=[...new Set([...base,...dates])].sort();
  return validSetValue(form,field,value,opts)?{kind:"add",value,evidence:text}:fail;
}
