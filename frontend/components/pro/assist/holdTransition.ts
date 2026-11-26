// Holds protect saving/sending; candidate visibility and validation are separate.
// A Send reserves fields provisionally. Only the response settlement promotes
// or resolves that reservation. Prior unresolved obligations have their own slot.
export type FieldHold = Readonly<{ settled?: string; provisional?: string }>;
export type HoldOutcome =
  | { kind: "verified-current" | "unchanged" | "withdrawn" }
  | { kind: "no-mention"; dismissProvisional: boolean }
  | { kind: "review"; message: string }
  | { kind: "preserve" };
export type HoldEvent =
  | { kind: "begin"; message: string }
  | { kind: "settle"; outcome: HoldOutcome }
  | { kind: "cancel" }
  | { kind: "manual" }
  | { kind: "apply" };
export type HoldTransition = { state: FieldHold; delta?: string | null };

export function activeHold(state: FieldHold): string | undefined {
  return state.provisional ?? state.settled;
}

// The only field-local hold state transition. The input is already classified
// by evidence/ownership validation, not raw wire metadata. This reducer never
// promotes a candidate, applies a form value, or interprets natural language.
export function reduceFieldHold(state: FieldHold, event: HoldEvent): HoldTransition {
  const active = activeHold(state);
  if (event.kind === "begin") return {
    state: { settled: state.settled, provisional: event.message }, delta: event.message,
  };
  // The form's manual callback already removed its own visible hold.
  if (event.kind === "manual") return { state: {} };
  if (event.kind === "apply") return { state: {}, delta: null };
  if (event.kind === "cancel") return { state: { settled: active } };
  const outcome = event.outcome;
  switch (outcome.kind) {
    case "verified-current":
    case "unchanged":
    case "withdrawn":
      return { state: {}, ...(active === undefined ? {} : { delta: null }) };
    case "no-mention":
      // No mention never resolves a real earlier obligation.
      if (outcome.dismissProvisional && state.settled === undefined && state.provisional !== undefined)
        return { state: {}, delta: null };
      return { state: { settled: active } };
    case "review":
      return active === undefined
        ? { state: { settled: outcome.message }, delta: outcome.message }
        : { state: { settled: active } };
    case "preserve":
      return { state: { settled: active } };
  }
}
