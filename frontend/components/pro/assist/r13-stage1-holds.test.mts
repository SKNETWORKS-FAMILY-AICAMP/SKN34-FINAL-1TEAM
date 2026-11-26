import assert from "node:assert/strict";
import { test } from "node:test";
import { activeHold, reduceFieldHold } from "./holdTransition.ts";
import { AssistSession } from "./session.ts";
import { formKeys, type AssistCurrent, type AssistForm, type AssistRequest, type CandidateField, type RuleOptions } from "./protocol.ts";

test("no mention dismisses provisional only; settled obligation survives",()=>{
  const fresh=reduceFieldHold({}, {kind:"begin",message:"new"});
  assert.equal(activeHold(fresh.state),"new");
  assert.equal(fresh.delta,"new");
  const dropped=reduceFieldHold(fresh.state,{kind:"settle",outcome:{kind:"no-mention",dismissProvisional:true}});
  assert.deepEqual(dropped,{state:{},delta:null});
  const prior=reduceFieldHold({settled:"old"},{kind:"begin",message:"new"});
  assert.equal(prior.state.settled,"old");
  const kept=reduceFieldHold(prior.state,{kind:"settle",outcome:{kind:"no-mention",dismissProvisional:true}});
  assert.equal(activeHold(kept.state),"new");
  assert.equal(kept.state.provisional,undefined);
  assert.equal(kept.delta,undefined);
});
test("ambiguity and cancellation preserve obligations; explicit resolve/manual/apply are distinct",()=>{
  const begun=reduceFieldHold({}, {kind:"begin",message:"pending"}).state;
  for (const event of [{kind:"cancel"} as const,{kind:"settle",outcome:{kind:"preserve"}} as const]) {
    const stopped=reduceFieldHold(begun,event);
    assert.equal(stopped.state.settled,"pending");
    assert.equal(stopped.state.provisional,undefined);
    assert.equal(stopped.delta,undefined);
  }
  for (const kind of ["verified-current","unchanged","withdrawn"] as const) {
    assert.deepEqual(reduceFieldHold(begun,{kind:"settle",outcome:{kind}}),{state:{},delta:null});
  }
  assert.deepEqual(reduceFieldHold(begun,{kind:"manual"}),{state:{}});
  assert.deepEqual(reduceFieldHold(begun,{kind:"apply"}),{state:{},delta:null});
});
test("new actionable review is held even without prior reservation",()=>{
  const result=reduceFieldHold({}, {kind:"settle",outcome:{kind:"review",message:"check"}});
  assert.equal(result.state.settled,"check");
  assert.equal(result.delta,"check");
});
const opts:RuleOptions={today:"2026-11-25",baseMonth:"2026-12-01",requiredDays:2,crewMax:6,daysMax:120,vatModes:["included","excluded","exempt"]};
function fixture(form:AssistForm,current:AssistCurrent){
  const holds:Record<string,string>={}; let pending=false;
  let job:{r:AssistRequest,resolve:(v:unknown)=>void,reject:(v:unknown)=>void};
  const s=new AssistSession(form,"00000000-0000-4000-8000-000000000179",r=>new Promise((resolve,reject)=>job={r,resolve,reject}));
  function field(r:AssistRequest,k:string,detail:Partial<CandidateField>={}):CandidateField{
    return {field:k,action:"keep",value:null,evidence:"",source:"llm",status:"valid",alternatives:[],
      origin_request_id:r.request_id,intent_kind:"no_mention",value_source:null,...detail};
  }
  async function send(text:string,produce:(r:AssistRequest)=>CandidateField[]){
    assert(s.send(text,current,opts,d=>{
      pending=d.pending??pending;
      for(const [k,v] of Object.entries(d.fieldHolds??{})){if(v===null)delete holds[k];else if(typeof v==="string")holds[k]=v;}
    },()=>({current:structuredClone(current),manualVersions:{}})));
    assert(pending);assert(!s.apply(current,()=>assert.fail("pending Apply")));
    const r=job!.r, fields=produce(r);
    job!.resolve({form,request_id:r.request_id,source:"llm",warnings:[],fields:formKeys(form).map(k=>fields.find(f=>f.field===k)??field(r,k))});
    await new Promise<void>(resolve=>setImmediate(resolve));
    assert.equal(s.view.phase,"ready");assert(!pending);
    return r;
  }
  const ambiguous=(r:AssistRequest,k:string)=>field(r,k,{action:"uncertain",status:"uncertain",intent_kind:"ambiguous",evidence:r.text});
  const keep=(r:AssistRequest,k:string,evidence=r.text)=>field(r,k,{intent_kind:"retain_current",value_source:"current",evidence});
  return {s,holds,send,field,ambiguous,keep};
}
const quote={amount:3000000,vat:"included",days:4,valid:"2026-12-29"};
for(const [mode,expected] of [["포함",false],["별도",true]] as const){
  test("current literal VAT must match actual value: "+mode,async()=>{
    const h=fixture("quote",structuredClone(quote));try{
      await h.send("부가세 조건은 아직 미정입니다.",r=>[h.ambiguous(r,"vat")]);
      const text="부가세는 변경하지 말고 기존처럼 "+mode+"으로 유지하세요.";
      await h.send(text,r=>[h.keep(r,"vat")]);
      assert.equal(!!h.holds.vat,expected);
    }finally{h.s.dispose();}
  });
}
test("bare no-change request cannot erase earlier unresolved candidate",async()=>{
  const h=fixture("quote",structuredClone(quote));try{
    await h.send("금액은 아직 미정입니다.",r=>[h.ambiguous(r,"amount")]);
    await h.send("금액 수정은 하지 말아 주세요.",r=>[h.keep(r,"amount")]);
    assert(h.holds.amount);
  }finally{h.s.dispose();}
});
for(const [name,text,evidence] of [
  ["negated keep","금액은 그대로 유지하지 마세요.","금액은 그대로 유지"],
  ["conditional","승인이 나면 금액 수정은 하지 말아 주세요.","금액 수정은 하지 말아 주세요."],
  ["business","금액 관련 계약은 변경하지 말아 주세요.","금액 관련 계약은 변경하지 말아 주세요."],
  ["quoted","제목은 \"금액 수정은 하지 말아 주세요\"로 정합니다.","금액 수정은 하지 말아 주세요"],
] as const){
  test("retain current cannot erase unresolved obligation: "+name,async()=>{
    const h=fixture("extra",{title:"기존 작업",reason:"기존 사유",amount:300000,vat:"included"});try{
      await h.send("금액은 아직 미정입니다.",r=>[h.ambiguous(r,"amount")]);
      await h.send(text,r=>[h.keep(r,"amount",evidence)]);
      assert(h.holds.amount);
    }finally{h.s.dispose();}
  });
}
test("a stale sibling boundary origin cannot dismiss the other endpoint reservation",async()=>{
  const h=fixture("period",{start:"2026-12-02",end:"2026-12-28",excluded:[]});try{
    const text="완공 날짜를 12월 29일로 변경해 주세요.";
    await h.send(text,r=>[h.field(r,"end",{action:"set",value:"2026-12-29",evidence:text,intent_kind:"explicit_change",value_source:"utterance",origin_request_id:"stale"})]);
    assert(h.holds.start);
  }finally{h.s.dispose();}
});
