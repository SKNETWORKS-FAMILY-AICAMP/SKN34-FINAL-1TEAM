// R6 실제 실행된 20턴의 범위/유지 회귀와 독립 대조 사례. 서버는 스텁.
import assert from "node:assert/strict";
import { test } from "node:test";
import { AssistSession } from "./session.ts";
import { formKeys, type AssistForm, type AssistCurrent, type AssistRequest, type CandidateField } from "./protocol.ts";
import { emptyFormHolds, mergeFormHolds, manuallyResolveHolds } from "./formHolds.ts";
import { requestIntent, scopedFieldParts } from "./requestIntent.ts";

const opts = { today: "2026-11-25", baseMonth: "2026-12-01", daysMax: 365, crewMax: 6, vatModes: ["included","excluded","exempt"] };
const period = { start: "2026-12-02", end: "2026-12-24", excluded: [] };
const quote = { amount: 4550000, days: 3, vat: "included", valid: "2026-12-17" };
type Fields = Record<string, Partial<CandidateField>>;

function fixture(form: AssistForm, initial: AssistCurrent) {
  let current = { ...initial }, holds = emptyFormHolds();
  const manualVersions: Record<string, number> = {};
  let job: { request: AssistRequest; resolve: (v: unknown) => void; reject: (e: Error) => void } | undefined;
  const session = new AssistSession(form, "r6-live20", request =>
    new Promise((resolve, reject) => { job = { request, resolve, reject }; }));
  function send(text: string) {
    assert(session.send(text,current,opts,issues => { holds = mergeFormHolds(form,holds,issues); }, () => ({ current, manualVersions })));
    assert(job);
    assert(holds.pending);
    assert.equal(session.apply(current, () => assert.fail("응답 전 적용")), false);
    return job.request;
  }
  async function resolve(fields: Fields) {
    assert(job);
    const req = job.request;
    job.resolve({ form, request_id:req.request_id, source:"llm", warnings:[],
      fields:formKeys(form).map(field => ({ field, status:"valid", action:"keep", value:null,
        evidence:"", source:"rule", alternatives:[], origin_request_id:req.request_id,
        intent_kind:"no_mention", value_source:null, ...fields[field] })) });
    await Promise.resolve();
    await Promise.resolve();
    assert.equal(session.view.phase,"ready");
    assert.equal(holds.pending,false);
  }
  async function offline() {
    assert(job);
    job.reject(new Error("offline"));
    await Promise.resolve();
    await Promise.resolve();
    assert.equal(session.view.phase,"ready");
  }
  function apply(expected: AssistCurrent) {
    let patch: AssistCurrent = {};
    assert.equal(session.apply(current, v => { patch = v; current = {...current,...v}; }), Object.keys(expected).length > 0);
    assert.deepEqual(patch,expected);
  }
  function manual(field:string, value: AssistCurrent[string]) {
    current={...current,[field]:value};
    manualVersions[field]=(manualVersions[field]??0)+1;
    holds=manuallyResolveHolds(holds,[field]);
  }
  return { session,send,resolve,offline,apply,manual,get holds(){return holds;},get current(){return current;} };
}
function set(value: AssistCurrent[string], evidence: string): Partial<CandidateField> {
  return { action:"set",value,evidence,intent_kind:"explicit_change",value_source:"utterance",source:"llm" };
}
function keep(evidence: string): Partial<CandidateField> {
  return { action:"keep",value:null,evidence,intent_kind:"retain_current",value_source:"current" };
}
function carry(value: AssistCurrent[string], evidence: string, origin: string): Partial<CandidateField> {
  return { ...set(value,evidence), intent_kind:"retain_previous",value_source:"previous",origin_request_id:origin };
}
const unavailableQuestion = "방문을 12월 14일에 못 받는 걸로 할까요?";
const lastDay = "마지막 실측 가능일은 12월 29일로 확정합니다.";
const exclusions = ["2026-12-08","2026-12-15","2026-12-23"];

for (const wrongExcluded of [false,true]) test("R6 period-02: 제외일 문의와 종료일 확정의 독립 보류 "+wrongExcluded,async()=>{
  const text=unavailableQuestion+" "+lastDay;
  const f=fixture("period",period);
  const intent=requestIntent("period",text);
  assert.deepEqual([...intent.requested].sort(),["end","excluded"]);
  assert.deepEqual([...intent.blocked],["excluded"]);
  f.send(text);
  assert.deepEqual(Object.keys(f.holds.fields).sort(),["end","excluded"]);
  await f.resolve({end:set("2026-12-29",lastDay.slice(0,-1)),
    excluded:wrongExcluded?set(["2026-12-14"],unavailableQuestion):{status:"uncertain",action:"uncertain",value:null,evidence:unavailableQuestion,intent_kind:"ambiguous",reason_code:"ambiguous_intent"}});
  f.apply({end:"2026-12-29"});
  assert.deepEqual(Object.keys(f.holds.fields),["excluded"]);
  assert.equal(f.current.start,period.start);
});

for (const text of [
  "12월 8일하고 15일, 23일은 실측 못 받는 날로 빼주시겠어요?",
  "12월 8일, 15일, 23일은 방문 못 받는 날로 빼 주세요.",
  "12월 8일과 15일, 23일은 방문 제외일로 확정합니다.",
]) test("R6 period-05: 앞쪽 날짜까지 제외 목록에 귀속: "+text,async()=>{
  const f=fixture("period",period);
  const intent=requestIntent("period",text);
  assert.deepEqual([...intent.requested],["excluded"]);
  assert.equal(intent.blocked.size,0);
  f.send(text);
  assert.deepEqual(Object.keys(f.holds.fields),["excluded"]);
  await f.resolve({excluded:set(exclusions,text)});
  f.apply({excluded:exclusions});
  assert.deepEqual(f.holds.fields,{});
});

test("제외 목록 문의는 막되 경계 보류를 만들지 않아요",async()=>{
  const f=fixture("period",period);
  const text="12월 8일, 15일, 23일은 방문 못 받는 날로 할까요?";
  f.send(text);
  await f.resolve({excluded:set(exclusions,text)});
  f.apply({});
  assert.deepEqual(Object.keys(f.holds.fields),["excluded"]);
});

test("별도 기간 범위 뒤 제외 문의 및 완전한 기간의 불가 요청을 구분해요",()=>{
  const mixed="12월 8일부터 22일까지 가능하고 15일은 못 받는 걸로 할까요?";
  const intent=requestIntent("period",mixed);
  assert.deepEqual([...intent.requested].sort(),["end","excluded","start"]);
  assert.deepEqual([...intent.blocked],["excluded"]);
  assert.equal(scopedFieldParts("period",mixed,"excluded").length,1);
  const unavailable="12월 8일부터 22일까지 방문을 못 받습니다.";
  assert.deepEqual([...requestIntent("period",unavailable).requested],["excluded"]);
  const independent="12월 8일은 가능합니다, 15일은 못 받습니다.";
  assert.deepEqual([...requestIntent("period",independent).requested].sort(),["end","excluded","start"]);
});

test("제외 요청 서버 실패는 경계까지 보류하지 않아요",async()=>{
  const f=fixture("period",period);
  f.send("12월 8일하고 15일, 23일은 실측 못 받는 날로 빼주시겠어요?");
  await f.offline();
  f.apply({});
  assert.deepEqual(Object.keys(f.holds.fields),["excluded"]);
});

const seed="우선 견적서 만료 날짜는 1월 6일, 공사 기간은 11일로 후보 주세요.";
const validityEvidence="견적서 만료 날짜는 1월 6일";
const untouched="공사 기간 손대지 않을게요";
const restore="견적 효력만 직전에 잡은 날짜로 반영해 주세요.";

for (const mode of ["verified","wrong-origin","question","manual"] as const) test("R6 quote-06: 현재 일수 유지와 검증된 만료일 계승 "+mode,async()=>{
  const f=fixture("quote",quote);
  const first=f.send(seed);
  await f.resolve({days:set(11,"공사 기간은 11일"),valid:{...set("2027-01-06",validityEvidence),value_source:"derived"}});
  assert.deepEqual(Object.keys(f.holds.fields).sort(),["days","valid"]);
  if(mode==="manual") f.manual("valid","2026-12-28");
  const keepText=mode==="question"?"공사 기간 손대지 않을까요?":untouched+".";
  const next=f.send(keepText+" "+restore);
  if(mode==="manual") assert(!next.previous_candidate?.fields.some(x=>x.field==="valid"));
  else assert.equal(next.previous_candidate?.origins?.valid,first.request_id);
  await f.resolve({days:keep(keepText.replace(/[.?]$/,"")),valid:carry("2027-01-06",validityEvidence,mode==="wrong-origin"?next.request_id:first.request_id)});
  const valid=mode==="verified"||mode==="question";
  f.apply(valid?{valid:"2027-01-06"}:{});
  assert.equal(f.current.days,3);
  assert.equal(!!f.holds.fields.days,mode==="question");
  if (mode==="verified") assert.deepEqual(f.holds.fields,{});
  if (mode==="wrong-origin") assert.ok(f.holds.fields.valid);
  if (mode==="manual") assert.equal(f.current.valid,"2026-12-28");
});
