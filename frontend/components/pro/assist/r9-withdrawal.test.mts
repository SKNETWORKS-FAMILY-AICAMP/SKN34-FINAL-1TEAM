// R9: frozen actual-session controls for typed withdrawal proof and field lifecycle.
import assert from "node:assert/strict";
import { test } from "node:test";
import { AssistSession } from "./session.ts";
import { formKeys, type AssistCurrent, type AssistForm, type AssistRequest, type AssistValue, type CandidateField, type RuleOptions } from "./protocol.ts";
const opts:RuleOptions={today:"2026-11-25",baseMonth:"2027-01-01",requiredDays:2,daysMax:365,crewMax:6,vatModes:["included","excluded","exempt"]};
const currents:Record<AssistForm,AssistCurrent>={
 quote:{amount:3260000,vat:"included",days:4,valid:"2027-01-27"},
 extra:{title:"기존 보강",reason:"기존 손상",amount:320000,vat:"included"},
 dates:{days:["2027-01-08","2027-01-12"],crew:2},
 period:{start:"2027-01-06",end:"2027-01-29",excluded:["2027-01-14","2027-01-22"]},
};
function set(r:AssistRequest,field:string,value:AssistValue,evidence=r.text):CandidateField{
 return {field,action:"set",value,evidence,status:"valid",source:"llm",intent_kind:"explicit_change",value_source:"utterance",origin_request_id:r.request_id,alternatives:[]};
}
function keep(r:AssistRequest,field:string,evidence:string,source:"llm"|"rule"="llm"):CandidateField{
 return {field,action:"keep",value:null,evidence,status:"valid",source,intent_kind:"retain_current",value_source:"current",origin_request_id:r.request_id,alternatives:[]};
}
function none(r:AssistRequest,field:string):CandidateField{
 return {field,action:"keep",value:null,evidence:"",status:"valid",source:"rule",intent_kind:"no_mention",origin_request_id:r.request_id,alternatives:[]};
}
function hold(r:AssistRequest,field:string,evidence=r.text):CandidateField{
 return {field,action:"uncertain",value:null,evidence,status:"uncertain",source:"llm",intent_kind:"ambiguous",origin_request_id:r.request_id,reason_code:"ambiguous_intent",alternatives:[]};
}
function carry(r:AssistRequest,field:string):CandidateField{
 const p=r.previous_candidate;assert(p);
 const old=p.fields.find(x=>x.field===field);assert(old);
 return {...old,status:"valid",source:p.sources?.[field]??"llm",origin_request_id:p.origins?.[field]??p.request_id,alternatives:[],intent_kind:"retain_previous",value_source:"previous"};
}
function offered(f:CandidateField){return f.status==="valid"&&(f.action==="set"||f.action==="clear")||f.status==="conflict";}
function fixture(form:AssistForm,initial:AssistCurrent=currents[form],options:RuleOptions=opts){
 let current=structuredClone(initial),pending=false,writes=0;
 const versions:Record<string,number>={},holds:Record<string,string>={};
 type Pending={request:AssistRequest;resolve:(v:unknown)=>void;reject:(v:unknown)=>void};
 const requests:Pending[]=[];
 const session=new AssistSession(form,"00000000-0000-4000-8000-000000000179",r=>new Promise((resolve,reject)=>requests.push({request:r,resolve,reject})));
 function start(text:string){
  assert(session.send(text,current,options,delta=>{
   if(typeof delta.pending==="boolean")pending=delta.pending;
   for(const [f,v] of Object.entries(delta.fieldHolds??{})){if(typeof v==="string")holds[f]=v;else if(v===null)delete holds[f];}
  },()=>({current:structuredClone(current),manualVersions:{...versions}})));
  assert(pending);assert.equal(session.apply(current,()=>assert.fail("Apply before reply")),false);
  return requests.at(-1)!;
 }
 async function reply(p:Pending,fields:CandidateField[],offline=false){
  const before=structuredClone(current),count=writes;
  if(offline)p.reject(new Error("offline"));
  else p.resolve({form,request_id:p.request.request_id,source:"llm",warnings:[],fields:formKeys(form).map(field=>fields.find(f=>f.field===field)??none(p.request,field))});
  await Promise.resolve();await Promise.resolve();
  assert.deepEqual(current,before);assert.equal(writes,count);
 }
 async function send(text:string,produce:(r:AssistRequest)=>CandidateField[],offline=false){
  const p=start(text);await reply(p,produce(p.request),offline);assert.equal(session.view.phase,"ready");assert(!pending);return p.request;
 }
 function field(key:string){return session.view.response!.fields.find(f=>f.field===key)!;}
 function manual(key:string,value:AssistValue){current[key]=structuredClone(value);versions[key]=(versions[key]??0)+1;delete holds[key];}
 function apply(expected:AssistCurrent){
  for(const key of formKeys(form))session.select(key,Object.hasOwn(expected,key));
  let patch:AssistCurrent={};const count=writes;
  assert.equal(session.apply(current,v=>{writes++;patch=structuredClone(v);Object.assign(current,v);}),Object.keys(expected).length>0);
  assert.equal(writes,count+Number(Object.keys(expected).length>0));assert.deepEqual(patch,expected);
 }
 return {session,start,reply,send,field,manual,apply,holds,get current(){return current;},get pending(){return pending;}};
}
const cases=[
 {form:"quote",field:"amount",label:"견적 금액",value:3810000,token:"381만 원",sibling:"days",siblingLabel:"공사 일수",siblingValue:7,siblingToken:"7일",siblingAssign:"공사 일수는 7일로 확정합니다.",business:"견적 금액의 납부 요청은 취소합니다."},
 {form:"extra",field:"title",label:"항목명",value:"문틀 보강",token:"문틀 보강",sibling:"amount",siblingLabel:"추가 금액",siblingValue:730000,siblingToken:"73만 원",siblingAssign:"추가 금액은 73만 원으로 확정합니다.",business:"항목명의 주문 요청은 취소합니다."},
 {form:"dates",field:"crew",label:"작업 인원",value:4,token:"4명",sibling:"days",siblingLabel:"작업 일정",siblingValue:["2027-01-14","2027-01-21"],siblingToken:"1월 14일과 21일",siblingAssign:"작업 일정은 1월 14일과 21일로 확정합니다.",business:"작업 인원 배치 계약은 취소합니다."},
 {form:"period",field:"start",label:"시작 날짜",value:"2027-01-09",token:"1월 9일",sibling:"end",siblingLabel:"종료 날짜",siblingValue:"2027-02-03",siblingToken:"2월 3일",siblingAssign:"종료 날짜는 2월 3일로 확정합니다.",business:"시작 날짜의 실측 예약을 취소합니다."},
] as const;
type C=(typeof cases)[number];
function assign(c:C){return c.label+"은 "+c.token+"으로 확정합니다.";}
async function seeded(c:C){
 const h=fixture(c.form);
 const seed=await h.send(assign(c)+" "+c.siblingAssign,r=>[set(r,c.field,c.value as AssistValue,assign(c)),set(r,c.sibling,c.siblingValue as AssistValue,c.siblingAssign)]);
 assert(offered(h.field(c.field)));assert(offered(h.field(c.sibling)));assert.ok(h.holds[c.field]);assert.ok(h.holds[c.sibling]);
 return {h,seed};
}
test("R8 called period09 T1/T2 stored reject and corrected retain-current response",async()=>{
 for(const corrected of [false,true]){
  const h=fixture("period");
  try{
   await h.send("실측 가능 시작일은 2026년 11월 25일로 입력해 주세요.",r=>[{...hold(r,"start","시작일은 2026년 11월 25일로 입력"),status:"invalid",reason_code:"business_constraint"}]);
   assert(h.holds.start);h.apply({});
   const scope="시작 날짜 조정은 철회합니다",text=scope+". 실측 마감은 2027년 1월 30일로 설정해 주세요.";
   await h.send(text,r=>[corrected?keep(r,"start",scope):{...hold(r,"start",""),source:"rule",origin_request_id:null},set(r,"end","2027-01-30","실측 마감은 2027년 1월 30일")]);
   assert.equal(Boolean(h.holds.start),!corrected);
   h.apply({end:"2027-01-30"});assert.equal(h.current.start,"2027-01-06");assert.equal(Boolean(h.holds.start),!corrected);
  }finally{h.session.dispose();}
 }
});
for(const c of cases){
 const scope=c.label+" 조정은 철회합니다";
 const retain=c.siblingLabel+" 후보는 그대로 사용하겠습니다.";
 for(const mode of ["model","rule","group","question","negative","conditional","report","business","wrong-field","fragment","stale-origin","competing","contradictory"] as const){
  test("typed withdrawal "+c.form+" "+mode,async()=>{
   const {h,seed}=await seeded(c);try{
    let phrase=scope+".";
    if(mode==="group")phrase=c.label+"과 "+c.siblingLabel+" 조정은 철회합니다.";
    if(mode==="question")phrase=c.label+" 조정을 철회할까요?";
    if(mode==="negative")phrase=c.label+" 조정을 철회하지 마세요.";
    if(mode==="conditional")phrase="승인이 나면 "+scope+".";
    if(mode==="report")phrase="고객이 "+scope+".";
    if(mode==="business")phrase=c.business;
    if(mode==="competing")phrase+=" "+c.label+"은 아직 미정입니다.";
    if(mode==="contradictory")phrase+=" 그런데 그건 취소하지 마세요.";
    const text=phrase+(mode==="group"?"":" "+retain);
    const r=await h.send(text,req=>{
     const evidence=mode==="wrong-field"?retain:mode==="fragment"?"철회합니다":mode==="contradictory"?scope:phrase.replace(/[.]$/,"");
     const k=keep(req,c.field,evidence,mode==="rule"?"rule":"llm");
     if(mode==="stale-origin")k.origin_request_id=seed.request_id;
     if(mode==="group")return [k,keep(req,c.sibling,evidence)];
     return [k,carry(req,c.sibling)];
    });
    assert.equal(r.previous_candidate?.origins?.[c.field],seed.request_id);
    const approved=mode==="model"||mode==="rule"||mode==="group";
    assert.equal(Boolean(h.holds[c.field]),!approved);
    assert(!offered(h.field(c.field)));
    if(mode==="group"){
     assert(!h.holds[c.sibling]);assert(!offered(h.field(c.sibling)));h.apply({});
     await h.send(c.label+" 후보는 그대로 사용하겠습니다.",req=>{assert.equal(req.previous_candidate,null);return [set(req,c.field,c.value as AssistValue)];});
     assert(!offered(h.field(c.field)));h.apply({});
    }else{
     assert(offered(h.field(c.sibling)));assert.equal(h.field(c.sibling).origin_request_id,seed.request_id);
     const next=await h.send(retain,req=>[carry(req,c.sibling)]);
     assert(!next.previous_candidate?.fields.some(x=>x.field===c.field));
     assert.equal(next.previous_candidate?.origins?.[c.sibling],seed.request_id);
     h.apply({[c.sibling]:c.siblingValue as AssistValue});
     assert.deepEqual(h.current[c.field],currents[c.form][c.field]);
     assert.equal(Boolean(h.holds[c.field]),!approved);
    }
   }finally{h.session.dispose();}
  });
 }
 test("offline verified operation "+c.form,async()=>{
  const h=fixture(c.form);try{
   await h.send(c.label+"은 아직 미정입니다.",r=>[hold(r,c.field)]);
   await h.send(scope+". "+c.siblingAssign,()=>[],true);
   assert(!h.holds[c.field]);h.apply({[c.sibling]:c.siblingValue as AssistValue});
  }finally{h.session.dispose();}
 });
 test("actual conflict select then cancellation prevents revival "+c.form,async()=>{
  const h=fixture(c.form);try{
   // Use independent valid candidates and exercise explicit deselection/reselection.
   const seed=await h.send(assign(c)+" "+c.siblingAssign,r=>[set(r,c.field,c.value as AssistValue,assign(c)),set(r,c.sibling,c.siblingValue as AssistValue,c.siblingAssign)]);
   h.session.select(c.field,false);h.session.select(c.field,true);
   const canceled=h.start(scope+". "+retain);h.session.cancel();assert(!h.pending);assert.equal(h.session.view.phase,"idle");
   const next=h.start(c.siblingAssign);
   await h.reply(canceled,[keep(canceled.request,c.field,scope),carry(canceled.request,c.sibling)]);
   assert.equal(h.session.view.phase,"loading");assert(h.pending);
   assert.equal(next.request.previous_candidate,null);
   await h.reply(next,[set(next.request,c.sibling,c.siblingValue as AssistValue,c.siblingAssign)]);
   assert.equal(h.session.view.phase,"ready");h.apply({[c.sibling]:c.siblingValue as AssistValue});
   assert.deepEqual(h.current[c.field],currents[c.form][c.field]);assert.notEqual(seed.request_id,next.request.request_id);
  }finally{h.session.dispose();}
 });
 test("manual before and during withdrawal keep cannot revive old field "+c.form,async()=>{
  const {h}=await seeded(c);try{
   h.manual(c.field,currents[c.form][c.field]);
   const p=h.start(scope+". "+retain);
   assert(!p.request.previous_candidate?.fields.some(x=>x.field===c.field));
   h.manual(c.field,c.value as AssistValue);
   await h.reply(p,[keep(p.request,c.field,scope),carry(p.request,c.sibling)]);
   assert(!h.holds[c.field]);assert(!offered(h.field(c.field)));
   h.apply({[c.sibling]:c.siblingValue as AssistValue});
   assert.deepEqual(h.current[c.field],c.value);
  }finally{h.session.dispose();}
 });
}
// Described scalar edit withdrawal is distinct from selection of a specific historical candidate.
// The original over-strict diagnostic is preserved in the frozen baseline directory.
for(const c of [cases[0],cases[2]])for(const mode of ["matching","mismatch","missing","stale","two-values"] as const){
 test("described scalar operation withdrawal "+c.form+" "+mode,async()=>{
  const h=fixture(c.form);try{
   await h.send(assign(c)+" "+c.siblingAssign,r=>[...(mode==="missing"?[]:[set(r,c.field,c.value as AssistValue,assign(c))]),set(r,c.sibling,c.siblingValue as AssistValue,c.siblingAssign)]);
   if(mode==="stale")h.manual(c.field,currents[c.form][c.field]);
   const token=mode==="mismatch"?(c.form==="quote"?"382만 원":"5명"):c.token;
   const scope=c.label+" "+(mode==="two-values"?c.token+(c.form==="quote"?"을 400만 원":"에서 5명"):token)+"으로 변경하려던 요청은 철회합니다";
   const retain=c.siblingLabel+" 후보는 그대로 사용하겠습니다.";
   await h.send(scope+". "+retain,r=>[keep(r,c.field,scope),carry(r,c.sibling)]);
   assert(!h.holds[c.field]);assert(!offered(h.field(c.field)));
   h.apply({[c.sibling]:c.siblingValue as AssistValue});
   assert.deepEqual(h.current[c.field],currents[c.form][c.field]);
  }finally{h.session.dispose();}
 });
}
for(const c of cases)for(const mode of ["matching","mismatch","missing","stale"] as const){
 test("selected historical candidate withdrawal "+c.form+" "+mode,async()=>{
  const h=fixture(c.form);try{
   await h.send(assign(c)+" "+c.siblingAssign,r=>[...(mode==="missing"?[]:[set(r,c.field,c.value as AssistValue,assign(c))]),set(r,c.sibling,c.siblingValue as AssistValue,c.siblingAssign)]);
   if(mode==="stale")h.manual(c.field,currents[c.form][c.field]);
   const mismatch={quote:"382만 원",dates:"5명",period:"1월 10일",extra:"창틀 보강"}[c.form];
   const token=mode==="mismatch"?mismatch:c.token;
   const scope="앞서 제안한 "+c.label+" "+(c.form==="extra"?'"'+token+'"':token)+" 후보를 철회합니다";
   const retain=c.siblingLabel+" 후보는 그대로 사용하겠습니다.";
   const r=await h.send(scope+". "+retain,req=>[keep(req,c.field,scope),carry(req,c.sibling)]);
   if(mode==="stale"||mode==="missing")assert(!r.previous_candidate?.fields.some(x=>x.field===c.field));
   assert.equal(Boolean(h.holds[c.field]),mode!=="matching");assert(!offered(h.field(c.field)));
   const next=await h.send(retain,req=>[carry(req,c.sibling)]);
   assert(!next.previous_candidate?.fields.some(x=>x.field===c.field));
   h.apply({[c.sibling]:c.siblingValue as AssistValue});
   assert.deepEqual(h.current[c.field],currents[c.form][c.field]);
  }finally{h.session.dispose();}
 });
}
for(const applied of [false,true])test("conflict alternative selection then typed withdrawal "+applied,async()=>{
 const h=fixture("quote");try{
  await h.send("견적 381만 원 아니고 382만 원",r=>[{...hold(r,"amount"),status:"conflict",alternatives:[
   {action:"set",value:3810000,source:"rule",evidence:"381만 원"},
   {action:"set",value:3820000,source:"llm",evidence:"382만 원"},
  ]}]);
  assert.equal(h.field("amount").status,"conflict");
  assert.equal(h.session.apply(h.current,()=>assert.fail("unselected conflict")),false);
  h.session.chooseAlternative("amount",1);
  if(applied)h.apply({amount:3820000});
  const scope="견적 금액 조정은 철회합니다";
  const r=await h.send(scope+". 공사 일수는 7일로 확정합니다.",req=>[keep(req,"amount",scope),set(req,"days",7)]);
  assert(!r.previous_candidate?.fields.some(f=>f.field==="amount"));
  assert(!h.holds.amount);assert(!offered(h.field("amount")));
  h.apply({days:7});assert.equal(h.current.amount,applied?3820000:currents.quote.amount);
  const p=h.start("공사 일수 8일로 확정합니다.");
  assert(!p.request.previous_candidate?.fields.some(f=>f.field==="amount"));
  await h.reply(p,[set(p.request,"days",8)]);
  h.apply({days:8});
 }finally{h.session.dispose();}
});
// Whole-sentence group must be fully owned, and unbound business cancellation
// must never clear the pending amount hold or resurrect a previous candidate.
test("unbound actual business cancellation is not generic pending withdrawal",async()=>{
 const c=cases[0],{h}=await seeded(c);try{
  await h.send("견적서 발송은 취소합니다.",r=>[keep(r,c.field,r.text),keep(r,c.sibling,r.text)]);
  assert(h.holds[c.field]);assert(h.holds[c.sibling]);h.apply({});
 }finally{h.session.dispose();}
});

// Integrated R8/R9 regression rows and the already exercised stage2 live20 S2-Q5.
for(const seeded of [false,true])test("R8 linked crew modification withdrawal "+seeded,async()=>{
 const h=fixture("dates",{days:["2026-12-09","2026-12-16"],crew:2},{...opts,baseMonth:"2026-12-01"});try{
  if(seeded)await h.send("작업 인원을 4명으로 변경해도 될까요?",r=>[hold(r,"crew")]);
  const scope="작업 인원 수정은 없던 일로 하고",text=scope+" 작업일은 12월 8일과 18일로 확정해 주세요.";
  await h.send(text,r=>[keep(r,"crew",scope,"rule"),set(r,"days",["2026-12-08","2026-12-18"])]);
  assert(!h.holds.crew);h.apply({days:["2026-12-08","2026-12-18"]});
  assert.equal(h.current.crew,2);
 }finally{h.session.dispose();}
});
for(const [scope,field] of [["작업 인원 증원 요청은 철회합니다","crew"],["작업 날짜 변경 신청은 철회합니다","days"]] as const)test("owned operation noun "+field,async()=>{
 const h=fixture("dates");try{
  await h.send("작업일과 작업 인원은 미정입니다.",r=>[hold(r,"crew"),hold(r,"days")]);
  await h.send(scope+".",r=>[keep(r,field,scope)]);
  assert(!h.holds[field]);assert(h.holds[field==="crew"?"days":"crew"]);h.apply({});
 }finally{h.session.dispose();}
});
for(const c of cases)for(const approved of [true,false])test("coordinated operation objects "+c.form+" "+approved,async()=>{
 const {h}=await seeded(c);try{
  const scope=c.label+(approved?" 변경과 ":" 계약과 ")+c.siblingLabel+" 수정 요청을 철회합니다";
  await h.send(scope+".",r=>[keep(r,c.field,scope),keep(r,c.sibling,scope)]);
  assert.equal(Boolean(h.holds[c.field]),!approved);
  if(approved)assert(!h.holds[c.sibling]);
  assert(!offered(h.field(c.field)));h.apply({});
 }finally{h.session.dispose();}
});
test("S2-Q5 unlabeled described scalar correction withdrawal after manual edit",async()=>{
 const h=fixture("quote",{...currents.quote,amount:4550000,days:3});try{
  await h.send("총액은 496만 원으로 올려 주세요.",r=>[set(r,"amount",4960000)]);
  h.manual("amount",5300000);
  const scope="아까 496만 원을 536만 원으로 정정하려던 요청은 철회합니다";
  const req=await h.send(scope+". 부가세만 별도로 처리해 주세요.",r=>[keep(r,"amount",scope,"rule"),set(r,"vat","excluded")]);
  assert(!req.previous_candidate?.fields.some(f=>f.field==="amount"));
  assert(!h.holds.amount);h.apply({vat:"excluded"});assert.equal(h.current.amount,5300000);
 }finally{h.session.dispose();}
});
for(const mode of ["affirmative","question","conditional"] as const)test("shared period boundary explicit proposal "+mode,async()=>{
 const h=fixture("period",{start:"2026-12-02",end:"2026-12-24",excluded:[]},{...opts,baseMonth:"2026-12-01"});try{
  const text=(mode==="conditional"?"가능하면 ":"")+"방문 시작일은 12월 4일로, 종료일은 12월 27일로 "+(mode==="question"?"제안해도 될까요?":"제안해 주세요.");
  const req=await h.send(text,()=>[],true);
  if(mode==="affirmative"){
   assert.equal(req.rule_fields.find(f=>f.field==="start")?.value,"2026-12-04");
   assert.equal(req.rule_fields.find(f=>f.field==="end")?.value,"2026-12-27");
   h.apply({start:"2026-12-04",end:"2026-12-27"});
  }else{assert(!offered(h.field("start")));assert(!offered(h.field("end")));h.apply({});}
 }finally{h.session.dispose();}
});
for(const field of ["title","reason"] as const)for(const previousHold of [false,true])test("foreign historical report does not create unrelated text hold "+field+" "+previousHold,async()=>{
 const h=fixture("extra");try{
  if(previousHold)await h.send((field==="title"?"항목명":"사유")+"은 미정입니다.",r=>[hold(r,field)]);
  await h.send("추가 금액을 35만 원으로 바꿀 수 있을까요?",r=>[hold(r,"amount")]);
  const evidence="지난주에는 추가 금액 변경 요청을 철회했다고 합니다";
  const req=await h.send(evidence+". 부가세는 별도로 청구해 주세요.",r=>[hold(r,field,evidence),hold(r,"amount",""),set(r,"vat","excluded")]);
  assert.equal(req.rule_fields.find(f=>f.field==="title")?.action,"keep");
  assert.equal(Boolean(h.holds[field]),previousHold);
  assert(h.holds.amount);h.apply({vat:"excluded"});
  assert.equal(Boolean(h.holds[field]),previousHold);
 }finally{h.session.dispose();}
});
