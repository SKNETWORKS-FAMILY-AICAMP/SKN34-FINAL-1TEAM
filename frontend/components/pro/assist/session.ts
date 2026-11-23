// 업체 도우미 한 화면의 후보 상태. 화면에는 미리보기만 보여 주고 확인 때만 폼 setter를 불러요.
import { AssistApiError, interpretAssist } from "./api";
import {
  type AssistCurrent, type AssistForm, type AssistRequest, type AssistResponse,
  type AssistValue, type CandidateField, type RuleOptions,
  copyCurrent, formKeys, mergeKeptFields, previousCandidate, readResponse, sameValue, validCombinedPeriod,
} from "./protocol";
import { ruleFields, ruleResponse } from "./rules";

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
    if (old === field && oldChoices[field.field]) return [field.field, { ...oldChoices[field.field] }];
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
    if (field.action === "keep" && old?.status === "valid" &&
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

  send(text: string, current: AssistCurrent, options: RuleOptions): boolean {
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
    // 직전 후보 원문과 새 글을 함께 보낼 수 있는 길이 안에서만 문맥을 잇습니다.
    const keepPrior = !!before && priorContext.length + utterance.length + 1 <= 1000;
    const previous = keepPrior ? before : null;
    const context = previous ? priorContext + "\n" + utterance : utterance;
    const snapshot = copyCurrent(this.form, current);
    const opts: RuleOptions = {
      ...options, holidays: [...(options.holidays ?? [])], vatModes: [...(options.vatModes ?? [])],
      immutableFields: [...(options.immutableFields ?? [])],
    };
    const id = requestId();
    const parsed = ruleFields(this.form, utterance, opts);
    const fallback = ruleResponse(this.form, id, utterance, opts, parsed);
    const preview = mergeKeptFields(previous, fallback);
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
      previous_candidate: previous ? previousCandidate(previous, priorContext) : null,
      rule_fields: parsed,
    };
    void (async () => {
      try {
        const raw = await this.requester(request, abort.signal);
        if (this.disposed || abort.signal.aborted || token !== this.sequence) return;
        const response = readResponse(raw, request, opts);
        const merged = mergeKeptFields(previous, response);
        this.baselines = baselinesFor(response, previous, priorBaselines, snapshot);
        this.publish({
          phase: "ready", utterance, response: merged,
          choices: choicesFor(merged, previous, priorChoices),
          error: null, appliedFields: [],
        });
      } catch (error) {
        if (this.disposed || abort.signal.aborted || token !== this.sequence) return;
        if (error instanceof AssistApiError && [401, 403, 429].includes(error.status)) {
          this.baselines = null;
          this.validationOptions = null;
          this.publish({ phase: "denied", utterance, response: null, choices: {}, error: error.message, appliedFields: [] });
          return;
        }
        // 연결 장애나 잘못된 응답일 때도 규칙 후보는 적용 전 확인받아요.
        const warning = error instanceof AssistApiError ? error.message :
          error instanceof Error ? error.message : "해석 요청에 문제가 생겼어요.";
        this.publish({
          phase: "ready", utterance,
          response: { ...preview, warnings: [...preview.warnings, warning + " 규칙으로 읽은 후보만 보여 드려요."] },
          choices: choicesFor(preview, previous, priorChoices),
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
