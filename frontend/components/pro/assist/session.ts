// 업체 도우미 한 화면의 후보 상태. 화면에는 미리보기만 보여 주고 확인 때만 폼 setter를 불러요.
import { AssistApiError, interpretAssist } from "./api";
import {
  type AssistCurrent, type AssistForm, type AssistRequest, type AssistResponse,
  type AssistValue, type CandidateField, type RuleOptions,
  copyCurrent, formKeys, freshPrevious, mergeKeptFields, previousCandidate, readResponse, sameValue, validCombinedPeriod,
} from "./protocol";
import { ruleFields, ruleResponse } from "./rules";
import { assistInputIssues, amountToConfirm, historicalAmountCorrection, historicalAmountReport } from "./guard";
import { readCrewReplacement } from "./parse";

export type InputIssues = ReturnType<typeof assistInputIssues>;

function hasCrewCorrectionProof(raw: unknown, request: AssistRequest, old: number): boolean {
  if (!raw || typeof raw !== "object") return false;
  const fields = (raw as Record<string, unknown>).fields;
  if (!Array.isArray(fields)) return false;
  const matches = fields.filter((field): field is Record<string, unknown> =>
    !!field && typeof field === "object" && field.field === "crew");
  if (matches.length !== 1) return false;
  const field = matches[0];
  if (field.origin_request_id !== request.request_id || field.source !== "llm" ||
    !field.correction || typeof field.correction !== "object") return false;
  const proof = field.correction as Record<string, unknown>;
  const normalize = (s: string) => s.normalize("NFKC").trim().replace(/\s+/g, " ");
  const utterance = normalize(request.text);
  const present = (value: unknown) => typeof value === "string" && !!normalize(value) &&
    utterance.includes(normalize(value));
  return proof.kind === "replace" && proof.rejected_value === old &&
    present(proof.rejected_evidence) && present(proof.accepted_evidence);
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
    if ((old === field || retained) && oldChoices[field.field]) return [field.field, { ...oldChoices[field.field] }];
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
  private baselines: AssistCurrent | null = null;
  private validationOptions: RuleOptions | null = null;
  private disposed = false;

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

  send(text: string, current: AssistCurrent, options: RuleOptions, onInputIssues?: (issues: InputIssues) => void): boolean {
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
    const previous = keepPrior ? freshPrevious(before, priorBaselines, snapshot) : null;
    const context = previous ? priorContext + "\n" + utterance : utterance;
    const opts: RuleOptions = {
      ...options, holidays: [...(options.holidays ?? [])], vatModes: [...(options.vatModes ?? [])],
      immutableFields: [...(options.immutableFields ?? [])],
    };
    const id = requestId();
    const reported = (this.form === "quote" || this.form === "extra") && historicalAmountReport(utterance);
    const historical = reported ? historicalAmountCorrection(utterance, opts.today, this.form) : undefined;
    const oldMatches = historical && previous?.fields.some((field) =>
      field.field === "amount" && field.status === "valid" && field.action === "set" && field.value === historical.old);
    const confirmedAmount = this.form === "quote" || this.form === "extra" ? amountToConfirm(utterance, opts.today, this.form) : undefined;
    const issues = assistInputIssues(this.form, utterance, opts, confirmedAmount);
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
    onInputIssues?.(hold);
    const parsed = ruleFields(this.form, utterance, opts);
    const fallbackIssues = reported ? hold : issues;
    const fallback = checkedNumericFields(ruleResponse(this.form, id, utterance, opts, parsed), fallbackIssues, confirmedAmount);
    const preview = checkedNumericFields(mergeKeptFields(previous, fallback), fallbackIssues, confirmedAmount);
    const token = ++this.sequence;
    this.controller?.abort();
    const abort = new AbortController();
    this.controller = abort;
    this.contextText = context;
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
          hasCrewCorrectionProof(raw, request, crewReplacement.old) ? confirmedCrew : undefined;
        const responseIssues = reported && (!historical || !oldMatches || !serverVerifiedAmount(raw, request, historical))
          ? hold : issues;
        const response = checkedNumericFields(readResponse(raw, request, opts), responseIssues, confirmedAmount, verifiedCrew);
        // 서버의 keep/보류가 이전 후보를 제외했다면 화면에서 다시 되살리지 않아요.
        this.baselines = baselinesFor(response, previous, priorBaselines, snapshot);
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
          this.publish({ phase: "denied", utterance, response: null, choices: {}, error: error.message, appliedFields: [] });
          return;
        }
        // 실패한 서버 응답으로는 이전 후보를 재검증할 수 없어요. 이번 글의 규칙 후보만 남겨요.
        const warning = error instanceof AssistApiError ? error.message :
          error instanceof Error ? error.message : "해석 요청에 문제가 생겼어요.";
        this.contextText = utterance;
        this.baselines = copyCurrent(this.form, snapshot);
        this.publish({
          phase: "ready", utterance,
          response: {
            ...fallback,
            warnings: [
              ...fallback.warnings,
              warning + " 규칙으로 읽은 이번 메시지의 후보만 보여 드려요.",
              ...(previous ? ["이전 후보는 서버에서 다시 확인하지 못해 이번 결과에 포함하지 않았어요."] : []),
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
      if (!sameValue(this.baselines[field.field], now[field.field])) changedManually.push(field.field);
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
    this.publish({ ...this.state, phase: "applied", error: null, appliedFields: names });
    onApply(selected);
    return true;
  }

  cancel(): void {
    ++this.sequence;
    this.controller?.abort();
    this.controller = null;
    this.contextText = "";
    this.baselines = null;
    this.validationOptions = null;
    this.publish({ phase: "idle", utterance: "", response: null, choices: {}, error: null, appliedFields: [] });
  }

  dispose(): void {
    this.cancel();
    this.disposed = true;
    this.listener = null;
  }
}
