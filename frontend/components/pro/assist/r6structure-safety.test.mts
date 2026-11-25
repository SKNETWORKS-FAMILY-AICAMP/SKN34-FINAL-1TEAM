// 필드별 독립 상태, 수량 참조, 실제 선택/적용의 독립 회귀. 외부 요청 없음.
import assert from "node:assert/strict";
import {test} from "node:test";
import {resolve} from "node:path";
import {pathToFileURL} from "node:url";
import type {AssistForm,AssistCurrent,AssistRequest,AssistValue,CandidateField,RuleOptions} from "./protocol.ts";

const source=process.env.R6STRUCTURE_WORKER17_FE_SOURCE;
const local=(name:string)=>source?pathToFileURL(resolve(source,name+".ts")).href:new URL(name+".ts",import.meta.url).href;
const {AssistSession}=await import(local("session"));
const {formKeys}=await import(local("protocol"));
for(const name of ["session","protocol","parse","requestIntent","quantity","rules"])console.log("SOURCE_IMPORT",local(name));
const opts:RuleOptions={today:"2026-11-25",baseMonth:"2026-12-01",daysMax:120,crewMax:6,requiredDays:2,vatModes:["included","excluded","exempt"]};
const day=(n:number)=>"2026-12-"+String(n).padStart(2,"0");
const bases:Record<AssistForm,AssistCurrent>={
  quote:{amount:3650000,vat:"included",days:4,valid:"2027-01-27"},
  period:{start:day(2),end:day(29),excluded:[day(11),day(21)]},
  dates:{days:[day(9),day(16)],crew:2},
  extra:{title:"기존 공사",reason:"기존 사유",amount:100000,vat:"included"}
};
const fresh=(r:AssistRequest,field:string,value:AssistValue,evidence=r.text):CandidateField=>({
  field,action:"set",value,evidence,status:"valid",source:"llm",alternatives:[],origin_request_id:r.request_id,
  intent_kind:"explicit_change",value_source:"utterance"
});
const none=(r:AssistRequest,field:string):CandidateField=>({
  field,action:"keep",value:null,evidence:"",status:"valid",source:"rule",alternatives:[],origin_request_id:r.request_id,intent_kind:"no_mention"
});
const keep=(r:AssistRequest,field:string,evidence:string):CandidateField=>({...none(r,field),evidence,intent_kind:"retain_current",value_source:"current"});
const hold=(r:AssistRequest,field:string,evidence=r.text):CandidateField=>({...none(r,field),action:"uncertain",status:"uncertain",evidence,intent_kind:"ambiguous"});
const offered=(f:CandidateField)=>f.status==="conflict"||f.status==="valid"&&["set","clear"].includes(f.action);
function carried(r:AssistRequest,field:string):CandidateField{
  const p=r.previous_candidate,old=p?.fields.find(x=>x.field===field);assert(p&&old);
  return {...old,status:"valid",source:p.sources![field],origin_request_id:p.origins![field],alternatives:[],
    intent_kind:"retain_previous",value_source:old.value_source==="derived"?"derived":"previous"};
}
function fixture(form:AssistForm,initial:AssistCurrent=bases[form]){
  let current=structuredClone(initial),writes=0,pending=false,requests=0;
  const holds:Record<string,string>={},versions:Record<string,number>={};
  type Job={r:AssistRequest;resolve:(value:unknown)=>void;reject:(err:Error)=>void};
  let job:Job|undefined;
  const session=new AssistSession(form,"offline-r6structure-state",(r:AssistRequest)=>new Promise((resolve,reject)=>{
    requests++;job={r:structuredClone(r),resolve,reject};
  }));
  const live=()=>({current:structuredClone(current),manualVersions:{...versions}});
  function start(text:string){
    const before=structuredClone(current),n=writes,m=requests;
    assert(session.send(text,current,opts,(issues:{pending?:boolean;fieldHolds?:Record<string,string|null>})=>{
      if(typeof issues.pending==="boolean")pending=issues.pending;
      for(const [k,v] of Object.entries(issues.fieldHolds??{}))if(v===null)delete holds[k];else holds[k]=v;
    },live));
    assert.equal(requests,m+1);assert(pending&&job);
    assert.equal(session.apply(current,()=>assert.fail("early Apply")),false);
    assert.deepEqual(current,before);assert.equal(writes,n);
    return job.r;
  }
  function response(j:Job,fields:CandidateField[]){
    j.resolve({form,request_id:j.r.request_id,source:"llm",warnings:[],
      fields:formKeys(form).map((k:string)=>fields.find(f=>f.field===k)??none(j.r,k))});
  }
  async function reply(fields:CandidateField[]){
    const before=structuredClone(current),n=writes;
    assert(job);response(job,fields);
    await Promise.resolve();await Promise.resolve();
    assert.equal(session.view.phase,"ready");assert(!pending);
    assert.deepEqual(current,before);assert.equal(writes,n);
  }
  async function send(text:string,make:(r:AssistRequest)=>CandidateField[]){
    const r=start(text);await reply(make(r));return r;
  }
  function field(k:string):CandidateField{
    const f=session.view.response?.fields.find((x:CandidateField)=>x.field===k);assert(f,k);return f;
  }
  function apply(expected:AssistCurrent){
    const before=structuredClone(current),n=writes;let patch:AssistCurrent={};
    const ok=Object.keys(expected).length>0;
    assert.equal(session.apply(current,(p:AssistCurrent)=>{writes++;patch=structuredClone(p);Object.assign(current,p);}),ok);
    assert.deepEqual(patch,expected);assert.equal(writes,n+Number(ok));assert.deepEqual(current,{...before,...expected});
    if(ok)assert.equal(session.apply(current,()=>assert.fail("duplicate Apply")),false);
  }
  function manual(k:string,value:AssistValue){current[k]=structuredClone(value);versions[k]=(versions[k]??0)+1;delete holds[k];}
  function assertCarry(r:AssistRequest,k:string,origin:string,value:AssistValue,evidence:string){
    const old=r.previous_candidate?.fields.find(x=>x.field===k),f=field(k);
    assert(old&&r.previous_candidate);
    assert.equal(r.previous_candidate.origins?.[k],origin);assert.equal(r.previous_candidate.sources?.[k],"llm");
    assert.deepEqual(r.previous_candidate.baselines?.[k],current[k]);
    assert.deepEqual(old.value,value);assert.equal(old.evidence,evidence);
    assert.equal(f.status,"valid");assert.equal(f.action,"set");assert.deepEqual(f.value,value);
    assert.equal(f.source,"llm");assert.equal(f.origin_request_id,origin);assert.equal(f.evidence,evidence);
    assert.equal(f.intent_kind,"retain_previous");assert.equal(f.value_source,old.value_source==="derived"?"derived":"previous");
    assert.deepEqual(f.correction??null,old.correction??null);
    assert.equal(session.view.choices[k].selected,true);
  }
  async function cancelLate(fields:CandidateField[]){
    assert(job);const j=job,before=structuredClone(current),n=writes;
    session.cancel();assert(!pending);response(j,fields);
    await Promise.resolve();await Promise.resolve();
    assert.equal(session.view.response,null);assert.equal(session.apply(current,()=>assert.fail("late Apply")),false);
    assert.deepEqual(current,before);assert.equal(writes,n);
  }
  return {session,start,reply,send,field,apply,manual,assertCarry,cancelLate,holds,live};
}

const history=[
  ["quote","days",9,"공사 일수는 9일로 정합니다.","amount",4920000,"견적 금액은 492만원으로 정합니다.",
   "소요일은 앞서 제안한 일수 그대로 반영해 주세요.","금액 변경은 철회합니다."],
  ["dates","crew",4,"작업 인원은 4명으로 정합니다.","days",[day(8),day(19)],"작업 날짜는 12월 8일과 19일로 정합니다.",
   "인원은 직전에 제안한 인원 그대로 반영해 주세요.","작업 날짜 변경은 철회합니다."],
  ["period","end",day(28),"종료일은 12월 28일로 정합니다.","start",day(4),"시작일은 12월 4일로 정합니다.",
   "종료일은 직전 후보 그대로 적용해 주세요.","시작일 변경은 철회합니다."],
  ["extra","title","몰딩 끝부분 정리","항목명은 몰딩 끝부분 정리로 정합니다.","amount",284000,"추가 비용은 28만 4천 원으로 정합니다.",
   "항목명은 직전 후보 그대로 적용해 주세요.","금액 변경은 철회합니다."]
] as const satisfies readonly (readonly [AssistForm,string,AssistValue,string,string,AssistValue,string,string,string])[];
for(const [form,k,v,seed,other,ov,os,reference,cancel] of history){
  test("scoped cancel and independent carry "+form,async()=>{
    const h=fixture(form);try{
      const r1=await h.send(seed+" "+os,r=>[fresh(r,k,v,seed),fresh(r,other,ov,os)]);
      const r2=await h.send(cancel+" "+reference,r=>[keep(r,other,cancel),carried(r,k)]);
      h.assertCarry(r2,k,r1.request_id,v,seed);assert(!offered(h.field(other)));
      assert.equal(h.holds[other],undefined);
      h.apply({[k]:v});assert.deepEqual(h.holds,{});
    }finally{h.session.dispose();}
  });
  test("manual same-value before Send invalidates prior "+form,async()=>{
    const h=fixture(form);try{
      const r1=await h.send(seed,r=>[fresh(r,k,v,seed)]);
      h.manual(k,bases[form][k]);const r2=h.start(reference);
      assert(!r2.previous_candidate?.fields.some(x=>x.field===k));
      await h.reply([{...fresh(r2,k,v,seed),origin_request_id:r1.request_id,intent_kind:"retain_previous",value_source:"previous"}]);
      assert(!offered(h.field(k)));h.apply({});assert.deepEqual(h.live().current,bases[form]);
    }finally{h.session.dispose();}
  });
  test("manual pending same-value and next send "+form,async()=>{
    const h=fixture(form);try{
      await h.send(seed,r=>[fresh(r,k,v,seed)]);
      const r2=h.start(reference);
      const f=carried(r2,k);h.manual(k,bases[form][k]);await h.reply([f]);
      // 보이는 후보의 정책과 별개로 수동 수정 뒤 실제 적용은 금지한다.
      h.apply({});assert.equal(h.holds[k],undefined);assert.equal(h.live().manualVersions[k],1);
      const r3=h.start(os);assert(!r3.previous_candidate?.fields.some(x=>x.field===k));
      await h.reply([fresh(r3,other,ov,os)]);h.apply({[other]:ov});
    }finally{h.session.dispose();}
  });
  test("late response after cancel "+form,async()=>{
    const h=fixture(form);try{
      const r=h.start(seed);await h.cancelLate([fresh(r,k,v,seed)]);
      const n=h.start(os);assert.equal(n.previous_candidate,null);await h.reply([fresh(n,other,ov,os)]);h.apply({[other]:ov});
    }finally{h.session.dispose();}
  });
}

// 기간 수량이 없는 진짜 과거 참조와 새로운 양/불완전 수량의 구분.
for(const [tag,text] of [
  ["nominal","소요일은 직전에 제안한 일수 그대로 반영해 주세요."],
  ["past","공사 기간은 앞서 정한 일수대로 유지해 주세요."],
  ["nominal-cancel","금액 변경은 철회합니다. 공사 소요일은 앞서 제안한 일수 그대로 반영해 주세요."]
] as const)test("reference without new quantity "+tag,async()=>{
  const h=fixture("quote");const seed="공사 기간은 9일로 정합니다.";
  try{
    const r1=await h.send(seed,r=>[fresh(r,"days",9,seed)]);
    const r2=await h.send(text,r=>[carried(r,"days")]);h.assertCarry(r2,"days",r1.request_id,9,seed);h.apply({days:9});
  }finally{h.session.dispose();}
});
for(const [tag,text] of [
  ["decimal","공사 기간은 6.5일로 변경합니다."],
  ["range","공사 기간은 6~8일로 변경합니다."],
  ["approx","공사 일수는 6일 정도로 생각합니다."],
  ["question","공사 일수는 6일로 정할까요?"],
  ["mixed-reference","공사 일수는 이전 후보 그대로, 6일로 바꿔 주세요."],
  ["negative","공사 기간은 6일로 바꾸지 마세요."]
] as const)for(const kind of ["fresh","carried"] as const)test("quantity risk "+tag+" "+kind,async()=>{
  const h=fixture("quote"),seed="공사 일수는 9일로 정합니다.",vat="부가세는 별도로 합니다.";
  try{
    await h.send(seed,r=>[fresh(r,"days",9,seed)]);
    await h.send(text+" "+vat,r=>[kind==="fresh"?fresh(r,"days",6,text):carried(r,"days"),fresh(r,"vat","excluded",vat)]);
    assert(!offered(h.field("days")));h.apply({vat:"excluded"});
  }finally{h.session.dispose();}
});
test("fresh duration has fresh origin",async()=>{
  const h=fixture("quote");try{
    await h.send("공사 일수는 9일로 정합니다.",r=>[fresh(r,"days",9)]);
    const r2=await h.send("공사 기간은 6일로 변경합니다.",r=>[fresh(r,"days",6)]);
    assert.equal(h.field("days").origin_request_id,r2.request_id);assert.notEqual(h.field("days").value_source,"previous");h.apply({days:6});
  }finally{h.session.dispose();}
});

// 출처/근거/기준/정정의 독립 위조를 차단한다.
for(const key of ["origin","source","evidence","value","correction"] as const)test("forged history "+key,async()=>{
  const h=fixture("quote");try{
    const seed="공사 기간은 9일로 정합니다.";
    const r1=await h.send(seed,r=>[fresh(r,"days",9)]);
    const r2=await h.send("소요일은 직전 후보 그대로 적용해 주세요.",r=>{
      const f=carried(r,"days");
      if(key==="origin")f.origin_request_id="forged";
      if(key==="source")f.source="rule";
      if(key==="evidence")f.evidence="공사 기간은 8일";
      if(key==="value")f.value=8;
      if(key==="correction")f.correction={kind:"replace",rejected_value:4,rejected_evidence:"4일",accepted_evidence:"9일"};
      return [f];
    });
    if(key==="correction"){
      // 스칼라 정정 관계는 이력에 전달하지 않는다. 관계를 버린 정확한 이전 값만 허용한다.
      h.assertCarry(r2,"days",r1.request_id,9,seed);
      assert.equal(h.field("days").correction??null,null);
      const r3=h.start("소요일은 직전 후보 그대로 적용해 주세요.");
      assert.equal(r3.previous_candidate?.fields.find(f=>f.field==="days")?.correction??null,null);
      await h.reply([carried(r3,"days")]);
      h.assertCarry(r3,"days",r1.request_id,9,seed);
      assert.equal(h.field("days").correction??null,null);
      h.apply({days:9});
    }else{assert(!offered(h.field("days")));h.apply({});}
  }finally{h.session.dispose();}
});
test("derived date correction retains proof on later crew change",async()=>{
  const h=fixture("dates"),seed="작업일은 12월 8일과 19일로 정합니다.",edit="이전 작업일 중 19일을 22일로 옮겨 주세요.";
  try{
    await h.send(seed,r=>[fresh(r,"days",[day(8),day(19)],seed)]);
    const correction={kind:"replace" as const,rejected_value:[day(8),day(19)],rejected_evidence:"19일",accepted_evidence:"22일"};
    const r2=await h.send(edit,r=>[{...fresh(r,"days",[day(8),day(22)],edit),value_source:"derived",correction}]);
    assert.deepEqual(h.field("days").correction,correction);
    const r3=await h.send("작업 인원은 5명으로 정합니다.",r=>[carried(r,"days"),fresh(r,"crew",5)]);
    h.assertCarry(r3,"days",r2.request_id,[day(8),day(22)],edit);
    assert.deepEqual(h.field("days").correction,correction);h.apply({days:[day(8),day(22)],crew:5});
  }finally{h.session.dispose();}
});
test("forged derived date correction cannot inherit exact list value",async()=>{
  const h=fixture("dates"),seed="작업일은 12월 8일과 19일로 정합니다.",edit="이전 작업일 중 19일을 22일로 옮겨 주세요.";
  try{
    await h.send(seed,r=>[fresh(r,"days",[day(8),day(19)],seed)]);
    const correction={kind:"replace" as const,rejected_value:[day(8),day(19)],rejected_evidence:"19일",accepted_evidence:"22일"};
    const r2=await h.send(edit,r=>[{...fresh(r,"days",[day(8),day(22)],edit),value_source:"derived",correction}]);
    assert.deepEqual(h.field("days").correction,correction);
    const r3=await h.send("작업일은 직전 후보 그대로 유지하고 작업 인원은 5명으로 정합니다.",r=>{
      const old=r.previous_candidate?.fields.find(f=>f.field==="days");
      assert.deepEqual(old?.correction,correction);
      assert.deepEqual(r.previous_candidate?.baselines?.days,bases.dates.days);
      assert.equal(r.previous_candidate?.origins?.days,r2.request_id);
      const f=carried(r,"days");
      f.correction={...correction,rejected_value:[day(8),day(20)],rejected_evidence:"20일"};
      return [f,fresh(r,"crew",5)];
    });
    assert.equal(r3.previous_candidate?.sources?.days,"llm");
    assert(!offered(h.field("days")));
    assert.equal(h.session.view.choices.days.selected,false);
    assert(h.holds.days);
    h.apply({crew:5});
    assert.deepEqual(h.live().current.days,bases.dates.days);
    assert(h.holds.days);
  }finally{h.session.dispose();}
});

// 전체 제안이 유효해도 부분 Apply의 조합은 다시 확인한다.
for(const selected of [["start","end","excluded"],["start"],["end"],["excluded"]] as const)test("coupled period Apply "+selected.join("+"),async()=>{
  const h=fixture("period",{start:day(2),end:day(10),excluded:[day(6)]});
  try{
    const text="시작일은 12월 17일로, 종료일은 12월 27일로 변경합니다. 제외일은 12월 21일로 지정합니다.";
    await h.send(text,r=>[fresh(r,"start",day(17),"시작일은 12월 17일로"),fresh(r,"end",day(27),"종료일은 12월 27일로 변경합니다"),fresh(r,"excluded",[day(21)],"제외일은 12월 21일로 지정합니다")]);
    for(const k of ["start","end","excluded"])assert(offered(h.field(k)),k);
    for(const k of ["start","end","excluded"])h.session.select(k,(selected as readonly string[]).includes(k));
    const patch=selected.length===3?{start:day(17),end:day(27),excluded:[day(21)]}:selected[0]==="end"?{end:day(27)}:{};
    h.apply(patch);
    if(!Object.keys(patch).length)for(const k of selected)assert(h.holds[k],k);
  }finally{h.session.dispose();}
});
for(const [good,bad,v,badValue] of [["start","end",day(4),day(18)],["end","start",day(27),day(14)]] as const)test("independent period valid field "+good,async()=>{
  const h=fixture("period");const text="시작일과 종료일을 변경합니다.";
  try{
    await h.send(text,r=>[fresh(r,good,v),hold(r,bad)]);
    assert(offered(h.field(good)));assert(!offered(h.field(bad)));
    h.apply({[good]:v});assert(h.holds[bad]);assert.equal(h.holds[good],undefined);
    assert.notEqual(h.live().current[bad],badValue);
  }finally{h.session.dispose();}
});
for(const index of [0,1])test("explicit genuine conflict choice "+index,async()=>{
  const h=fixture("extra"),a="항목명은 몰딩 보수로 정합니다.",b="항목명은 창틀 마감으로 정합니다.";
  const values=["몰딩 보수","창틀 마감"];
  try{
    await h.send(a+" "+b,r=>[{...hold(r,"title"),status:"conflict",alternatives:[
      {action:"set",value:values[0],source:"rule",evidence:a},
      {action:"set",value:values[1],source:"llm",evidence:b}]}]);
    assert.equal(h.field("title").status,"conflict");assert.equal(h.session.view.choices.title.selected,false);
    h.apply({});h.session.chooseAlternative("title",index);
    assert.equal(h.session.view.choices.title.alternative,index);assert(h.session.view.choices.title.selected);h.apply({title:values[index]});
  }finally{h.session.dispose();}
});
