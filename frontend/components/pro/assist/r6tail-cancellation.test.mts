// 독립 취소와 후보 유지: 실제 세션, 서버 응답만 주입. 외부 API/DB 없음.
import assert from "node:assert/strict";
import { test } from "node:test";
import { AssistSession } from "./session.ts";
import { formKeys, type AssistForm, type AssistCurrent, type AssistRequest,
  type AssistValue, type CandidateField, type RuleOptions } from "./protocol.ts";

const options: RuleOptions = {
  today: "2026-11-25", baseMonth: "2026-12-01", daysMax: 15, crewMax: 6,
  requiredDays: 2, vatModes: ["included", "excluded", "exempt"],
};
type Field = { name: string; label: string; value: AssistValue; evidence: string; alternate: AssistValue };
type Example = { form: AssistForm; current: AssistCurrent; seed: string; drop: Field; retain: Field; change: string };
const examples: Example[] = [
  { form:"quote", current:{amount:4550000,vat:"included",days:3,valid:"2026-12-17"},
    seed:"세금은 면세로 처리하고 견적 금액은 815만 7천 원으로 초안 내주세요.",
    drop:{name:"vat",label:"부가세",value:"exempt",evidence:"세금은 면세로 처리",alternate:"excluded"},
    retain:{name:"amount",label:"금액",value:8157000,evidence:"견적 금액은 815만 7천 원",alternate:6790000},
    change:"견적 금액은 679만 원으로 변경해 주세요." },
  { form:"period", current:{start:"2026-12-02",end:"2026-12-24",excluded:[]},
    seed:"실측 시작일은 12월 6일, 종료일은 12월 28일로 제안해 주세요.",
    drop:{name:"start",label:"시작일",value:"2026-12-06",evidence:"실측 시작일은 12월 6일",alternate:"2026-12-07"},
    retain:{name:"end",label:"종료일",value:"2026-12-28",evidence:"종료일은 12월 28일",alternate:"2026-12-30"},
    change:"종료일은 12월 30일로 변경해 주세요." },
  { form:"dates",current:{days:["2026-12-08","2026-12-15"],crew:2},
    seed:"시공일은 12월 9일과 18일, 작업 인원은 4명으로 제안해 주세요.",
    drop:{name:"crew",label:"작업 인원",value:4,evidence:"작업 인원은 4명",alternate:3},
    retain:{name:"days",label:"시공일",value:["2026-12-09","2026-12-18"],evidence:"시공일은 12월 9일과 18일",alternate:["2026-12-10","2026-12-19"]},
    change:"시공일은 12월 10일과 19일로 변경해 주세요." },
  { form:"extra",current:{title:"몰딩 정비",reason:"현장 확인",amount:260000,vat:"included"},
    seed:"추가 항목명은 문틀 보강, 사유는 고정 나사 풀림으로 후보를 잡아 주세요.",
    drop:{name:"title",label:"항목명",value:"문틀 보강",evidence:"추가 항목명은 문틀 보강",alternate:"문틀 교체"},
    retain:{name:"reason",label:"사유",value:"고정 나사 풀림",evidence:"사유는 고정 나사 풀림",alternate:"마감재 파손"},
    change:"사유는 마감재 파손으로 변경해 주세요." },
];
function candidate(r:AssistRequest,field:string,value:AssistValue,evidence=r.text):CandidateField {
  return {field,value,evidence,action:"set",status:"valid",source:"llm",alternatives:[],
    intent_kind:"explicit_change",value_source:"utterance",origin_request_id:r.request_id};
}
function noMention(r:AssistRequest,field:string):CandidateField {
  return {field,value:null,evidence:"",action:"keep",status:"valid",source:"rule",alternatives:[],
    intent_kind:"no_mention",origin_request_id:r.request_id};
}
function keep(r:AssistRequest,field:string,evidence:string):CandidateField {
  return {...noMention(r,field),evidence,intent_kind:"retain_current",value_source:"current",source:"llm"};
}
function carry(r:AssistRequest,field:string):CandidateField {
  const p=r.previous_candidate, f=p?.fields.find(x=>x.field===field);
  assert(p && f,field+" prior missing");
  return {...f,status:"valid",alternatives:[],source:p.sources![field],
    origin_request_id:p.origins![field],intent_kind:"retain_previous",value_source:"previous"};
}
function harness(e:Example) {
  let current=structuredClone(e.current),pending=false,writes=0;
  const holds:Record<string,string>={},versions:Record<string,number>={};
  let req:AssistRequest;
  let resolve:(value:unknown)=>void;
  const session=new AssistSession(e.form,"00000000-0000-4000-8000-000000000179",r=>
    new Promise(done=>{req=structuredClone(r);resolve=done;}));
  const live=()=>({current:structuredClone(current),manualVersions:{...versions}});
  function start(text:string) {
    const before=structuredClone(current),count=writes;
    assert(session.send(text,current,options,delta=>{
      if(typeof delta.pending==="boolean")pending=delta.pending;
      for(const [k,v] of Object.entries(delta.fieldHolds??{})){
        if(typeof v==="string")holds[k]=v;else if(v===null)delete holds[k];
      }
    },live));
    assert(pending);
    assert.equal(session.apply(current,()=>assert.fail("before response Apply")),false);
    assert.deepEqual(current,before);assert.equal(writes,count);
    return req!;
  }
  async function reply(fields:CandidateField[]) {
    const before=structuredClone(current),count=writes;
    resolve!({form:e.form,request_id:req!.request_id,source:"llm",warnings:[],
      fields:formKeys(e.form).map(k=>fields.find(f=>f.field===k)??noMention(req!,k))});
    await Promise.resolve();await Promise.resolve();
    assert.equal(session.view.phase,"ready");assert.equal(pending,false);
    assert.deepEqual(current,before);assert.equal(writes,count);
  }
  async function send(text:string,fn:(r:AssistRequest)=>CandidateField[]) {
    const r=start(text);await reply(fn(r));return r;
  }
  function manual(field:string,value:AssistValue) {
    current[field]=structuredClone(value);versions[field]=(versions[field]??0)+1;delete holds[field];
  }
  function apply(names:string[]) {
    for(const f of session.view.response!.fields)session.select(f.field,names.includes(f.field));
    const count=writes;let changes:AssistCurrent={};
    const applied=session.apply(current,p=>{writes++;changes=structuredClone(p);Object.assign(current,p);});
    assert.equal(writes,count+Number(applied));return {applied,changes};
  }
  function field(name:string){return session.view.response!.fields.find(f=>f.field===name)!;}
  async function seed(fields:Field[]=[e.drop,e.retain]) {
    return send(e.seed,r=>fields.map(f=>candidate(r,f.name,f.value,f.evidence)));
  }
  return {session,start,reply,send,manual,apply,field,seed,holds,live};
}
function offer(f:CandidateField){return f.status==="valid"&&(f.action==="set"||f.action==="clear")||f.status==="conflict";}
function keys(o:object){return Object.keys(o).sort();}
function assertNoOffer(h:ReturnType<typeof harness>,field:string){assert.equal(offer(h.field(field)),false);}
function retainText(e:Example){return e.retain.label+" 후보는 그대로 사용하겠습니다.";}
function cancelText(e:Example){return e.drop.label+" 후보는 취소하겠습니다.";}

test("executed quote-12 T2 corrected BE: preview only, exact hold and next history",async()=>{
  const e=examples[0],h=harness(e);
  try{
    const r1=await h.seed();
    const text="면세 선택은 취소할게요. 금액 후보는 그대로 사용하겠습니다.";
    const r2=await h.send(text,r=>[keep(r,"vat","면세 선택은 취소할게요"),carry(r,"amount")]);
    assert.equal(h.field("amount").origin_request_id,r1.request_id);
    assert.equal(h.field("amount").source,"llm");
    assert.equal(h.field("amount").evidence,e.retain.evidence);
    assert.equal(h.field("amount").value,8157000);
    assert.equal(h.field("vat").action,"keep");assert.equal(h.field("vat").origin_request_id,r2.request_id);
    assert.deepEqual(keys(h.holds),["amount"]);assert.deepEqual(h.live().current,e.current);
    // 독립 후속 요청은 새 검증용 문장이지 미호출 평가 문장이 아니에요.
    const r3=await h.send("금액 후보만 그대로 반영해 주세요.",r=>[carry(r,"amount")]);
    assert.deepEqual(r3.previous_candidate?.fields.map(f=>f.field),["amount"]);
    assert.equal(r3.previous_candidate?.origins?.amount,r1.request_id);
    assert.deepEqual(h.apply(["vat","amount"]),{applied:true,changes:{amount:8157000}});
    assert.equal(h.live().current.vat,"included");assert.deepEqual(h.holds,{});
    assert.equal(h.session.apply(h.live().current,()=>assert.fail("duplicate Apply")),false);
  }finally{h.session.dispose();}
});

for(const e of examples){
  const a=e.drop.name,b=e.retain.name;
  for(const order of ["cancel-first","retain-first"] as const){
    test(e.form+" typed candidate cancellation and retention "+order,async()=>{
      const h=harness(e);
      try{
        const r1=await h.seed(), cancel=cancelText(e), retain=retainText(e);
        await h.send(order==="cancel-first"?cancel+" "+retain:retain+" "+cancel,r=>[keep(r,a,cancel.slice(0,-1)),carry(r,b)]);
        assert.deepEqual(keys(h.holds),[b]);assert.equal(h.field(b).origin_request_id,r1.request_id);
        assert.deepEqual(h.live().current,e.current);
        assert.deepEqual(h.apply([a,b]),{applied:true,changes:{[b]:e.retain.value}});
        assert.deepEqual(h.live().current[a],e.current[a]);
      }finally{h.session.dispose();}
    });
  }
  for(const mode of ["fresh","missing","stale","wrong-value","wrong-source","wrong-evidence","wrong-origin","fresh-keep","missing-keep","competing","question","negative","conditional","incomplete"] as const){
    test(e.form+" explicit candidate retention guards "+mode,async()=>{
      const h=harness(e);
      try{
        await h.seed(mode==="missing"||mode==="missing-keep"?[e.drop]:undefined);
        if(mode==="stale")h.manual(b,e.retain.alternate);
        const cancel=cancelText(e);
        let retain=retainText(e);
        if(mode==="competing")retain+=" "+e.change;
        if(mode==="question")retain=e.retain.label+" 후보는 그대로 사용해도 될까요?";
        if(mode==="negative")retain=e.retain.label+" 후보는 그대로 사용하지 마세요.";
        if(mode==="conditional")retain="가능하면 "+retain;
        if(mode==="incomplete")retain=e.retain.label+" 후보는 그대로...";
        const r2=await h.send(cancel+" "+retain,r=>{
          let f:CandidateField;
          if(mode==="fresh-keep"||mode==="missing-keep")f=keep(r,b,retain.replace(/[.]$/,""));
          else if(mode==="fresh"||mode==="missing"||mode==="stale")f=candidate(r,b,e.retain.value,retain);
          else{
            f=carry(r,b);
            if(mode==="wrong-value")f.value=e.retain.alternate;
            if(mode==="wrong-source")f.source="rule";
            if(mode==="wrong-evidence")f.evidence=retain;
            if(mode==="wrong-origin")f.origin_request_id="other-request";
          }
          return [keep(r,a,cancel.slice(0,-1)),f];
        });
        if(mode==="missing"||mode==="stale"||mode==="missing-keep")assert.equal(r2.previous_candidate?.fields.some(f=>f.field===b),false);
        assertNoOffer(h,b);assert.ok(h.holds[b],mode+" retained hold");assert.equal(h.holds[a],undefined);
        assert.deepEqual(h.apply([a,b]),{applied:false,changes:{}});
        assert.deepEqual(h.live().current,{...e.current,...(mode==="stale"?{[b]:e.retain.alternate}:{})});
      }finally{h.session.dispose();}
    });
  }
  test(e.form+" verified independent current keep is not prior candidate",async()=>{
    const h=harness(e);
    try{
      await h.seed();
      const text=e.drop.label+"은 현재 입력값 그대로 유지해 주세요.";
      await h.send(text+" "+retainText(e),r=>[keep(r,a,text.slice(0,-1)),carry(r,b)]);
      assert.deepEqual(keys(h.holds),[b]);
      assert.deepEqual(h.apply([b]),{applied:true,changes:{[b]:e.retain.value}});
      assert.deepEqual(h.live().current[a],e.current[a]);
    }finally{h.session.dispose();}
  });
  test(e.form+" manual during retained response prevents overwrite",async()=>{
    const h=harness(e);
    try{
      await h.seed();
      const r=h.start(cancelText(e)+" "+retainText(e));
      h.manual(b,e.retain.alternate);
      await h.reply([keep(r,a,cancelText(e).slice(0,-1)),carry(r,b)]);
      assert.deepEqual(h.apply([a,b]),{applied:false,changes:{}});
      assert.deepEqual(h.live().current[b],e.retain.alternate);assert.equal(h.holds[b],undefined);
    }finally{h.session.dispose();}
  });
  for(const suffix of ["취소할까요?","취소하지 마세요.","취소 여부는 미정입니다.","가능하면 취소하겠습니다."]){
    test(e.form+" uncertain cancellation leaves independent retain "+suffix,async()=>{
      const h=harness(e);
      try{
        await h.seed();
        const cancel=e.drop.label+" 후보는 "+suffix;
        await h.send(cancel+" "+retainText(e),r=>[keep(r,a,cancel),carry(r,b)]);
        assert.ok(h.holds[a]);assert.equal(offer(h.field(b)),true);
        assert.deepEqual(h.apply([b]),{applied:true,changes:{[b]:e.retain.value}});
        assert.ok(h.holds[a]);
      }finally{h.session.dispose();}
    });
  }
}

const vatLabels={exempt:"면세",excluded:"별도",included:"포함"} as const;
for(const form of ["quote","extra"] as const){
  for(const mode of ["exempt","excluded","included"] as const){
    const base=examples[0],otherMode=mode==="included"?"excluded":"included";
    const choice=vatLabels[mode]+(mode==="included"?"으로":"로");
    const e:Example={...base,form,current:form==="quote"?{...base.current,vat:otherMode}:
      {title:"몰딩 정비",reason:"현장 확인",amount:4550000,vat:otherMode},
      drop:{...base.drop,value:mode,alternate:otherMode,evidence:"세금은 "+choice},
      seed:"세금은 "+choice+" 정하고 금액은 815만 7천 원으로 제안해 주세요."};
    for(const variant of ["matching","mismatch","missing","stale","wrong-proof","question","negative","condition","competing"] as const){
      test(form+" typed VAT mode withdrawal "+mode+" "+variant,async()=>{
        const h=harness(e);
        try{
          await h.seed(variant==="missing"?[e.retain]:undefined);
          if(variant==="stale")h.manual("vat",otherMode); // same-value edit must invalidate prior
          const word=variant==="mismatch"?vatLabels[otherMode]:vatLabels[mode];
          const label=(word!=="면세"||variant==="mismatch"||variant==="missing"||variant==="stale"?"부가세 ":"")+word+" 선택은 ";
          let cancel=label+"취소할게요.";
          if(variant==="question")cancel=label+"취소할까요?";
          if(variant==="negative")cancel=label+"취소하지 마세요.";
          if(variant==="condition")cancel="가능하면 "+cancel;
          if(variant==="competing")cancel+=" 부가세는 "+vatLabels[otherMode]+(otherMode==="included"?"으로":"로")+" 변경해 주세요.";
          const r=await h.send(cancel+" "+retainText(e),r=>[
            keep(r,"vat",variant==="wrong-proof"?retainText(e).slice(0,-1):cancel.slice(0,-1)),carry(r,"amount")]);
          if(variant==="missing"||variant==="stale")assert.equal(r.previous_candidate?.fields.some(f=>f.field==="vat"),false);
          assert.equal(!!h.holds.vat,variant!=="matching");
          assert.equal(offer(h.field("amount")),true);
          assert.deepEqual(h.apply(["vat","amount"]),{applied:true,changes:{amount:e.retain.value}});
          assert.equal(!!h.holds.vat,variant!=="matching");
          assert.equal(h.live().current.vat,otherMode);
        }finally{h.session.dispose();}
      });
    }
  }
}

// 포함/별도만으로 어떤 필드의 선택을 철회하는지 정할 수 없어요.
for(const form of ["quote","extra"] as const){
  for(const mode of ["excluded","included"] as const){
    const base=examples[0],otherMode=mode==="included"?"excluded":"included";
    const choice=vatLabels[mode]+(mode==="included"?"으로":"로");
    const e:Example={...base,form,current:form==="quote"?{...base.current,vat:otherMode}:
      {title:"몰딩 정비",reason:"현장 확인",amount:4550000,vat:otherMode},
      drop:{...base.drop,value:mode,evidence:"세금은 "+choice},
      seed:"세금은 "+choice+" 정하고 금액은 815만 7천 원으로 제안해 주세요."};
    test(form+" unbound "+mode+" selection does not clear holds or restore siblings",async()=>{
      const h=harness(e);
      try{
        await h.seed();
        const cancel=vatLabels[mode]+" 선택은 취소할게요.";
        await h.send(cancel+" "+retainText(e),r=>[
          keep(r,"vat",cancel.slice(0,-1)),carry(r,"amount")]);
        assertNoOffer(h,"amount");
        assert.deepEqual(keys(h.holds),["amount","vat"]);
        assert.deepEqual(h.apply(["amount","vat"]),{applied:false,changes:{}});
        assert.deepEqual(h.live().current,e.current);
      }finally{h.session.dispose();}
    });
  }
}

// 같은 세금 모드 별칭은 동일한 선택값 증명을 요구해요.
for (const form of ["quote","extra"] as const) {
  for (const [mode, label] of [
    ["exempt","부가세 면제 선택"], ["exempt","부가세 없음 후보"],
    ["exempt","부가세 0원 제안"], ["included","세금 포함된 후보"],
    ["excluded","부가세 제외 선택"], ["included","VAT 포함 후보"],
    ["included","세금 방식 포함 선택"],
  ] as const) {
    for (const missing of [false,true]) {
      test(form+" withdrawal alias "+label+(missing?" missing":" matching"),async()=>{
        const base=examples[0],otherMode=mode==="included"?"excluded":"included";
        const choice=vatLabels[mode]+(mode==="included"?"으로":"로");
        const e:Example={...base,form,current:form==="quote"?{...base.current,vat:otherMode}:
          {title:"몰딩 정비",reason:"현장 확인",amount:4550000,vat:otherMode},
          drop:{...base.drop,value:mode,evidence:"세금은 "+choice},
          seed:"세금은 "+choice+" 정하고 금액은 815만 7천 원으로 제안해 주세요."};
        const h=harness(e);
        try {
          await h.seed(missing?[e.retain]:undefined);
          const cancel=label+"은 취소합니다";
          await h.send(cancel+". "+retainText(e),r=>[keep(r,"vat",cancel),carry(r,"amount")]);
          assert.equal(!!h.holds.vat,missing);
          assert.equal(offer(h.field("amount")),true);
          assert.deepEqual(h.apply(["vat","amount"]),{applied:true,changes:{amount:e.retain.value}});
          assert.equal(h.live().current.vat,otherMode);
          assert.equal(!!h.holds.vat,missing);
        } finally {h.session.dispose();}
      });
    }
  }
}
