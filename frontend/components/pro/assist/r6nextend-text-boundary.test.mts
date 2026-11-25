// 제목/사유 전체 값의 경계와 독립 금액 계승을 실제 AssistSession에서 확인해요.
import assert from "node:assert/strict";
import { test } from "node:test";
import { parseExtra } from "./parse.ts";
import { AssistSession } from "./session.ts";
import { formKeys, type AssistCurrent, type AssistRequest, type CandidateField, type AssistValue } from "./protocol.ts";

const today="2026-11-25", opts={today,vatModes:["included","excluded","exempt"]};
const initial={title:"보강",reason:"현장 추가",amount:1000000,vat:"included"};
const amount=913000;
type TextField="title"|"reason";
function set(r:AssistRequest,field:string,value:AssistValue,evidence=r.text):CandidateField {
  return {field,value,evidence,action:"set",status:"valid",source:"llm",alternatives:[],
    intent_kind:"explicit_change",value_source:"utterance",origin_request_id:r.request_id};
}
function noMention(r:AssistRequest,field:string):CandidateField {
  return {field,value:null,evidence:"",action:"keep",status:"valid",source:"rule",alternatives:[],
    intent_kind:"no_mention",origin_request_id:r.request_id};
}
function carry(r:AssistRequest,field:string):CandidateField {
  const p=r.previous_candidate,old=p?.fields.find(x=>x.field===field);
  assert(p&&old);
  return {...old,status:"valid",source:p.sources![field],origin_request_id:p.origins![field],
    alternatives:[],intent_kind:"retain_previous",value_source:"previous"};
}
function conflict(field:TextField,values:{value:string;evidence:string;source:"llm"|"rule"}[]):CandidateField {
  return {field,action:"uncertain",value:null,evidence:"",status:"conflict",source:"llm",origin_request_id:null,
    alternatives:values.map(x=>({...x,action:"set"})),reason_code:"ambiguous_intent",intent_kind:"ambiguous"};
}
function fixture(){
  let current:AssistCurrent={...initial},pending=false,writes=0;
  const holds:Record<string,string>={},versions:Record<string,number>={};
  let job:{r:AssistRequest;resolve:(v:unknown)=>void;reject:(e:Error)=>void}|undefined;
  const session=new AssistSession("extra","r6nextend-boundary",r=>new Promise((resolve,reject)=>{
    job={r:structuredClone(r),resolve,reject};
  }));
  const live=()=>({current:{...current},manualVersions:{...versions}});
  function start(text:string){
    const before={...current},count=writes;
    assert(session.send(text,current,opts,delta=>{
      if(typeof delta.pending==="boolean")pending=delta.pending;
      for(const [key,value] of Object.entries(delta.fieldHolds??{})){
        if(typeof value==="string")holds[key]=value;else if(value===null)delete holds[key];
      }
    },live));
    assert(pending);assert.equal(session.apply(current,()=>assert.fail("before reply")),false);
    assert.deepEqual(current,before);assert.equal(writes,count);return job!.r;
  }
  async function reply(fields:CandidateField[]=[],offline=false){
    const before={...current},count=writes,r=job!.r;
    if(offline)job!.reject(new Error("offline"));
    else job!.resolve({form:"extra",request_id:r.request_id,source:"llm",warnings:[],
      fields:formKeys("extra").map(k=>fields.find(f=>f.field===k)??noMention(r,k))});
    await Promise.resolve();await Promise.resolve();assert.equal(session.view.phase,"ready");assert(!pending);
    assert.deepEqual(current,before);assert.equal(writes,count);
  }
  async function send(text:string,fn:(r:AssistRequest)=>CandidateField[]){
    const r=start(text);await reply(fn(r));return r;
  }
  async function seedAmount(){
    return send("추가 비용은 91만 3천 원으로 후보만 내주세요.",r=>[set(r,"amount",amount,"91만 3천 원")]);
  }
  function manual(field:string,value:AssistValue){current[field]=value;versions[field]=(versions[field]??0)+1;delete holds[field];}
  function apply(names:string[]){
    for(const f of session.view.response!.fields)session.select(f.field,names.includes(f.field));
    const count=writes;let patch:AssistCurrent={};
    const applied=session.apply(current,p=>{writes++;patch={...p};Object.assign(current,p);});
    assert.equal(writes,count+Number(applied));return {applied,patch};
  }
  function field(name:string){return session.view.response!.fields.find(f=>f.field===name)!;}
  return {session,start,reply,send,seedAmount,manual,apply,field,holds,live};
}
function offered(f:CandidateField){return f.status==="valid"&&["set","clear"].includes(f.action)||f.status==="conflict";}
function assertHeld(h:ReturnType<typeof fixture>,field:string){assert.equal(offered(h.field(field)),false,field);assert.ok(h.holds[field]);}
const exact="추가 항목명은 선반 하부 보강으로 새로 정합니다. 비용은 앞서 제안한 값으로 해주세요.";
const good="선반 하부 보강", bad="선반 하부 보강으로 새";
const titleClause=exact.split(".")[0];
for(const mode of ["corrected","stored-conflict","hostile-set","offline"] as const){
  test("executed extra-09 T1-T3 "+mode,async()=>{
    const h=fixture();
    try{
      const t1=await h.seedAmount();
      const t2=await h.send("항목 이름은 앞에서 정했던 걸로 유지 바랍니다.",r=>[
        {...noMention(r,"title"),status:"uncertain",action:"uncertain",intent_kind:"ambiguous",
          evidence:"항목 이름은 앞에서 정했던 걸로 유지 바랍니다",source:"llm"},carry(r,"amount")]);
      assertHeld(h,"title");assert.ok(h.holds.amount);
      const t3=h.start(exact);
      assert.deepEqual(t3.previous_candidate?.fields.map(f=>f.field),["amount"]);
      assert.equal(t3.previous_candidate?.origins?.amount,t1.request_id);
      assert.equal(t2.previous_candidate?.origins?.amount,t1.request_id);
      if(mode==="offline")await h.reply([],true);
      else if(mode==="corrected")await h.reply([set(t3,"title",good,titleClause),carry(t3,"amount")]);
      else if(mode==="hostile-set")await h.reply([set(t3,"title",bad,exact),carry(t3,"amount")]);
      else await h.reply([conflict("title",[{value:bad,evidence:exact,source:"rule"},{value:good,evidence:titleClause,source:"llm"}]),carry(t3,"amount")]);
      assert.deepEqual(h.live().current,initial);
      if(mode==="corrected"){
        assert.equal(t3.rule_fields.find(f=>f.field==="title")?.value,good);
        assert.equal(h.field("title").value,good);
        assert.equal(h.field("title").origin_request_id,t3.request_id);
        assert.equal(h.field("amount").origin_request_id,t1.request_id);
        assert.deepEqual(h.apply(["title","amount"]),{applied:true,patch:{title:good,amount}});
        assert.deepEqual(h.holds,{});
      }else if(mode==="offline"){
        assert.equal(h.field("title").value,good);
        assert.equal(offered(h.field("amount")),false);
        assert.deepEqual(h.apply(["title","amount"]),{applied:true,patch:{title:good}});
        assert.ok(h.holds.amount);
      }else{
        assertHeld(h,"title");assert.deepEqual(h.field("title").alternatives,[]);
        h.session.chooseAlternative("title",0);
        assert.equal(h.session.view.choices.title.alternative,null);
        assert.equal(h.field("amount").origin_request_id,t1.request_id);
        assert.deepEqual(h.apply(["title","amount"]),{applied:true,patch:{amount}});
        assert.ok(h.holds.title);
      }
      assert.equal(h.session.apply(h.live().current,()=>assert.fail("duplicate Apply")),false);
    }finally{h.session.dispose();}
  });
}

for(const [field,label,value,alt] of [
  ["title","항목명은 ","경첩 하부 보완","걸쇠 조정"],
  ["reason","추가 사유는 ","고정용 앵커 느슨함","자재 접합부 균열"],
] as const){
  for(const suffix of ["으로 새로 정합니다","으로 새롭게 지정해 주세요","으로 다시 입력합니다","으로 재차 기재하겠습니다",
    "으로 한 번 더 확정할게요","이라고 새로 적어 주세요","으로새로정합니다","으로 정합니다",""]){
    test(field+" complete assignment boundary "+suffix,async()=>{
      const h=fixture(),phrase=label+value+suffix;
      try{
        assert.equal(parseExtra(phrase+".",today)[field],value);
        const t1=await h.seedAmount();
        const r=await h.send(phrase+". 비용은 앞서 제안한 값으로 해주세요.",q=>[set(q,field,value,phrase),carry(q,"amount")]);
        assert.equal(r.rule_fields.find(f=>f.field===field)?.value,value);
        assert.equal(h.field(field).value,value);assert.equal(h.field("amount").origin_request_id,t1.request_id);
        assert.deepEqual(h.apply([field,"amount"]),{applied:true,patch:{[field]:value,amount}});
        assert.deepEqual(h.holds,{});
      }finally{h.session.dispose();}
    });
  }
  for(const [literal,quote] of [[value+"으로 새로 정합니다",['"','"']],[value+", "+alt+"로 정합니다",["“","”"]],[value+"으로 새",["'","'"]]] as const){
    test(field+" quoted literal "+literal,async()=>{
      const h=fixture(),phrase=label+quote[0]+literal+quote[1]+"로 정합니다.";
      try{
        assert.equal(parseExtra(phrase,today)[field],literal);
        await h.send(phrase,r=>[set(r,field,literal,phrase)]);
        assert.deepEqual(h.apply([field]),{applied:true,patch:{[field]:literal}});
      }finally{h.session.dispose();}
    });
  }
  for(const mode of ["clipped-value","clipped-adverb","whole-directive","unrelated","wrong-evidence","empty-evidence",
    "foreign-evidence","fresh-clear","single-survivor","genuine-conflict","genuine-conflict-with-bad","competing-set",
    "question","negative","condition","cancel","incomplete-modifier","incomplete-verb","manual-before","manual-during","manual-after",
    "prior-carry","prior-quoted-literal","other-field-uncertain"] as const){
    test(field+" grounded session "+mode,async()=>{
      const h=fixture();
      try{
        const t1=await h.seedAmount(),clause=label+value+"으로 새로 정합니다";
        let text=clause+". 비용은 앞서 제안한 값으로 해주세요.";
        if(mode==="question")text=label+value+"으로 새로 정할까요? 비용은 앞서 제안한 값으로 해주세요.";
        if(mode==="negative")text=label+value+"으로 새로 정하지 마세요. 비용은 앞서 제안한 값으로 해주세요.";
        if(mode==="condition")text="가능하면 "+text;
        if(mode==="cancel")text=clause+". "+label+"취소하겠습니다. 비용은 앞서 제안한 값으로 해주세요.";
        if(mode==="incomplete-modifier")text=label+value+"으로 새로. 비용은 앞서 제안한 값으로 해주세요.";
        if(mode==="incomplete-verb")text=label+value+"으로 새로 정하기. 비용은 앞서 제안한 값으로 해주세요.";
        if(mode==="other-field-uncertain")text=clause+". 부가세 별도로 해도 될까요? 비용은 앞서 제안한 값으로 해주세요.";
        if(mode==="manual-before")h.manual("amount",1000000);
        const multiple=["genuine-conflict","genuine-conflict-with-bad","competing-set"].includes(mode);
        const second=label+alt+"으로 다시 지정해 주세요";
        if(multiple)text=clause+". "+second+". 비용은 앞서 제안한 값으로 해주세요.";
        const positive=["manual-before","manual-during","manual-after","prior-carry","prior-quoted-literal","other-field-uncertain"].includes(mode);
        const literal=value+"으로 새로 정합니다";
        if(mode==="prior-quoted-literal")text=label+'"'+literal+'"로 확정합니다. 비용은 앞서 제안한 값으로 해주세요.';
        const r=h.start(text);
        if(mode==="manual-during")h.manual(field,"직접 수정");
        let f=set(r,field,mode==="prior-quoted-literal"?literal:value,text);
        if(mode==="clipped-value")f.value=value.slice(0,-2);
        if(mode==="clipped-adverb")f.value=value+"으로 새";
        if(mode==="whole-directive")f.value=value+"으로 새로 정합니다";
        if(mode==="unrelated")f.value="다른 공사";
        if(mode==="wrong-evidence")f.evidence="비용은 앞서 제안한 값으로 해주세요";
        if(mode==="empty-evidence")f.evidence="";
        if(mode==="foreign-evidence")f.evidence="외부에 적힌 "+value;
        if(mode==="fresh-clear"){f.action="clear";f.value=null;}
        if(mode==="single-survivor")f=conflict(field,[{value,evidence:clause,source:"llm"},
          {value:value+"으로 새",evidence:text,source:"rule"}]);
        if(mode==="genuine-conflict"||mode==="genuine-conflict-with-bad")f=conflict(field,[
          {value,evidence:clause,source:"llm"},{value:alt,evidence:second,source:"rule"},
          ...(mode==="genuine-conflict-with-bad"?[{value:alt+"으로 다시 지정해 주세요",evidence:second,source:"rule" as const}]:[])]);
        await h.reply([f,...(mode==="manual-before"?[set(r,"amount",amount,"비용은 앞서 제안한 값으로 해주세요")]:[carry(r,"amount")])]);
        if(mode==="manual-before"){
          assert.equal(r.previous_candidate?.fields.some(f=>f.field==="amount")??false,false);
          assert.deepEqual(h.apply([field]),{applied:true,patch:{[field]:value}});return;
        }
        assert.equal(h.field("amount").origin_request_id,t1.request_id);
        if(mode==="manual-after")h.manual(field,"직접 수정");
        if(mode==="manual-after"||mode==="manual-during"){
          assert.deepEqual(h.apply([field,"amount"]),{applied:false,patch:{}});
          assert.deepEqual(h.apply(["amount"]),{applied:true,patch:{amount}});
          assert.equal(h.live().current[field],"직접 수정");return;
        }
        if(mode==="genuine-conflict"||mode==="genuine-conflict-with-bad"){
          assert.equal(h.field(field).status,"conflict");
          assert.deepEqual(h.field(field).alternatives.map(a=>a.value),[value,alt]);
          assert.equal(h.session.view.choices[field].selected,false);
          h.session.chooseAlternative(field,1);
          assert.deepEqual(h.apply([field,"amount"]),{applied:true,patch:{[field]:alt,amount}});return;
        }
        if(mode==="prior-carry"||mode==="prior-quoted-literal"){
          const next=await h.send(label.replace(/[은는]\s+$/,"")+" 후보는 그대로 사용하겠습니다.",q=>[carry(q,field),carry(q,"amount")]);
          assert.equal(next.previous_candidate?.origins?.[field],r.request_id);
          assert.deepEqual(h.apply([field,"amount"]),{applied:true,patch:{[field]:mode==="prior-quoted-literal"?literal:value,amount}});return;
        }
        if(positive){
          assert.deepEqual(h.apply([field,"amount"]),{applied:true,patch:{[field]:value,amount}});return;
        }
        assertHeld(h,field);assert.deepEqual(h.field(field).alternatives,[]);
        h.session.chooseAlternative(field,0);
        assert.deepEqual(h.apply([field,"amount"]),{applied:true,patch:{amount}});
        assert.ok(h.holds[field]);assert.equal(h.live().current[field],initial[field]);
      }finally{h.session.dispose();}
    });
  }
}

for(const [field,clause] of [
  ["title","항목명은 경첩 보완으로 새로 정할까요?"],
  ["title","항목명은 경첩 보완으로 새로 정하지 마세요."],
  ["reason","가능하면 사유는 앵커 느슨함으로 새로 정하겠습니다."],
  ["reason","사유는 앵커 느슨함으로 다시 정할 예정입니다."],
] as const){
  test("independent definite amount with text-only risk "+clause,async()=>{
    const h=fixture(),text="추가 비용은 34만 원으로 정합니다. "+clause;
    try{
      assert.equal(parseExtra(text,today).amount,340000);
      const r=h.start(text);
      assert.equal(r.rule_fields.find(f=>f.field==="amount")?.value,340000);
      await h.reply([],true);
      assertHeld(h,field);
      assert.deepEqual(h.apply(["amount",field]),{applied:true,patch:{amount:340000}});
      assert.ok(h.holds[field]);
    }finally{h.session.dispose();}
  });
}
for(const text of [
  "추가 비용은 34만 원으로 정할까요? 항목명은 경첩 보완으로 정합니다.",
  "추가 비용은 34만 원으로 정합니다. 항목명은 경첩 보완으로 정합니다. 금액 변경은 취소하겠습니다.",
  "사유가 확정되면, 추가 비용은 34만 원으로 정합니다.",
]){
  test("amount risk is not erased by text-field scope "+text,async()=>{
    const h=fixture();
    try{
      const r=h.start(text);
      assert.notEqual(r.rule_fields.find(f=>f.field==="amount")?.action,"set");
      await h.reply([set(r,"amount",340000,"34만 원")]);
      assertHeld(h,"amount");
      assert.deepEqual(h.apply(["amount"]),{applied:false,patch:{}});
    }finally{h.session.dispose();}
  });
}

for(const [field,label] of [["title","항목명은 "],["reason","사유는 "]] as const){
  for(const [punctuation,suffix] of [[".",""],[".","으로 새로 정합니다"],["?","으로 정합니다"],["!","으로 정합니다"]] as const){
    for(const clip of [false,true]){
      test(field+" exact terminal quoted punctuation "+punctuation+" "+suffix+" clip="+clip,async()=>{
        const h=fixture(),value="경첩 보완"+punctuation,clause=label+'"'+value+'"'+suffix;
        try{
          await h.seedAmount();
          assert.equal(parseExtra(clause,today)[field],value);
          const r=await h.send(clause+". 비용은 앞서 제안한 값으로 해주세요.",q=>[
            set(q,field,clip?value.slice(0,-1):value,clause),carry(q,"amount")]);
          assert.equal(r.rule_fields.find(f=>f.field===field)?.value,value);
          if(clip)assertHeld(h,field);
          else assert.equal(h.field(field).value,value);
          assert.deepEqual(h.apply([field,"amount"]),{applied:true,patch:{amount,...(clip?{}:{[field]:value})}});
          assert.equal(!!h.holds[field],clip);
        }finally{h.session.dispose();}
      });
    }
  }
}

// 인용된 값의 내부 라벨/숫자는 별도 칸의 실제 요청이 아니에요.
for(const [field,label,literal,foreign] of [
  ["title","항목명은 ","사유는 별도, 부가세는 포함, 금액은 37만 원","reason"],
  ["reason","사유는 ","항목명은 경첩 보완, 부가세는 포함, 금액은 37만 원","title"],
] as const){
  const clause=label+'"'+literal+'"로 새로 정합니다.';
  test(field+" bound quote masks unrelated scalar and text fields",async()=>{
    const h=fixture();
    try{
      const parsed=parseExtra(clause,today);
      assert.deepEqual(parsed,{[field]:literal});
      const r=h.start(clause);
      assert.equal(r.rule_fields.find(f=>f.field===field)?.value,literal);
      assert.deepEqual(r.rule_fields.filter(f=>f.action==="set").map(f=>f.field),[field]);
      assert.deepEqual(Object.keys(h.holds),[field]);
      await h.reply([set(r,field,literal,clause),set(r,"amount",370000,"37만 원"),
        set(r,"vat","included","부가세는 포함"),set(r,foreign,field==="title"?"별도":"경첩 보완",
          field==="title"?"사유는 별도":"항목명은 경첩 보완")]);
      for(const k of ["amount","vat",foreign])assert.equal(offered(h.field(k)),false,k);
      assert.deepEqual(h.apply([field,"amount","vat",foreign]),{applied:true,patch:{[field]:literal}});
      assert.deepEqual(h.holds,{});
    }finally{h.session.dispose();}
  });
  for(const hostile of [false,true]){
    test(field+" independent numeric quote remains outside text literal / "+hostile,async()=>{
      const h=fixture(),text=clause+' 비용은 "41만 원"으로 정합니다. 부가세는 별도로 처리합니다.';
      try{
        assert.deepEqual(parseExtra(text,today),{[field]:literal,amount:410000,vat:"excluded"});
        const r=h.start(text);
        await h.reply([set(r,field,literal,clause),set(r,"amount",hostile?370000:410000,hostile?"37만 원":"41만 원"),
          set(r,"vat",hostile?"included":"excluded",hostile?"부가세는 포함":"부가세는 별도로 처리합니다")]);
        if(hostile){assertHeld(h,"amount");assertHeld(h,"vat");}
        assert.deepEqual(h.apply([field,"amount","vat"]),{applied:true,
          patch:{[field]:literal,...(hostile?{}:{amount:410000,vat:"excluded"})}});
      }finally{h.session.dispose();}
    });
  }
  test(field+" quoted scalar words do not invalidate verified prior carry",async()=>{
    const h=fixture();
    try{
      const t1=await h.seedAmount();
      const text=clause+" 비용 후보는 그대로 유지해 주세요.";
      await h.send(text,r=>[set(r,field,literal,clause),carry(r,"amount")]);
      assert.equal(h.field("amount").origin_request_id,t1.request_id);
      assert.deepEqual(h.apply([field,"amount"]),{applied:true,patch:{[field]:literal,amount}});
      assert.deepEqual(h.holds,{});
    }finally{h.session.dispose();}
  });
  for(const tail of ['"경첩 보완','"경첩 보완"으로 새로','경첩 보완으로 새']){
    test(field+" incomplete literal boundary "+tail,async()=>{
      const h=fixture(),text=label+tail;
      try{
        assert.equal(parseExtra(text,today)[field],undefined);
        await h.send(text,r=>[set(r,field,"경첩 보완",text)]);
        assertHeld(h,field);
        assert.deepEqual(h.apply([field]),{applied:false,patch:{}});
      }finally{h.session.dispose();}
    });
  }
}
