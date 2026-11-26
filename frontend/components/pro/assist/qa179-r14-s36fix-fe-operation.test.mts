import assert from "node:assert/strict";
import test from "node:test";
import { requestIntent,resolvedIntentSegments,referencedPeriodExclusion } from "./requestIntent.ts";
import { parseExtra,extraTextGrounding,positiveExtraTextValue,readExplicitPeriodExclusions,readQuoteDuration,parseDates } from "./parse.ts";
import { ruleFields } from "./rules.ts";
import { relativeQuantityOperation,referencedExclusionAddOperation } from "./fieldOperation.ts";

const opts={today:"2027-01-01",baseMonth:"2027-02-01",crewMax:6,daysMax:120};
const byField=(form,text,current)=>Object.fromEntries(ruleFields(form,text,opts,current).map(f=>[f.field,f]));
test("relative operations distinguish operand from final current-based value",()=>{
  for(const [form,field,text,current,value] of [
    ["quote","days","기존 작업 일수에서 사흘 더 늘려 주시겠어요?",{days:5},8],
    ["quote","days","일정을 이틀 단축해서 견적 공사 일수를 조정해 주세요.",{days:9},7],
    ["dates","crew","지금 인원에 두 명 더 추가해서 보내 주세요.",{crew:3},5],
    ["dates","crew","현재 인원에서 한 명 줄여 주세요.",{crew:4},3],
    ["quote","days","공사 일수 4일 연장해 주세요.",{days:3},7],
  ] as const){
    const op=relativeQuantityOperation(form,text,field,current,opts);
    assert.equal(op?.value,value,text);
    const rule=byField(form,text,current)[field];
    assert.equal(rule.value,value,text);
    assert.equal(rule.value_source,"derived");
    assert.equal(byField(form,text,undefined)[field].action,"keep");
  }
  assert.equal(relativeQuantityOperation("quote","일수를 4일로 연장해 주세요.","days",{days:3},opts),undefined);
  assert.equal(relativeQuantityOperation("dates","투입 인원 2명 더 추가해 주세요.","crew",{crew:5},opts)?.value,undefined);
  assert.equal(relativeQuantityOperation("dates","투입 인원 2명 더 추가해 주세요.","crew",{crew:3},opts,true)?.value,undefined);
  assert.equal(relativeQuantityOperation("quote","일수를 이틀 단축하지 마세요.","days",{days:5},opts)?.value,undefined);
});
test("reason field owns descriptive labels, including the slot noun and quoted title-like words",()=>{
  for(const [text,reason] of [
    ["추가 작업을 하게 된 경위란에 '매립 배선 위치 변경'이라고 남겨 주십시오.","매립 배선 위치 변경"],
    ["왜 이 공사가 더 들어갔는지 쓰는 칸에는 '고객의 방음 요청'이라고 넣어 주세요.","고객의 방음 요청"],
    ["사유 항목에는 '공사명 표기 오류에 따른 재확인'을 넣어 주세요.","공사명 표기 오류에 따른 재확인"],
  ]){
    const parsed=parseExtra(text,opts.today);
    assert.equal(parsed.reason,reason,text);
    assert.equal(parsed.title,undefined,text);
    assert.equal(byField("extra",text,{}).reason.value,reason,text);
    assert.ok(positiveExtraTextValue(text,"reason",reason,text,opts.today),text);
    assert.ok(!positiveExtraTextValue(text,"title",reason,text,opts.today),text);
  }
});
test("independent text operations do not turn contextual narration into another candidate",()=>{
  const text="방문 일정이 밀렸지만 사유란에는 '방진 설비 추가'라고 써 주세요. 제목은 '집진기 설치'로 바꾸죠.";
  const parsed=parseExtra(text,opts.today);
  assert.equal(parsed.reason,"방진 설비 추가");
  assert.equal(parsed.title,"집진기 설치");
  for(const [text,field,value] of [
    ["현장 치수가 달라서 추가 공사비를 76만원으로 바꿔 주세요.","reason","현장 치수가 달라서"],
    ["실내 도장 건인데 추가 공사비는 97만원으로 바꿔 주세요.","title","실내 도장"],
    ["창호 주변 보강이 필요해요. 이 추가 항목 이름을 '창호 기밀 보강'으로 해 주세요.","reason","창호 주변 보강이 필요해요"],
  ]){
    assert.equal(positiveExtraTextValue(text,field as "title"|"reason",value,value,opts.today),false,text);
  }
});
test("postposed exclusion list owns all members without creating boundaries",()=>{
  for(const [text,dates] of [
    ["입주자 행사 때문에 1월 10일, 18일, 25일을 제외일 전체로 설정해 주세요.",["2027-01-10","2027-01-18","2027-01-25"]],
    ["1월 11일과 1월 23일만 공사 제외일로 설정해 주세요.",["2027-01-11","2027-01-23"]],
  ] as const){
    assert.deepEqual([...requestIntent("period",text).requested],["excluded"],text);
    assert.deepEqual(readExplicitPeriodExclusions(text,opts.today,opts.baseMonth)?.dates,dates,text);
    const rules=byField("period",text,{});
    assert.equal(rules.start.action,"keep");assert.equal(rules.end.action,"keep");
    assert.deepEqual(rules.excluded.value,dates);
  }
});
test("referenced exclusion add preserves existing list; member move is not a literal replacement",()=>{
  const text="관리실에서 2월 18일 하루는 안 된다고 하네요. 제외일로 그날만 추가해 주세요.";
  assert.equal(referencedPeriodExclusion(text)?.kind,"add");
  assert.deepEqual([...requestIntent("period",text).requested],["excluded"]);
  assert.deepEqual(referencedExclusionAddOperation("period",text,"excluded",{excluded:["2027-02-10"]},opts)?.value,["2027-02-10","2027-02-18"]);
  const move="첫 방문 2월 9일만 2월 12일로 밀어 주세요. 두 번째 날짜는 기존대로요.";
  assert.ok(requestIntent("dates",move).replacements.has("days"));
  assert.equal(byField("dates",move,{days:["2027-02-09","2027-02-20"]}).days.action,"keep");
});
test("visit-day hold and quote deadline aliases retain independent owners",()=>{
  const text="방문일은 고객과 협의 중이라 보류하고, 투입 인원은 5명으로 확정해 주세요.";
  const i=requestIntent("dates",text);
  assert.ok(i.requested.has("days")&&i.blocked.has("days"));
  assert.ok(i.requested.has("crew")&&!i.blocked.has("crew"));
  assert.equal(byField("dates",text,{}).crew.value,5);
  const quote="합의한 금액은 변경하지 않습니다. 견적 마감 날짜를 2월 24일로 연장해 주십시오.";
  assert.ok(requestIntent("quote",quote).requested.has("valid"));
  assert.equal(byField("quote",quote,{}).valid.value,"2027-02-24");
});
