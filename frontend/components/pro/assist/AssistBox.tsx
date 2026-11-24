"use client";
// 기존 재재 채팅 전송으로 규칙·서버 해석을 시작하고, 업체 확인 전에는 업무 폼을 수정하지 않아요.
import { useContext, useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { Button } from "@/components/ui/Button";
import { VAT_MODE_LABEL } from "../text";
import { AssistSession, type InputIssues } from "./session";
import { AssistSlot } from "./slot";
import type { AssistCurrent, AssistForm, AssistValue, RuleOptions, CandidateField, Alternative, AssistResponse } from "./protocol";
import s from "./assist.module.css";

const FIELD_LABEL: Record<string, string> = {
  start: "시작일",
  end: "끝나는 날",
  excluded: "빼는 날",
  amount: "금액",
  vat: "부가세",
  days: "공사할 날",
  valid: "유효기간",
  crew: "인원",
  title: "항목",
  reason: "이유",
};

function showValue(field: string, value: AssistValue): string {
  if (value === null || value === "") return "비어 있음";
  if (Array.isArray(value)) return value.length ? value.join(" · ") : "없음";
  if (field === "vat") return VAT_MODE_LABEL[value] ?? value;
  if (field === "amount" && typeof value === "number") return value.toLocaleString("ko-KR") + "원";
  if (field === "crew") return value + "명";
  if (field === "days" && typeof value === "number") return value + "일";
  return String(value);
}

function proposal(field: CandidateField | Alternative, key: string): string {
  if (field.action === "clear") return "비우기";
  return showValue(key, field.value);
}

export function AssistBox({ hint, examples, form, resourceId, current, options, onApply, onInputIssues, readManualVersions }: {
  hint: string;
  examples: string[];
  form: AssistForm;
  resourceId: string;
  current: AssistCurrent;
  options: RuleOptions;
  /** 업체가 '폼에 적용'을 누른 뒤 선택·검증된 칸만 전달해요. */
  onApply: (changes: AssistCurrent, response: AssistResponse) => void;
  onInputIssues?: (issues: InputIssues) => void;
  readManualVersions?: () => Readonly<Record<string, number>>;
}) {
  const session = useMemo(() => new AssistSession(form, resourceId), [form, resourceId]);
  const slot = useContext(AssistSlot);
  const [view, setView] = useState(() => session.view);
  const [text, setText] = useState("");
  const latest = useRef({ current, options, onApply, readManualVersions });
  latest.current = { current, options, onApply, readManualVersions };
  useEffect(() => {
    const unsubscribe = session.subscribe(setView);
    return () => { unsubscribe(); session.dispose(); };
  }, [session]);

  function send() {
    if (session.send(text, latest.current.current, latest.current.options, onInputIssues,
      () => ({ current: latest.current.current, manualVersions: latest.current.readManualVersions?.() }))) setText("");
  }

  function apply() {
    const response = session.view.response;
    if (response) session.apply(latest.current.current, (changes) => latest.current.onApply(changes, response));
  }

  const response = view.response;
  const pending = view.phase === "loading";
  const actionable = response?.fields.some((field) =>
    field.status === "valid" && (field.action === "set" || field.action === "clear") ||
    field.status === "conflict" && field.alternatives.length > 0);

  const box = (
    <section className={s.box} aria-label="업체 도우미">
      <p className={s.head}><b>재재 · 업체 도우미</b><span>{hint}</span></p>
      {view.utterance ? (
        <div className={s.log} aria-live="polite">
          <p className={s.me}>{view.utterance}</p>
          {view.phase === "applied" ? <p className={s.bot}>선택한 {view.appliedFields.length}개 칸에 반영했어요. 제출은 아래 버튼을 직접 눌러 주세요.</p>
            : view.phase === "denied" ? <p className={s.bot}>요청을 처리하지 못했어요.</p>
              : <p className={s.bot}>{pending ? "AI 해석을 확인하고 있어요. 적용은 결과가 나온 뒤 할 수 있어요." : "변경할 칸을 고른 뒤 폼에 적용해 주세요."}</p>}
        </div>
      ) : null}
      {response && view.phase !== "applied" && view.phase !== "denied" ? (
        <div className={s.preview} aria-label="변경 후보">
          <p className={s.previewHead}><b>변경 후보</b><span>{response.source === "llm" ? "AI 해석" : "규칙 해석"}</span></p>
          {response.fields.map((field) => {
            if (field.action === "keep" && field.status === "valid") return null;
            const choice = view.choices[field.field];
            const valid = field.status === "valid" && (field.action === "set" || field.action === "clear");
            const conflict = field.status === "conflict";
            return (
              <div key={field.field} className={s.fieldRow}>
                <div className={s.fieldTitle}>
                  <b>{FIELD_LABEL[field.field] ?? field.field}</b>
                  <span>{conflict ? "해석이 달라요" : valid ? "변경 후보" : "확인 필요"}</span>
                </div>
                <p className={s.current}>현재: {showValue(field.field, current[field.field] ?? null)}</p>
                {valid ? (
                  <label className={s.checkLine}>
                    <input
                      type="checkbox"
                      checked={choice?.selected ?? false}
                      disabled={pending}
                      onChange={(event) => session.select(field.field, event.target.checked)}
                      aria-label={(FIELD_LABEL[field.field] ?? field.field) + " 적용 선택"}
                    />
                    <span>후보: <b>{proposal(field, field.field)}</b></span>
                  </label>
                ) : conflict ? (
                  <div className={s.alternatives} role="radiogroup" aria-label={(FIELD_LABEL[field.field] ?? field.field) + " 후보 고르기"}>
                    {field.alternatives.map((option, i) => (
                      <label className={s.checkLine} key={i}>
                        <input
                          type="radio"
                          name={"assist-" + form + "-" + field.field + "-" + response.request_id}
                          checked={choice?.alternative === i}
                          disabled={pending}
                          onChange={() => session.chooseAlternative(field.field, i)}
                        />
                        <span>{proposal(option, field.field)} <small>({option.source === "llm" ? "AI" : "규칙"})</small></span>
                      </label>
                    ))}
                    {choice?.alternative !== null && choice?.alternative !== undefined ? (
                      <label className={s.checkLine}>
                        <input type="checkbox" checked={choice.selected} disabled={pending}
                          onChange={(event) => session.select(field.field, event.target.checked)} />
                        <span>선택한 후보 적용</span>
                      </label>
                    ) : <span className={s.help}>적용할 값을 직접 골라 주세요.</span>}
                  </div>
                ) : <p className={s.help}>확실한 값을 찾지 못했어요. 기존 입력을 유지해요.</p>}
                {field.evidence && field.action !== "keep" ? <p className={s.evidence}>근거: {field.evidence}</p> : null}
              </div>
            );
          })}
          {!actionable ? <p className={s.help}>바꿀 수 있는 값이 없어요. 내용을 더 구체적으로 적어 주세요.</p> : null}
          {response.warnings.map((message, i) => <p key={i} className={s.warning} role="status">{message}</p>)}
          <div className={s.actions}>
            <Button size="sm" onClick={apply}
              disabled={pending || !actionable}>폼에 적용</Button>
            <Button size="sm" variant="secondary" onClick={() => session.cancel()}>취소</Button>
          </div>
        </div>
      ) : null}
      {view.phase === "applied" ? (
        <div className={s.actions}><Button variant="secondary" size="sm" onClick={() => session.cancel()}>후보 닫기</Button></div>
      ) : null}
      {view.error ? <p className={s.warning} role="alert">{view.error}</p> : null}
      <div className={s.row}>
        <textarea
          className={s.input}
          rows={2}
          value={text}
          maxLength={1000}
          placeholder="재재에게 말해 보세요"
          aria-label="업체 도우미에게 보낼 말"
          onChange={(event) => setText(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) {
              event.preventDefault();
              send();
            }
          }}
        />
        <Button size="sm" variant="secondary" onClick={send} disabled={pending || !text.trim()}>보내기</Button>
      </div>
      <div className={s.ex}>
        {examples.map((example) =>
          <button key={example} type="button" className={s.exBtn} onClick={() => setText(example)}>{example}</button>)}
      </div>
    </section>
  );
  if (slot.inDesk && !slot.el) return null;
  return slot.el ? createPortal(box, slot.el) : box;
}
