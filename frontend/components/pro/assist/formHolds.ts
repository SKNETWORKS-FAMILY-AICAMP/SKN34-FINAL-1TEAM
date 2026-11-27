import { useRef, useState } from "react";
import { formKeys, type AssistCurrent, type AssistForm, type AssistResponse } from "./protocol";
import { resolvePeriodHolds, type PeriodHolds, type PeriodIssueScope } from "./periodHold";

// 해석 세션의 내부 변경분. 기존 호출자가 새 필드를 보내지 않아도 동작해요.
export type FormHoldIssues = {
  fieldHolds?: Partial<Record<string, string | null>>;
  pending?: boolean;
  amount?: string;
  crew?: string;
  days?: string;
  period?: string;
  periodScope?: PeriodIssueScope;
};

export type FormHoldState = {
  fields: Partial<Record<string, string>>;
  pending: boolean;
  legacyPeriod: PeriodHolds;
};

export function emptyFormHolds(): FormHoldState {
  return { fields: {}, pending: false, legacyPeriod: {} };
}

/** 새 필드별 변경분이 있으면 같은 이벤트의 이전 방식 보류는 무시해요. */
export function mergeFormHolds(form: AssistForm, old: FormHoldState, issues: FormHoldIssues): FormHoldState {
  const fields = { ...old.fields };
  let legacyPeriod = { ...old.legacyPeriod };
  if (issues.fieldHolds !== undefined) {
    // 이전 방식에서 넘어올 때 범위를 모르는 경계 보류는 보수적으로 이어 받아요.
    if (form === "period") {
      if (legacyPeriod.boundary) {
        fields.start ??= legacyPeriod.boundary;
        fields.end ??= legacyPeriod.boundary;
      }
      if (legacyPeriod.excluded) fields.excluded ??= legacyPeriod.excluded;
      legacyPeriod = {};
    }
    for (const field of formKeys(form)) {
      const change = issues.fieldHolds[field];
      if (change === null) delete fields[field];
      else if (typeof change === "string") fields[field] = change || "말씀하신 값을 확인해 주세요.";
    }
  } else {
    for (const field of ["amount", "crew", "days"] as const) {
      if (formKeys(form).includes(field) && issues[field]) fields[field] = issues[field];
    }
    if (form === "period" && issues.period) legacyPeriod[issues.periodScope ?? "boundary"] = issues.period;
  }
  return { fields, pending: issues.pending ?? old.pending, legacyPeriod };
}

/** 실제로 직접 고친 필드만 풀어요. 대기 중인 해석 자체는 계속 쓰기를 막아요. */
export function manuallyResolveHolds(old: FormHoldState, edited: readonly string[]): FormHoldState {
  const fields = { ...old.fields };
  const legacyPeriod = { ...old.legacyPeriod };
  for (const field of edited) delete fields[field];
  if (edited.includes("start") || edited.includes("end")) delete legacyPeriod.boundary;
  if (edited.includes("excluded")) delete legacyPeriod.excluded;
  return { ...old, fields, legacyPeriod };
}

/** 적용된 칸만 풀어요. 선택하지 않은 유효 후보의 해제는 세션의 명시적 null 변경분으로 받아요. */
export function appliedFormHolds(old: FormHoldState, changes: AssistCurrent, response: AssistResponse): FormHoldState {
  const fields = { ...old.fields };
  for (const field of Object.keys(changes)) delete fields[field];
  return { ...old, fields, legacyPeriod: response.form === "period"
    ? resolvePeriodHolds(old.legacyPeriod, changes, response) : old.legacyPeriod };
}

export const PENDING_FORM_MESSAGE = "AI 해석을 확인하고 있어요. 결과를 확인한 뒤 저장하거나 보내 주세요.";

export function formWriteBlock(state: FormHoldState): string | null {
  return state.pending ? PENDING_FORM_MESSAGE :
    Object.values(state.fields).find((message) => !!message) ??
    state.legacyPeriod.boundary ?? state.legacyPeriod.excluded ?? null;
}

/** 저장·발송 이벤트와 예약된 저장은 렌더링 전에도 최신 보류를 읽어요. */
export function useFormHolds(form: AssistForm) {
  const [state, setState] = useState(emptyFormHolds);
  const current = useRef(state);
  const manualVersions = useRef<Record<string, number>>({});
  function update(next: FormHoldState) {
    const old = current.current;
    if (old.pending === next.pending &&
        formKeys(form).every((field) => old.fields[field] === next.fields[field]) &&
        old.legacyPeriod.boundary === next.legacyPeriod.boundary &&
        old.legacyPeriod.excluded === next.legacyPeriod.excluded) return old;
    current.current = next;
    setState(next);
    return next;
  }
  return {
    state, current, manualVersions,
    receive: (issues: FormHoldIssues) => update(mergeFormHolds(form, current.current, issues)),
    manual: (edited: readonly string[]) => {
      for (const field of edited) manualVersions.current[field] = (manualVersions.current[field] ?? 0) + 1;
      return update(manuallyResolveHolds(current.current, edited));
    },
    applied: (changes: AssistCurrent, response: AssistResponse) => update(appliedFormHolds(current.current, changes, response)),
  };
}
