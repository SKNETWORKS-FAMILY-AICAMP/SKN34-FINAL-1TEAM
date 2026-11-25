// R6 실제 실행된 20턴의 범위/유지 회귀와 독립 대조 사례. 서버는 스텁.
import assert from "node:assert/strict";
import { test } from "node:test";
import { AssistSession } from "./session.ts";
import { formKeys, type AssistForm, type AssistCurrent, type AssistRequest, type CandidateField } from "./protocol.ts";
import { emptyFormHolds, mergeFormHolds, manuallyResolveHolds } from "./formHolds.ts";
import { requestIntent } from "./requestIntent.ts";

const opts = { today: "2026-11-25", baseMonth: "2026-12-01", daysMax: 365, crewMax: 6, vatModes: ["included","excluded","exempt"] };
const period = { start: "2026-12-02", end: "2026-12-24", excluded: [] };
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
const seed = "실측은 12월 5일부터 22일까지 접수하는 걸로 우선 제시합니다.";
const endEvidence = "12월 5일부터 22일까지";
const startEvidence = "12월 5일부터";
async function seededPeriod() {
  const f = fixture("period", period);
  const req = f.send(seed);
  await f.resolve({start:set("2026-12-05",startEvidence),end:set("2026-12-22",endEvidence)});
  return {f,req};
}
for (const withdrawal of ["없던 일로 해주세요", "없었던 것으로 하겠습니다", "없던 걸로 해주세요"]) {
  test("독립 취소 및 날짜 참조 유지: "+withdrawal,async()=>{
    const {f,req}=await seededPeriod();
    const start="시작일 수정은 "+withdrawal;
    const end="종료일은 방금 제시한 날을 쓰겠습니다";
    f.send(start+". "+end+".");
    await f.resolve({start:keep(start),end:carry("2026-12-22",endEvidence,req.request_id)});
    f.apply({end:"2026-12-22"});
    assert.equal(f.current.start,period.start);
    assert.deepEqual(f.holds.fields,{});
  });
}
for (const text of ["시작일 수정은 없던 일로 할까요?", "시작일 수정은 없던 일로 하지 마세요", "시작일 수정은 없던 일로"]) {
  test("미확정 취소는 보류를 해제하지 않음: "+text,async()=>{
    const {f}=await seededPeriod();
    f.send(text);
    await f.resolve({start:keep(text)});
    assert.ok(f.holds.fields.start);
    f.apply({});
  });
}
for (const text of ["종료일은 방금 제시한 날을 쓰지 마세요","종료일은 방금 제시한 날을 안 쓰겠습니다","종료일은 방금 제시한 날을 사용할까요?"]) {
  test("위험한 이전값 사용 요청: "+text,async()=>{
    const {f,req}=await seededPeriod();
    assert.ok(requestIntent("period",text).blocked.has("end"));
    f.send(text);
    await f.resolve({end:carry("2026-12-22",endEvidence,req.request_id)});
    f.apply({});
    assert.equal(f.current.end,period.end);
    assert.ok(f.holds.fields.end);
  });
}
test("수동 종료일 변경 후 이전 후보를 다시 적용하지 않음",async()=>{
  const {f,req}=await seededPeriod();
  f.manual("end","2026-12-27");
  const start="시작일 수정은 없던 일로 해주세요";
  f.send(start+". 종료일은 방금 제시한 날을 쓰겠습니다.");
  await f.resolve({start:keep(start),end:carry("2026-12-22",endEvidence,req.request_id)});
  f.apply({});
  assert.equal(f.current.end,"2026-12-27");
});
test("출처가 다른 이전 후보를 적용하지 않음",async()=>{
  const {f}=await seededPeriod();
  const start="시작일 수정은 없던 일로 해주세요";
  f.send(start+". 종료일은 방금 제시한 날을 쓰겠습니다.");
  await f.resolve({start:keep(start),end:carry("2026-12-22",endEvidence,"invalid-origin")});
  f.apply({});
  assert.equal(f.current.end,period.end);
});
test("불완전한 전체 철회 표현을 확정 취소로 해석하지 않음",()=>{
  for (const text of ["없던 일로","없었던 것으로","없던 걸로"]) assert.equal(requestIntent("period",text).cancelAll,false);
});
