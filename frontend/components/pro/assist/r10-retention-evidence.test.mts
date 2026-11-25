// R10: actual session contracts for a complete withdrawal plus current retention.
// Authored controls and the already-called failure only; no independent heldout input.
import assert from "node:assert/strict";
import { test } from "node:test";
import { AssistSession } from "./session.ts";
import { requestIntent } from "./requestIntent.ts";
import { formKeys, type AssistCurrent, type AssistForm, type AssistRequest, type CandidateField, type AssistValue, type RuleOptions } from "./protocol.ts";

const options:RuleOptions={today:"2026-11-25",baseMonth:"2027-01-01",requiredDays:2,daysMax:365,crewMax:6,vatModes:["included","excluded","exempt"]};
const initial:Record<AssistForm,AssistCurrent>={
  quote:{amount:3260000,days:4,valid:"2027-01-27",vat:"included"},
  period:{start:"2027-01-06",end:"2027-01-29",excluded:["2027-01-14","2027-01-22"]},
  dates:{days:["2027-01-12","2027-01-19"],crew:2},
  extra:{title:"기존 보강",reason:"기존 손상",amount:320000,vat:"included"},
};
function field(r:AssistRequest,k:string,action:"keep"|"uncertain"|"set",evidence:string,value:AssistValue=null):CandidateField {
  return {field:k,action,value,evidence,source:"llm",status:action==="uncertain"?"uncertain":"valid",alternatives:[],
    origin_request_id:r.request_id,intent_kind:action==="set"?"explicit_change":action==="uncertain"?"ambiguous":evidence?"retain_current":"no_mention",
    ...(action==="set"?{value_source:"utterance" as const}:action==="keep"&&evidence?{value_source:"current" as const}:{})};
}
function fixture(form:AssistForm) {
  const current=structuredClone(initial[form]), holds:Record<string,string>={}, versions:Record<string,number>={};
  let pending=false, calls=0, writes=0;
  const queue:{r:AssistRequest,resolve:(v:unknown)=>void,reject:(v:unknown)=>void}[]=[];
  const s=new AssistSession(form,"00000000-0000-4000-8000-000000000179",r=>new Promise((resolve,reject)=>{calls++;queue.push({r,resolve,reject});}));
  function start(text:string) {
    assert(s.send(text,current,options,d=>{
      if(typeof d.pending==="boolean")pending=d.pending;
      for(const [k,v] of Object.entries(d.fieldHolds??{})){if(typeof v==="string")holds[k]=v;else if(v===null)delete holds[k];}
    },()=>({current:structuredClone(current),manualVersions:{...versions}})));
    assert(pending);assert.equal(s.apply(current,()=>assert.fail("pending write")),false);
    return queue.at(-1)!;
  }
  async function reply(q:ReturnType<typeof start>,f:CandidateField[],offline=false) {
    const before=structuredClone(current),count=writes;
    if(offline)q.reject(new Error("offline"));
    else q.resolve({form,request_id:q.r.request_id,source:"llm",warnings:[],fields:formKeys(form).map(k=>f.find(v=>v.field===k)??field(q.r,k,"keep",""))});
    await Promise.resolve();await Promise.resolve();
    assert.deepEqual(current,before);assert.equal(writes,count);
  }
  async function send(text:string,produce:(r:AssistRequest)=>CandidateField[],offline=false) {
    const q=start(text);await reply(q,produce(q.r),offline);assert.equal(s.view.phase,"ready");assert(!pending);return q.r;
  }
  function manual(k:string,v:AssistValue){current[k]=structuredClone(v);versions[k]=(versions[k]??0)+1;delete holds[k];}
  function apply(expected:AssistCurrent) {
    for(const k of formKeys(form))s.select(k,Object.hasOwn(expected,k));
    const count=writes;let patch:AssistCurrent={};
    assert.equal(s.apply(current,v=>{writes++;patch=structuredClone(v);Object.assign(current,v);}),Object.keys(expected).length>0);
    assert.deepEqual(patch,expected);assert.equal(writes,count+Number(Object.keys(expected).length>0));
  }
  return {s,start,reply,send,manual,apply,current,holds,get pending(){return pending;},get calls(){return calls;}};
}
const fields=[
  ["quote","amount","견적 금액","금액","days","공사 일수"],
  ["quote","days","공사 일수","공사 일수","amount","견적 금액"],
  ["quote","valid","유효기간","유효기간","amount","견적 금액"],
  ["quote","vat","부가세","부가세","amount","견적 금액"],
  ["period","start","시작일","시작일","end","종료일"],
  ["period","end","종료일","종료일","start","시작일"],
  ["period","excluded","제외일 목록","제외일","start","시작일"],
  ["dates","days","공사 날짜","일정","crew","작업 인원"],
  ["dates","crew","작업 인원","인원","days","작업일"],
  ["extra","amount","추가 금액","금액","title","제목"],
  ["extra","vat","부가세","부가세","amount","추가 금액"],
  ["extra","title","항목명","제목","amount","추가 금액"],
  ["extra","reason","추가 사유","사유","title","제목"],
] as const;

for(const [form,k,label,noun,sibling,siblingLabel] of fields) {
  for(const mode of ["whole","tail","rule","offline","generic","question","negative","conditional","past","other-owner","fragment","forged","stale-origin","competing","business"] as const) {
    test("combined proof "+form+"/"+k+" "+mode,async()=>{
      const h=fixture(form);
      try {
        await h.send(label+"은 아직 미정입니다. "+siblingLabel+"은 아직 미정입니다.",r=>[field(r,k,"uncertain",label+"은 아직 미정입니다"),field(r,sibling,"uncertain",siblingLabel+"은 아직 미정입니다")]);
        assert(h.holds[k]);assert(h.holds[sibling]);
        let tail="현재 "+noun+"대로 두겠습니다";
        if(mode==="generic")tail="현재 값 그대로 두겠습니다";
        if(mode==="question")tail="현재 "+noun+"대로 둘까요?";
        if(mode==="negative")tail="현재 "+noun+"대로 두지 마세요";
        if(mode==="past")tail="이전 "+noun+"대로 두겠습니다";
        if(mode==="other-owner")tail="현재 "+siblingLabel+"대로 두겠습니다";
        const object=mode==="business"?label+"의 계약 요청은":label+" 조정은";
        const scope=object+" 취소하고 "+tail;
        let text=(mode==="conditional"?"승인이 나면 ":"")+scope+".";
        if(mode==="competing")text+=" "+label+"은 아직 미정입니다.";
        let evidence=mode==="whole"||mode==="rule"||mode==="business"?scope:tail;
        if(mode==="fragment")evidence="두겠습니다";
        if(mode==="forged")evidence="현재 "+noun+"을 유지합니다";
        const req=await h.send(text,r=>{
          const v=field(r,k,"keep",evidence);
          if(mode==="rule")v.source="rule";
          if(mode==="stale-origin")v.origin_request_id="old";
          return [v];
        },mode==="offline");
        const accepted=["whole","tail","rule","offline","generic"].includes(mode);
        assert.equal(Boolean(h.holds[k]),!accepted);
        assert(h.holds[sibling],"independent unresolved hold must remain");
        assert.deepEqual(h.current,initial[form]);
        assert(!req.previous_candidate?.fields.some(f=>f.field===k));
        h.apply({});
      } finally {h.s.dispose();}
    });
  }
}

test("already-called R9 dates lifecycle with verified current tail",async()=>{
  const h=fixture("dates");try{
    await h.send("작업 인원을 4명으로 후보를 만들어 주세요.",r=>[field(r,"crew","set","작업 인원을 4명으로",4)]);
    const r=await h.send("공사 날짜를 1월 14일로 할지 1월 18일로 할지 미정이에요.",req=>{
      const old=req.previous_candidate!.fields.find(f=>f.field==="crew")!;
      return [field(req,"days","uncertain",req.text),{...old,source:"llm",status:"valid",alternatives:[],origin_request_id:req.previous_candidate!.origins!.crew,intent_kind:"retain_previous",value_source:"previous"}];
    });
    assert(r.previous_candidate?.fields.some(f=>f.field==="crew"));
    h.apply({crew:4});assert(h.holds.days);
    const final=await h.send("공사 날짜 조정은 취소하고 현재 일정대로 두겠습니다.",req=>[field(req,"days","keep","현재 일정대로 두겠습니다")]);
    assert.equal(final.previous_candidate,null);assert(!h.holds.days);h.apply({});
    assert.deepEqual(h.current.days,initial.dates.days);assert.equal(h.current.crew,4);
  }finally{h.s.dispose();}
});

for(const form of ["quote","period","dates","extra"] as const) {
  const [_,k,label,noun,sibling,siblingLabel]=fields.find(row=>row[0]===form)!;
  test("group withdrawal and exact current scope "+form,async()=>{
    const h=fixture(form);try{
      await h.send(label+"은 미정입니다. "+siblingLabel+"은 미정입니다.",r=>[field(r,k,"uncertain",label),field(r,sibling,"uncertain",siblingLabel)]);
      const scope=label+" 변경과 "+siblingLabel+" 수정 요청은 철회하고 현재 값 그대로 두겠습니다";
      await h.send(scope+".",r=>[field(r,k,"keep",scope),field(r,sibling,"keep",scope)]);
      assert(!h.holds[k]);assert(!h.holds[sibling]);h.apply({});
    }finally{h.s.dispose();}
  });
  test("manual/stale response with combined proof "+form,async()=>{
    const h=fixture(form);try{
      await h.send(label+"은 미정입니다.",r=>[field(r,k,"uncertain",label)]);
      const old=h.start(label+" 변경은 철회하고 현재 "+noun+"대로 두겠습니다.");
      h.s.cancel();assert(!h.pending);
      const fresh=h.start(label+"은 아직 미정입니다.");
      await h.reply(old,[field(old.r,k,"keep","현재 "+noun+"대로 두겠습니다")]);
      assert(h.pending);assert.equal(h.s.view.phase,"loading");assert(h.holds[k]);
      h.manual(k,initial[form][k]!);
      await h.reply(fresh,[field(fresh.r,k,"uncertain",fresh.r.text)]);
      assert(!h.holds[k]);assert.deepEqual(h.current,initial[form]);
    }finally{h.s.dispose();}
  });
}

test("complete current tail is not an assignment proof",()=>{
  const i=requestIntent("dates","공사 날짜 조정은 취소하고 현재 일정대로 두겠습니다.");
  assert(i.withdrawalProofs.has("days"));assert(!i.withdrawalProofs.has("crew"));
});
