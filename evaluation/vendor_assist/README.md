# 업체 도우미 #178 고정 평가

기존 `parse.ts` 규칙과 `assist_service.interpret` 하이브리드 응답을 같은 문장·문맥에서 비교한다. 평가 코드에는 별도 자연어 추출 규칙이 없다.

## 데이터와 검수 상태

| 화면 | dev | heldout | 전체 |
| --- | ---: | ---: | ---: |
| 실측 가능 기간(`period`) | 24 | 12 | 36 |
| 실측 견적서(`quote`) | 24 | 12 | 36 |
| 공사 날짜·인원(`dates`) | 24 | 12 | 36 |
| 추가 금액(`extra`) | 24 | 12 | 36 |
| **합계** | **96** | **48** | **144** |

**현재 144개 정답은 모두 AI가 작성한 검수 전 초안이다.** 모든 줄의 `gold_origin`은 `ai_draft`, `human_verified`는 `false`, `reviewed_by`는 `null`이다. 기존 QA 문장을 포함하되, 외부 사람이 원문·의도·필드별 기대값을 확인하지 않았다. 여기서 나온 수치는 모델 품질 승인 또는 실사용 정확도의 근거가 아니다.

각 줄은 `id`, `split`, `form`, `kind`, `tags`, `text`, `today`, `base_month`, `current`, `previous_candidate`, `context`, `expected`를 가진다. `expected`는 **화면의 모든 필드**에 `action`(`keep|set|clear|uncertain`)과 `value`를 명시한다. `set` 외 액션의 값은 `null`이다. 이전 후보는 이전 원문과 원문에 실제 등장한 근거를 포함한다. 날짜 기준은 2026-11-23, 기준 월은 2026-12-01로 고정했다.

문장 종류 12가지(`simple`, `multi_field`, `partial`, `negation`, `double_negation`, `correction`, `previous_correction`, `previous_keep`, `clear`, `ambiguous`, `conflict`, `no_action`)가 화면마다 3개씩 있다. 금액·기간 정정, 부가세 부정, 여러 날 제외 사례는 추가 `tags`로도 분류한다.

`make_dataset.py`의 각 사례와 기대 액션은 검수·수정 전까지 변하지 않도록 보관한다. 재생성 명령은 다음과 같다.

```bash
python evaluation/vendor_assist/make_dataset.py
python -m pytest -q evaluation/tests/test_vendor_assist_eval.py
```

사람이 직접 검토할 때는 문장에 대해 실제 원하는 폼 변화를 확인하고 정답과 검수 상태를 함께 갱신해야 한다. AI 초안을 임의로 `human_verified=true`로 바꾸지 않는다.

## 실행

저장소 루트에서 실행한다. Node는 저장소 요구 버전(24.18 이상)을 권장한다. 22.23에서는 `--experimental-strip-types`로 확인했다.

```bash
# 기본: 로컬 TS 규칙 파서, dev, 유료 호출 0회
python evaluation/vendor_assist/run.py --mode rule --split dev

# #176의 dates/extra 코드가 아직 내 checkout에 통합되지 않았을 때만 사용
python evaluation/vendor_assist/run.py --mode rule --split dev --parser-path ../vendor-assist-ui/frontend/components/pro/assist/parse.ts

# 서버 순수 해석 서비스 경유, 오프라인 스텁/규칙 대체 (실제 LLM 품질 측정 아님)
python evaluation/vendor_assist/run.py --mode hybrid --split dev

# held-out은 최종 평가에서만 허가한다
python evaluation/vendor_assist/run.py --mode rule --split heldout --final

# 상세 결과 JSON 저장
python evaluation/vendor_assist/run.py --mode rule --split dev --json evaluation/vendor_assist/rule-dev-report.json
```

`hybrid`는 `backend/app/vendor/assist_schemas.py`, `assist_service.py`, `agent/vendor_assist.py`가 통합된 환경에서 사용한다. 실제 업무 API 라우터를 호출하지 않고, 고정 UUID·날짜·제약으로 `AssistRequest`와 `AssistContext`를 만들어 서비스의 `interpret`를 호출한다. 규칙 후보는 같은 TS 파서의 출력으로 넣는다. `--parser-path`는 다른 체크아웃의 기존 파서를 읽기만 하며, 평가 파일을 수정하지 않는다.

`--live`를 주지 않으면 외부 `LLM_LIVE=true` 환경변수가 있어도 `hybrid` 실행에서 강제로 `LLM_LIVE=false`가 된다. **유료 호출은 명시적인 `--mode hybrid --live`에서만 가능하다.** 현재처럼 사람이 검수하지 않은 dev 문장으로 진단할 경우 `--allow-unreviewed`까지 명시해야 한다. `heldout`에는 이 옵션을 사용할 수 없다. 실모델 진단 수치도 정답 검수가 끝나기 전까지 최종 성능이라고 보고하지 않는다.

```bash
# 사람이 아직 검수하지 않은 dev의 유료 진단 허가 (사용자가 비용 확인 후 수동 실행)
python evaluation/vendor_assist/run.py --mode hybrid --split dev --live --allow-unreviewed
```

## 지표 의미

| 지표 | 계산 |
| --- | --- |
| 필드 정확도 | 필드별 `action`과 `value`를 함께 맞힌 수 / 전체 필드 수 |
| 문장 전체 정확도 | 화면의 모든 필드를 맞힌 문장 수 / 문장 수 |
| 잘못된 후보율 | 실제 적용 가능한 `set|clear` 중 정답 액션·값과 다른 후보 수 / 반환한 적용 가능 후보 수 |
| 미추출률 | 정답 `set|clear` 중 동일한 액션·값을 반환하지 못한 수 / 정답 적용 가능 필드 수 |
| 부정·정정·복합 정확도 | `kind`, `tags`별 문장 전체 정확도 |
| 응답 지연 | 문장별 실제 파서 또는 서비스 소요의 평균·p50·p95 |
| 토큰·비용 | 실제 수집된 입력·출력·캐시 토큰과 사용자가 명시한 토큰 요금만 사용 |
| 서비스 오류·시간초과 | 서비스 응답 자체가 없는 사례, 유효하지 않은 출력 또는 평가 제한 시간초과 건수 |
| 모델 경로 오류·시간초과 | 서비스가 규칙 후보로 복구한 경우에도 내부 LLM 호출에서 실제 발생한 예외/시간초과 건수 |

잘못된 `set` 값은 **오후보와 올바른 값 미추출에 동시에** 포함한다. `keep`은 삭제가 아니다. 충돌·유효하지 않은 후보는 자동 적용 후보로 세지 않는다. 필드별 정확도는 미언급 `keep`의 영향을 받으므로 문장 전체 정확도와 오후보·미추출률을 함께 비교한다. 날짜 배열은 순서만 무시하고, 중복 날짜는 허용하지 않는다. `source=rule`은 LLM 비활성·실패 시 대체·규칙 채택을 모두 포함하므로, 별도 `llm_error`, `llm_timeout`, `adapter_invoked`와 `llm_live` 기록으로 구분한다. `adapter_invoked`는 SDK 어댑터에 진입한 건수이며 유료 네트워크 요청 수를 뜻하지 않는다.

요금은 모델·가격이 확인됐을 때에만 다음 인자를 직접 입력한다: `--input-price`, `--output-price`, `--cached-price`(각각 **100만 토큰당 USD**). 가격이 누락되거나 사용량이 미계측이면 **비용은 `null`**이다. 입력·출력·캐시 토큰 값도 수집하지 못하면 `null`이며, 추정값을 0으로 채우지 않는다. 서비스의 `llm.structured` / `vendor_assist.interpret` 선택적 `usage` 인자를 통해 계측한다.

`--data`로 파일 이름을 변경해 지정해도 내부 `split=heldout`이면 `--final` 없이는 실행되지 않는다. dev 결과로 프롬프트·코드를 수정한 뒤, held-out은 최종 고정 버전으로 한 번만 확인한다.
