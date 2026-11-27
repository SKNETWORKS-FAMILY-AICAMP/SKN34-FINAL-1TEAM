import assert from "node:assert/strict";
import { test } from "node:test";
import { AssistSession } from "./session.ts";
import { formKeys, type AssistCurrent, type AssistForm, type AssistRequest, type CandidateField, type AssistValue, type RuleOptions } from "./protocol.ts";

const opts:RuleOptions={today:"2026-11-25",baseMonth:"2026-12-01",requiredDays:2,crewMax:6,daysMax:120,vatModes:["included","excluded","exempt"]};
const initial:Record<AssistForm,AssistCurrent>={
  period:{start:"2026-12-02",end:"2026-12-28",excluded:[]},
  dates:{days:["2026-12-09","2026-12-16"],crew:2},
  quote:{amount:3200000,days:4,valid:"2026-12-29",vat:"included"},
  extra:{title:"기존 작업",reason:"기존 사유",amount:300000,vat:"included"}
};
function candidate(r:AssistRequest,k:string,action:"set"|"keep"|"uncertain",evidence="",value:AssistValue=null):CandidateField {
  return {field:k,action,evidence,value,source:"llm",status:action==="uncertain"?"uncertain":"valid",alternatives:[],
    origin_request_id:r.request_id,intent_kind:action==="set"?"explicit_change":action==="uncertain"?"ambiguous":evidence?"retain_current":"no_mention",
    ...(action==="set"?{value_source:"utterance" as const}:action==="keep"&&evidence?{value_source:"current" as const}:{})};
}
function fixture(form:AssistForm) {
  const current=structuredClone(initial[form]),holds:Record<string,string>={},versions:Record<string,number>={};
  let pending=false,calls=0,writes=0;
  const queue:{r:AssistRequest,resolve:(v:unknown)=>void}[]=[];
  const s=new AssistSession(form,"00000000-0000-4000-8000-000000000179",r=>new Promise(resolve=>{calls++;queue.push({r,resolve});}));
  function start(text:string){
    assert(s.send(text,current,opts,d=>{
      if(typeof d.pending==="boolean")pending=d.pending;
      for(const [k,v] of Object.entries(d.fieldHolds??{})){if(typeof v==="string")holds[k]=v;else if(v===null)delete holds[k];}
    },()=>({current:structuredClone(current),manualVersions:{...versions}})));
    assert(pending);assert.equal(s.apply(current,()=>assert.fail("pending write")),false);
    return queue.at(-1)!;
  }
  async function reply(q:ReturnType<typeof start>,fields:CandidateField[]){
    const before=structuredClone(current),count=writes;
    q.resolve({form,request_id:q.r.request_id,source:"llm",warnings:[],fields:formKeys(form).map(k=>fields.find(f=>f.field===k)??candidate(q.r,k,"keep"))});
    await Promise.resolve();await Promise.resolve();
    assert.equal(s.view.phase,"ready");assert(!pending);assert.deepEqual(current,before);assert.equal(writes,count);
  }
  async function send(text:string,produce:(r:AssistRequest)=>CandidateField[]){
    const q=start(text);await reply(q,produce(q.r));return q.r;
  }
  function apply(expected:AssistCurrent){
    for(const k of formKeys(form))s.select(k,Object.hasOwn(expected,k));
    let patch:AssistCurrent={};const n=writes;
    assert.equal(s.apply(current,v=>{writes++;patch=structuredClone(v);Object.assign(current,v);}),Object.keys(expected).length>0);
    assert.deepEqual(patch,expected);assert.equal(writes,n+Number(Object.keys(expected).length>0));
  }
  function manual(k:string,v:AssistValue){current[k]=structuredClone(v);versions[k]=(versions[k]??0)+1;delete holds[k];}
  return {s,current,holds,start,reply,send,apply,manual,get calls(){return calls;}};
}
const dates=["2026-12-06","2026-12-14","2026-12-19"];
const exclusion="12월 6일, 14일, 19일은 시설 점검이라 세 날짜를 제외일로 등록해 주세요.";
test("canonical exclusion owns only its field; prior unresolved start survives",async()=>{
  const h=fixture("period");try{
    await h.send("착공일은 아직 미정입니다.",r=>[candidate(r,"start","uncertain",r.text)]);
    await h.send(exclusion,r=>[candidate(r,"excluded","set",r.text,dates)]);
    assert(h.holds.start);assert(!h.holds.end);assert(h.holds.excluded);
    h.apply({excluded:dates});assert(h.holds.start);assert(!h.holds.excluded);assert.equal(h.calls,2);
  }finally{h.s.dispose();}
});
test("canonical exclusion does not generate endpoint holds on an otherwise fresh form",async()=>{
  const h=fixture("period");try{
    await h.send(exclusion,r=>[candidate(r,"excluded","set",r.text,dates)]);
    assert.deepEqual(Object.keys(h.holds).sort(),["excluded"]);
    h.apply({excluded:dates});assert.deepEqual(h.holds,{});
  }finally{h.s.dispose();}
});
for(const [name,text,evidence,value] of [
  ["wrong-list",exclusion,exclusion,["2026-12-06","2026-12-14","2026-12-20"]],
  ["forged-evidence",exclusion,"12월 8일을 제외일로 등록해 주세요.",["2026-12-08"]],
  ["conditional","승인이 나면 12월 6일과 14일을 제외일로 등록해 주세요.","12월 6일과 14일을 제외일로 등록해 주세요.",dates.slice(0,2)],
  ["wrong-count","12월 6일, 14일, 19일 중 두 날짜를 제외일로 등록해 주세요.","12월 6일, 14일, 19일 중 두 날짜를 제외일로 등록해 주세요.",dates.slice(0,2)],
  ["negative","12월 6일과 14일을 제외일로 등록하지 마세요.","12월 6일과 14일을 제외일로 등록",dates.slice(0,2)]
] as const){
  test("canonical exclusion risk: "+name,async()=>{
    const h=fixture("period");try{
      await h.send(text,r=>[candidate(r,"excluded","set",evidence,[...value])]);
      h.apply({});assert.deepEqual(h.current,initial.period);
    }finally{h.s.dispose();}
  });
}
test("valid current retention releases its hold, not an independent unresolved field",async()=>{
  const h=fixture("dates");try{
    await h.send("작업 인원과 공사 날짜가 아직 미정입니다.",r=>[
      candidate(r,"crew","uncertain",r.text),candidate(r,"days","uncertain",r.text)]);
    await h.send("기존에 저장된 인원을 그대로 유지해 주세요.",r=>[candidate(r,"crew","keep","기존에 저장된 인원을 그대로 유지")]);
    assert(!h.holds.crew);assert(h.holds.days);h.apply({});assert.deepEqual(h.current,initial.dates);
  }finally{h.s.dispose();}
});
for(const [name,text,evidence] of [
  ["negated","현재 인원을 그대로 유지하지 마세요.","현재 인원을 그대로 유지"],
  ["conditional","승인이 나면 현재 인원을 그대로 유지해 주세요.","현재 인원을 그대로 유지"],
  ["wrong-owner","현재 일정을 그대로 유지해 주세요.","현재 일정을 그대로 유지"],
  ["invented","인원 변경은 아직 미정입니다.","현재 인원을 그대로 유지"]
] as const){
  test("retention hold must not release: "+name,async()=>{
    const h=fixture("dates");try{
      await h.send("작업 인원은 아직 미정입니다.",r=>[candidate(r,"crew","uncertain",r.text)]);
      await h.send(text,r=>[candidate(r,"crew","keep",evidence)]);
      assert(h.holds.crew);h.apply({});assert.deepEqual(h.current,initial.dates);
    }finally{h.s.dispose();}
  });
}
test("fresh crew offer cannot overwrite a manual edit after Send",async()=>{
  const h=fixture("dates");try{
    const q=h.start("작업 인원을 5명으로 변경해 주세요.");
    h.manual("crew",3);
    await h.reply(q,[candidate(q.r,"crew","set",q.r.text,5)]);
    h.apply({});assert.equal(h.current.crew,3);
  }finally{h.s.dispose();}
});
test("canonical VAT value needs an owned non-competing literal",async()=>{
  const h=fixture("quote");try{
    await h.send("부가세 별도로 청구해 주세요.",r=>[candidate(r,"vat","set","부가세 별도로","excluded")]);
    h.apply({vat:"excluded"});
  }finally{h.s.dispose();}
});
for(const [name,text,evidence] of [
  ["wrong-owner","별도 작업 비용을 확인해 주세요.","별도 작업 비용"],
  ["wrong-value","부가세는 포함으로 발행해 주세요.","부가세는 포함"],
  ["review-only","부가세 별도라는 후보를 검토해 주세요.","부가세 별도"],
  ["negative","부가세 별도라는 후보를 만들지 마세요.","부가세 별도"]
] as const){
  test("canonical VAT risk: "+name,async()=>{
    const h=fixture("quote");try{
      await h.send(text,r=>[candidate(r,"vat","set",evidence,"excluded")]);
      h.apply({});assert.deepEqual(h.current,initial.quote);
    }finally{h.s.dispose();}
  });
}
