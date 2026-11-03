"use client";
// 비밀번호 찾기 → 새 비밀번호 정하기 → 바꿈(시안 WLogin '비밀번호 찾기' · WResetPw). 화면만 있다.
// 메일 발송 · 토큰은 나중 작업: 지금은 API를 부르지 않고 화면 상태만 넘긴다.
// 메일의 링크(/reset-password?token=…)로 열면 새 비밀번호 정하기부터 보인다. 토큰 확인 · 만료 안내도 그때 붙인다.
import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import { FieldError, Req } from "./parts";
import { PasswordInput } from "./PasswordInput";
import s from "./auth.module.css";

type Step = "find" | "sent" | "new" | "done";
const TITLE: Record<Step, string> = { find: "비밀번호 찾기", sent: "비밀번호 찾기", new: "새 비밀번호를 정해 주세요", done: "새 비밀번호를 정해 주세요" };

function Check() {
  return <svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true"><path d="M3 8.5l3.2 3.2L13 4.5" /></svg>;
}

export function ResetPassword({ fromMail }: { fromMail: boolean }) {
  const [step, setStep] = useState<Step>(fromMail ? "new" : "find");
  const [email, setEmail] = useState("");
  const [errors, setErrors] = useState<{ email?: string; pw?: string; pw2?: string }>({});
  const h1 = useRef<HTMLHeadingElement>(null);
  const status = useRef<HTMLDivElement>(null);
  const moved = useRef(false);

  // 상태가 바뀌면 보낸 뒤 · 바꾼 뒤 안내나 제목으로 초점을 옮긴다(처음 열 때는 그대로).
  useEffect(() => {
    if (moved.current) (step === "sent" || step === "done" ? status.current : h1.current)?.focus();
    moved.current = true;
  }, [step]);

  function onFind(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const v = String(new FormData(e.currentTarget).get("email") ?? "").trim();
    if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(v)) { setErrors({ email: "이메일 형식을 확인해 주세요. 예: name@example.com" }); document.getElementById("pf-email")?.focus(); return; }
    setErrors({});
    setEmail(v);
    setStep("sent");
  }

  function onNew(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const f = new FormData(e.currentTarget);
    const pw = String(f.get("password") ?? "");
    const pw2 = String(f.get("password2") ?? "");
    const err: typeof errors = {};
    if (pw.length < 8) err.pw = "비밀번호를 8자 이상으로 적어 주세요.";
    else if (pw2 !== pw) err.pw2 = "비밀번호가 서로 달라요.";
    setErrors(err);
    if (err.pw || err.pw2) { document.getElementById(err.pw ? "rp-pw" : "rp-pw2")?.focus(); return; }
    setStep("done");
  }

  return (
    <div className={s.single}>
      <div className={s.head}>
        <h1 ref={h1} tabIndex={-1} className="mj-h1">{TITLE[step]}</h1>
        {step === "find" ? <p className="mj-sub">가입한 이메일로 비밀번호를 다시 정하는 링크를 보내요.</p> : null}
        {step === "new" ? <p className="mj-sub">{email ? <>가입한 이메일 <span className="mj-num">{email}</span>의 비밀번호를 바꿔요.</> : "가입한 이메일의 비밀번호를 바꿔요."}</p> : null}
      </div>
      <p className={`mj-notice ${s.notice}`}>비밀번호 찾기는 준비 중이에요. 지금은 메일이 가지 않고 화면만 볼 수 있어요.</p>

      {step === "find" ? (
        <form className={s.form} onSubmit={onFind} noValidate aria-label="비밀번호 재설정 링크 받기">
          <div className="mj-field">
            <label className="mj-label" htmlFor="pf-email">가입한 이메일</label>
            <input id="pf-email" name="email" type="email" autoComplete="email" className="mj-input" maxLength={254} defaultValue={email} aria-invalid={errors.email ? true : undefined} aria-describedby={errors.email ? "pf-email-e" : undefined} />
            {errors.email ? <FieldError id="pf-email-e">{errors.email}</FieldError> : null}
          </div>
          <button type="submit" className={`mj-btn mj-btn-pri ${s.submit}`}>재설정 링크 보내기</button>
        </form>
      ) : null}

      {step === "sent" ? (
        <div ref={status} tabIndex={-1} className={s.done} role="status">
          <p className={`mj-st mj-st-ok ${s.okLine}`}><Check />재설정 링크를 보냈어요</p>
          <p>가입한 이메일이면 메일이 가요. 링크는 30분 동안 열 수 있어요.</p>
          <p className="mj-small">메일의 링크를 누르면 새 비밀번호를 정하는 화면이 열려요.</p>
          {/* 메일 대신: 메일 발송이 붙기 전까지 다음 화면으로 넘어가는 버튼 */}
          <div className="mj-btn-row">
            <button type="button" className="mj-btn mj-btn-sec" onClick={() => setStep("new")}>새 비밀번호 정하기</button>
            <button type="button" className="mj-btn mj-btn-ghost" onClick={() => setStep("find")}>다른 이메일로 보내기</button>
          </div>
        </div>
      ) : null}

      {step === "new" ? (
        <form className={s.form} onSubmit={onNew} noValidate aria-label="새 비밀번호 정하기">
          <div className="mj-field">
            <label className="mj-label" htmlFor="rp-pw">새 비밀번호 <Req /></label>
            <PasswordInput id="rp-pw" name="password" autoComplete="new-password" required invalid={!!errors.pw} describedBy={errors.pw ? "rp-pw-h rp-pw-e" : "rp-pw-h"} />
            <p id="rp-pw-h" className="mj-hint">8자 이상</p>
            {errors.pw ? <FieldError id="rp-pw-e">{errors.pw}</FieldError> : null}
          </div>
          <div className="mj-field">
            <label className="mj-label" htmlFor="rp-pw2">비밀번호 확인 <Req /></label>
            <PasswordInput id="rp-pw2" name="password2" autoComplete="new-password" toggleLabel="비밀번호 확인 보기" required invalid={!!errors.pw2} describedBy={errors.pw2 ? "rp-pw2-e" : undefined} />
            {errors.pw2 ? <FieldError id="rp-pw2-e">{errors.pw2}</FieldError> : null}
          </div>
          <button type="submit" className={`mj-btn mj-btn-pri ${s.submit}`}>비밀번호 바꾸기</button>
        </form>
      ) : null}

      {step === "done" ? (
        <div ref={status} tabIndex={-1} className={s.done} role="status">
          <p className={`mj-st mj-st-ok ${s.okLine}`}><Check />비밀번호를 바꿨어요. 새 비밀번호로 로그인해 주세요.</p>
          <div><Link className="mj-btn mj-btn-pri" href="/login">로그인하기</Link></div>
        </div>
      ) : null}

      {step !== "done" ? <p><Link className={`${s.plain} ${s.plainSub}`} href="/login">로그인으로 돌아가기</Link></p> : null}
    </div>
  );
}
