// 실행된 날짜 정정과 독립 이력 참조를 실제 세션으로 검증해요.
import assert from "node:assert/strict";
import { test } from "node:test";
import { AssistSession } from "./session.ts";
import { formKeys, type AssistForm, type AssistCurrent, type AssistRequest,
  type AssistValue, type CandidateField, type RuleOptions } from "./protocol.ts";

const opts: RuleOptions={today:"2026-11-25",baseMonth:"2026-12-01",daysMax:30,crewMax:6,
  requiredDays:2,vatModes:["included","excluded","exempt"]};
type F={name:string; label:string; noun:string; value:AssistValue; alternate:AssistValue; evidence:string; change:string};
type E={form:AssistForm; current:AssistCurrent; seed:string; a:F; b:F};
const examples:E[]=[
  {form:"quote",current:{amount:3100000,vat:"included",days:4,valid:"2026-12-17"},
    seed:"세금은 별도로 처리하고 만료일은 12월 25일로 정해 주세요.",
    a:{name:"vat",label:"세금 조건",noun:"방식",value:"excluded",alternate:"exempt",evidence:"세금은 별도로 처리",change:"부가세는 면세로 바꿔 주세요."},
    b:{name:"valid",label:"만료 날짜",noun:"날짜",value:"2026-12-25",alternate:"2026-12-28",evidence:"만료일은 12월 25일",change:"만료 날짜는 12월 28일로 변경해 주세요."}},
  {form:"period",current:{start:"2026-12-02",end:"2026-12-24",excluded:[]},
    seed:"실측 시작일은 12월 6일, 종료일은 12월 28일로 제안해 주세요.",
    a:{name:"start",label:"시작일",noun:"날짜",value:"2026-12-06",alternate:"2026-12-08",evidence:"실측 시작일은 12월 6일",change:"시작일은 12월 8일로 변경해 주세요."},
    b:{name:"end",label:"종료일",noun:"날짜",value:"2026-12-28",alternate:"2026-12-30",evidence:"종료일은 12월 28일",change:"종료일은 12월 30일로 변경해 주세요."}},
  {form:"dates",current:{days:["2026-12-09","2026-12-16"],crew:2},
    seed:"투입 인원은 3명에 공사 날짜는 12월 8일과 29일로 먼저 후보 주세요.",
    a:{name:"crew",label:"작업자 수",noun:"인원",value:3,alternate:5,evidence:"투입 인원은 3명",change:"인원은 5명으로 변경해 주세요."},
    b:{name:"days",label:"공사일",noun:"날짜",value:["2026-12-08","2026-12-29"],alternate:["2026-12-10","2026-12-30"],evidence:"공사 날짜는 12월 8일과 29일",change:"공사일은 12월 10일과 30일로 변경해 주세요."}},
  {form:"extra",current:{title:"문틀 정비",reason:"기존 점검",amount:260000,vat:"included"},
    seed:"항목명은 몰딩 보강, 사유는 접착부 들뜸으로 제안해 주세요.",
    a:{name:"title",label:"항목명",noun:"제목",value:"몰딩 보강",alternate:"몰딩 교체",evidence:"항목명은 몰딩 보강",change:"항목명은 몰딩 교체로 변경해 주세요."},
    b:{name:"reason",label:"사유",noun:"이유",value:"접착부 들뜸",alternate:"고정구 손상",evidence:"사유는 접착부 들뜸",change:"사유는 고정구 손상으로 변경해 주세요."}},
];
const dates=examples[2];
function set(r:AssistRequest,field:string,value:AssistValue,evidence=r.text):CandidateField {
  return {field,value,evidence,action:"set",status:"valid",source:"llm",alternatives:[],
    intent_kind:"explicit_change",value_source:"utterance",origin_request_id:r.request_id};
}
function noMention(r:AssistRequest,field:string):CandidateField {
  return {field,value:null,evidence:"",action:"keep",status:"valid",source:"rule",alternatives:[],
    intent_kind:"no_mention",origin_request_id:r.request_id};
}
function uncertain(r:AssistRequest,field:string,evidence=r.text):CandidateField {
  return {...noMention(r,field),action:"uncertain",status:"uncertain",intent_kind:"ambiguous",source:"llm",evidence};
}
function carry(r:AssistRequest,field:string):CandidateField {
  const p=r.previous_candidate,f=p?.fields.find(x=>x.field===field);
  assert(p && f,field+" prior missing");
  return {...f,status:"valid",alternatives:[],source:p.sources![field],
    origin_request_id:p.origins![field],intent_kind:"retain_previous",value_source:"previous"};
}
function corrected(r:AssistRequest,old=3,next=5,oldSpan="3명이라 했는데",nextSpan="5명으로 정정하겠습니다"):CandidateField {
  return {...set(r,"crew",next,oldSpan+" "+nextSpan),
    correction:{kind:"replace",rejected_value:old,rejected_evidence:oldSpan,accepted_evidence:nextSpan}};
}
function fixture(e:E) {
  let current=structuredClone(e.current),pending=false,writes=0;
  const holds:Record<string,string>={},versions:Record<string,number>={};
  let job:{r:AssistRequest;resolve:(v:unknown)=>void;reject:(err:Error)=>void}|undefined;
  const session=new AssistSession(e.form,"r6end-history",r=>new Promise((resolve,reject)=>{
    job={r:structuredClone(r),resolve,reject};
  }));
  const live=()=>({current:structuredClone(current),manualVersions:{...versions}});
  function start(text:string) {
    const before=structuredClone(current),count=writes;
    assert(session.send(text,current,opts,delta=>{
      if(typeof delta.pending==="boolean")pending=delta.pending;
      for(const [k,v] of Object.entries(delta.fieldHolds??{})){
        if(typeof v==="string")holds[k]=v;else if(v===null)delete holds[k];
      }
    },live));
    assert(pending);assert.equal(session.apply(current,()=>assert.fail("before reply")),false);
    assert.deepEqual(current,before);assert.equal(writes,count);return job!.r;
  }
  async function reply(fields:CandidateField[],offline=false) {
    const before=structuredClone(current),count=writes,r=job!.r;
    if(offline)job!.reject(new Error("offline"));
    else job!.resolve({form:e.form,request_id:r.request_id,source:"llm",warnings:[],
      fields:formKeys(e.form).flatMap(k=>fields.some(f=>f.field===k)?fields.filter(f=>f.field===k):[noMention(r,k)])});
    await Promise.resolve();await Promise.resolve();
    assert.equal(session.view.phase,"ready");assert.equal(pending,false);
    assert.deepEqual(current,before);assert.equal(writes,count);
  }
  async function send(text:string,fn:(r:AssistRequest)=>CandidateField[]) {
    const r=start(text);await reply(fn(r));return r;
  }
  function manual(field:string,value:AssistValue){
    current[field]=structuredClone(value);versions[field]=(versions[field]??0)+1;delete holds[field];
  }
  function apply(fields:string[]){
    for(const f of session.view.response!.fields)session.select(f.field,fields.includes(f.field));
    let patch:AssistCurrent={};const count=writes;
    const applied=session.apply(current,p=>{writes++;patch=structuredClone(p);Object.assign(current,p);});
    assert.equal(writes,count+Number(applied));return {applied,patch};
  }
  function field(name:string){return session.view.response!.fields.find(f=>f.field===name)!;}
  async function seed(fields:F[]=[e.a,e.b]){
    return send(e.seed,r=>fields.map(f=>set(r,f.name,f.value,f.evidence)));
  }
  return {session,start,reply,send,manual,apply,field,seed,holds,live};
}
function offered(f:CandidateField){return f.status==="valid"&&(f.action==="set"||f.action==="clear")||f.status==="conflict";}
function noOffer(h:ReturnType<typeof fixture>,field:string){assert.equal(offered(h.field(field)),false,field);}
const actualCorrection="조금 전에 인원을 3명이라 했는데 5명으로 정정하겠습니다.";
const actualParallel="작업자 수는 방금 고친 후보, 공사일은 앞서 고른 날짜로 적용해주세요.";
function exactProof(r:AssistRequest){return {...corrected(r),evidence:"인원을 3명이라 했는데 5명으로 정정하겠습니다"};}

test("executed dates-11 T1/T2/T3 actual session with corrected server retention",async()=>{
  const h=fixture(dates);
  try{
    const t1=await h.seed();
    const t2=await h.send(actualCorrection,r=>[carry(r,"days"),exactProof(r)]);
    assert.equal(h.field("crew").value,5);assert.equal(h.field("crew").origin_request_id,t2.request_id);
    assert.equal(h.field("days").origin_request_id,t1.request_id);
    assert.deepEqual(Object.keys(h.holds).sort(),["crew","days"]);
    assert.deepEqual(h.live().current,dates.current);
    const t3=await h.send(actualParallel,r=>[carry(r,"crew"),carry(r,"days")]);
    assert.equal(t3.previous_candidate?.origins?.crew,t2.request_id);
    assert.equal(t3.previous_candidate?.origins?.days,t1.request_id);
    assert.deepEqual(t3.previous_candidate?.baselines,dates.current);
    assert.equal(h.field("crew").origin_request_id,t2.request_id);
    assert.equal(h.field("days").origin_request_id,t1.request_id);
    assert.deepEqual(h.apply(["crew","days"]),{applied:true,patch:{crew:5,days:dates.b.value}});
    assert.deepEqual(h.holds,{});assert.equal(h.session.apply(h.live().current,()=>assert.fail("duplicate Apply")),false);
  }finally{h.session.dispose();}
});

for(const [label,text,oldSpan,nextSpan] of [
  ["recent-spaced",actualCorrection,"3명이라 했는데","5명으로 정정하겠습니다"],
  ["recent-compact","조금전에 인원은 3명이라고 말했는데 5명으로 변경할게요.","3명이라고 말했는데","5명으로 변경할게요"],
  ["earlier-turn","직전에 작업 인원을 3명이라고 말씀드렸는데 5명으로 수정해 주세요.","3명이라고 말씀드렸는데","5명으로 수정해 주세요"],
  ["past-turn","지난번에 인력을 3명이라고 전달했는데 5명으로 정정할게요.","3명이라고 전달했는데","5명으로 정정할게요"],
  ["native","앞에서 인원은 세 명이라 했는데 다섯 명으로 정정하겠습니다.","세 명이라 했는데","다섯 명으로 정정하겠습니다"],
  ["old-existing","아까 인원을 3명이라 했는데 5명으로 정정하겠습니다.","3명이라 했는데","5명으로 정정하겠습니다"],
  ["independent-date-after","아까 인원을 3명이라 했는데 5명으로 정정하겠습니다. 공사일은 아직 미정입니다.","3명이라 했는데","5명으로 정정하겠습니다"],
  ["independent-date-before","공사일은 아직 미정입니다. 아까 인원을 3명이라 했는데 5명으로 정정하겠습니다.","3명이라 했는데","5명으로 정정하겠습니다"],
  ["independent-date-question","아까 인원을 3명이라 했는데 5명으로 정정하겠습니다. 공사일은 12월 19일이 가능할까요?","3명이라 했는데","5명으로 정정하겠습니다"],
] as const){
  test("historical crew accepts "+label,async()=>{
    const h=fixture(dates);
    try{
      const t1=await h.seed();
      const r=await h.send(text,q=>[corrected(q,3,5,oldSpan,nextSpan),
        ...(label.startsWith("independent")?[uncertain(q,"days","공사일")]:[carry(q,"days")])]);
      assert.equal(h.field("crew").status,"valid");assert.equal(h.field("crew").value,5);
      assert.equal(h.field("crew").origin_request_id,r.request_id);
      assert.equal(r.previous_candidate?.origins?.crew,t1.request_id);
      assert.deepEqual(h.live().current,dates.current);assert.ok(h.holds.crew);
      assert.deepEqual(h.apply(["crew"]),{applied:true,patch:{crew:5}});
      if(label.startsWith("independent"))assert.ok(h.holds.days);
    }finally{h.session.dispose();}
  });
}

for(const mode of ["wrong-value","wrong-old","wrong-source","wrong-origin","missing-proof","swapped-proof","foreign-proof",
  "empty-proof","duplicate","missing","stale","manual-during","bound","third-count","question","negative",
  "condition","attached-date-condition","same-field-cancel","unknown-risk","offline","same-field-keep"] as const){
  test("historical crew rejects "+mode,async()=>{
    const h=fixture(dates);
    try{
      const t1=await h.seed(mode==="missing"?[dates.b]:undefined);
      if(mode==="stale")h.manual("crew",2); // same-value manual version
      let text=actualCorrection;
      if(mode==="bound")text=text.replace("5명","7명");
      if(mode==="third-count")text+=" 인원은 4명도 생각 중입니다.";
      if(mode==="question")text=text.replace("정정하겠습니다.","정정할까요?");
      if(mode==="negative")text=text.replace("정정하겠습니다.","정정하지 마세요.");
      if(mode==="condition")text="가능하면 "+text;
      if(mode==="attached-date-condition")text="공사일이 확정되면, "+text;
      if(mode==="same-field-cancel")text+=" 인원 정정은 취소할게요.";
      if(mode==="unknown-risk")text+=" 혹시 괜찮을까요?";
      if(mode==="same-field-keep")text+=" 작업 인원 후보는 그대로 유지해 주세요.";
      const r=h.start(text);
      if(mode==="manual-during")h.manual("crew",4);
      let f=exactProof(r);
      if(mode==="wrong-value")f.value=4;
      if(mode==="wrong-old")f.correction!.rejected_value=2;
      if(mode==="wrong-source")f.source="rule";
      if(mode==="wrong-origin")f.origin_request_id=t1.request_id;
      if(mode==="missing-proof")delete f.correction;
      if(mode==="swapped-proof")f.correction={kind:"replace",rejected_value:3,rejected_evidence:"5명으로 정정하겠습니다",accepted_evidence:"3명이라 했는데"};
      if(mode==="foreign-proof")f.correction!.rejected_evidence="지난번 인원 3명";
      if(mode==="empty-proof")f.correction!.accepted_evidence="";
      if(mode==="bound")f=corrected(r,3,7,"3명이라 했는데","7명으로 정정하겠습니다");
      await h.reply([carry(r,"days"),f,...(mode==="duplicate"?[f]:[])],mode==="offline");
      if(mode==="missing"||mode==="stale")assert.equal(r.previous_candidate?.fields.some(f=>f.field==="crew"),false);
      if(mode!=="manual-during")noOffer(h,"crew");
      assert.deepEqual(h.apply(["crew"]),{applied:false,patch:{}});
      assert.equal(h.live().current.crew,mode==="manual-during"?4:2);
      if(mode!=="manual-during")assert.ok(h.holds.crew);
    }finally{h.session.dispose();}
  });
}

for(const e of examples){
  const a=e.a.name,b=e.b.name;
  for(const [verb,end] of [["고른","적용해주세요"],["골랐던","사용하겠습니다"],["선택했던","반영해 주세요"]] as const){
    test(e.form+" shared selection "+verb,async()=>{
      const h=fixture(e);
      try{
        const t1=await h.seed();
        const t2=await h.send(e.a.change,r=>[set(r,a,e.a.alternate),carry(r,b)]);
        await h.send(e.a.label+"은 방금 고친 후보, "+e.b.label+"은 앞서 "+verb+" "+e.b.noun+"로 "+end+".",r=>[carry(r,a),carry(r,b)]);
        assert.equal(h.field(a).origin_request_id,t2.request_id);assert.equal(h.field(b).origin_request_id,t1.request_id);
        assert.deepEqual(h.live().current,e.current);
        assert.deepEqual(h.apply([a,b]),{applied:true,patch:{[a]:e.a.alternate,[b]:e.b.value}});
        assert.deepEqual(h.holds,{});
      }finally{h.session.dispose();}
    });
  }
  for(const mode of ["fresh-a","fresh-b","missing-a","missing-b","stale-a","stale-b","wrong-type","wrong-value",
    "wrong-source","wrong-origin","wrong-evidence","competing","question","negative","condition","incomplete","manual-after"] as const){
    test(e.form+" shared selection protects "+mode,async()=>{
      const h=fixture(e);
      try{
        await h.seed(mode==="missing-a"?[e.b]:mode==="missing-b"?[e.a]:undefined);
        if(mode==="stale-a")h.manual(a,e.current[a]);
        if(mode==="stale-b")h.manual(b,e.current[b]);
        const badA=["fresh-a","missing-a","stale-a","competing"].includes(mode);
        const badB=["fresh-b","missing-b","stale-b","wrong-type","wrong-value","wrong-source","wrong-origin","wrong-evidence"].includes(mode);
        const both=["question","negative","condition","incomplete"].includes(mode);
        let text=e.a.label+"은 방금 고친 후보, "+e.b.label+"은 앞서 고른 "+(mode==="wrong-type"?"금액":e.b.noun)+"로 적용해주세요.";
        if(mode==="competing")text+=" "+e.a.change;
        if(mode==="question")text=text.replace("적용해주세요.","적용할까요?");
        if(mode==="negative")text=text.replace("적용해주세요.","적용하지 마세요.");
        if(mode==="condition")text=text.replace("적용해주세요.","가능하면 적용해주세요.");
        if(mode==="incomplete")text=text.replace("적용해주세요.","");
        const r=await h.send(text,q=>{
          const x=["fresh-a","missing-a","stale-a"].includes(mode)?set(q,a,e.a.value):carry(q,a);
          const y=["fresh-b","missing-b","stale-b"].includes(mode)?set(q,b,e.b.value):carry(q,b);
          if(mode==="wrong-value")y.value=e.b.alternate;
          if(mode==="wrong-source")y.source="rule";
          if(mode==="wrong-origin")y.origin_request_id="wrong-origin";
          if(mode==="wrong-evidence")y.evidence=q.text;
          return [x,y];
        });
        if(mode.startsWith("missing")||mode.startsWith("stale"))assert.equal(r.previous_candidate?.fields.some(f=>f.field===(mode.endsWith("a")?a:b)),false);
        if(mode==="manual-after"){
          h.manual(a,e.a.alternate);
          assert.deepEqual(h.apply([a,b]),{applied:false,patch:{}});
          assert.deepEqual(h.apply([b]),{applied:true,patch:{[b]:e.b.value}});
          assert.deepEqual(h.live().current[a],e.a.alternate);return;
        }
        if(badA||both)noOffer(h,a);else assert.equal(offered(h.field(a)),true);
        if(badB||both)noOffer(h,b);else assert.equal(offered(h.field(b)),true);
        const expected:AssistCurrent={};
        if(!badA&&!both)expected[a]=e.a.value;
        if(!badB&&!both)expected[b]=e.b.value;
        assert.deepEqual(h.apply([a,b]),{applied:Object.keys(expected).length>0,patch:expected});
        if(badA||both)assert.ok(h.holds[a]);if(badB||both)assert.ok(h.holds[b]);
      }finally{h.session.dispose();}
    });
  }
}

for (const form of ["quote","extra"] as const) {
  for (const mode of ["verified","missing","fresh","wrong-type","question"] as const) {
    test(form+" historical mode setting after manual amount "+mode,async()=>{
      const base=examples[0];
      const e:E={...base,form,current:form==="quote"?base.current:
        {title:"도장 보수",reason:"표면 들뜸",amount:85000,vat:"included"},
        seed:"부가세 포함 말고 별도로 표시해 주세요.",
        a:{...base.a,evidence:"부가세 포함 말고 별도로 표시해 주세요"}};
      const h=fixture(e);
      try {
        if(mode!=="missing") await h.seed([e.a]);
        h.manual("amount",337000);
        const noun=mode==="wrong-type"?"금액":"설정";
        const clause="부가세 구분은 아까 요청했던 "+noun+" 그대로 "+(mode==="question"?"갈까요?":"가주세요.");
        const text="제가 금액 입력란에서 33만 7천 원으로 이미 수정해 뒀습니다. 그 금액은 놔두고 "+clause;
        await h.send(text,r=>[mode==="missing"||mode==="fresh"?set(r,"vat","excluded",clause):carry(r,"vat")]);
        if(mode==="verified"){
          assert.equal(offered(h.field("vat")),true);
          assert.deepEqual(h.apply(["vat"]),{applied:true,patch:{vat:"excluded"}});
        }else{
          noOffer(h,"vat");
          assert.deepEqual(h.apply(["vat"]),{applied:false,patch:{}});
        }
        assert.equal(h.live().current.amount,337000);
      }finally{h.session.dispose();}
    });
  }
}
