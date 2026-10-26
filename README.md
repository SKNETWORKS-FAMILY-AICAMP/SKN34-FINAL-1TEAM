# SKN34-FINAL-1TEAM

SK Networks Family AICAMP 34기 1팀 최종 프로젝트 저장소입니다.

현재는 개발 시작 전 공통 협업 설정을 준비한 상태입니다. 프로젝트 주제, 기술 스택, 배포 환경이 결정되면 서비스별 테스트와 배포 파이프라인을 연결합니다.

## 개발 흐름

1. `main`에서 작업 브랜치를 만듭니다. 예: `feat/login`, `fix/api-error`, `docs/architecture`
2. 작업 후 Pull Request를 생성합니다.
3. CI 통과 및 팀원 리뷰 후 `main`으로 병합합니다.
4. 배포 설정이 완료되기 전까지 자동 배포는 실행되지 않습니다.

## 현재 CI

Pull Request, `main` 푸시, 수동 실행 시 GitHub Actions에서 다음을 확인합니다.

- 잘못된 JSON 문법
- 남아 있는 Git 충돌 표시
- 텍스트 파일 끝의 불필요한 공백
- 실수로 추적된 `.env` 설정 파일

로컬에서도 동일한 검사를 실행할 수 있습니다.

```bash
python .github/scripts/check_repo.py
```

> 이 검사는 저장소 공통 점검입니다. 애플리케이션 단위의 린트·테스트·빌드는 기술 스택을 확정한 후 추가해야 합니다.

## 추후 연결

- 백엔드/프론트엔드 프레임워크별 설치, 린트, 테스트, 빌드
- 개발·운영 배포 환경과 GitHub Environments
- 배포용 Secret 및 OIDC 권한 설정
- DB 마이그레이션 및 배포 후 상태 확인

팀 작업 방법은 [CONTRIBUTING.md](CONTRIBUTING.md)를 참고하세요.
