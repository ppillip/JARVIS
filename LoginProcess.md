# Login Process Guide

이 문서는 `JARVIS`에서 구현한 OpenAI 로그인 흐름을 정리한 개발 가이드다.
목표는 다음 프로젝트, 특히 안티그래비티 기반 구현에서 에이전트가 같은 문제를 다시 겪지 않고 그대로 재사용할 수 있게 하는 것이다.

## 포트 정책

JARVIS는 로그인 관련 구성까지 포함해 무조건 `7000`번대 포트만 사용한다.

- 메인 앱: `7300`
- 프론트엔드: `7400`
- OAuth loopback callback: `7500`

## 목표

- OpenAI GUI 로그인 화면을 사용한다.
- 로그인 후 토큰을 로컬 auth profile 저장소에 보관한다.
- 앱 재시작 후에도 인증 상태를 복원한다.
- OpenClaw/Codex CLI와 유사한 loopback callback 흐름을 사용한다.

## 결론

브라우저 쿠키 세션만으로는 안정적으로 구현되지 않았다.

정상 동작한 방식은 다음이다.

1. 앱이 OpenAI OAuth authorize URL로 리다이렉트한다.
2. redirect URI는 `http://localhost:7500/auth/callback`를 사용한다.
3. 로그인 중간 상태(`state`, `code_verifier`)는 브라우저 세션이 아니라 로컬 파일에 저장한다.
4. callback에서 authorization code를 access token / refresh token으로 교환한다.
5. 토큰은 `auth-profiles.json` 형태로 저장한다.
6. 이후 인증 상태 조회는 저장된 auth profile을 읽고, 필요하면 refresh token으로 갱신한다.

## 왜 이 구조가 필요한가

처음에는 일반적인 웹앱처럼 다음 구조를 시도했다.

- 시작: `http://127.0.0.1:7300/api/auth/openai`
- callback: `http://127.0.0.1:7300/api/auth/openai/callback`
- `state`, `code_verifier`는 브라우저 세션 쿠키에 저장

이 방식은 OpenAI 인증 화면에서 `unknown_error`가 발생했다.

그 다음에는 OpenClaw/Codex CLI 스타일로 callback을 `localhost:7500`로 바꿨다.

- 시작: `127.0.0.1:7300`
- callback: `localhost:7500`

이때 또 문제가 생겼다.

- `127.0.0.1`과 `localhost`는 브라우저 입장에서 같은 쿠키 스코프가 아니다.
- 따라서 시작 시점에 저장한 세션 쿠키가 callback 시점에 복원되지 않았다.
- 결과적으로 `state` 검증이 실패하거나, 인증 완료 후에도 앱 상태가 복원되지 않았다.

그래서 최종적으로 다음 원칙이 필요했다.

- loopback callback을 쓸 때 로그인 중간 상태를 브라우저 쿠키에 의존하지 말 것
- 중간 상태는 로컬 파일 저장소로 옮길 것

## 현재 구현 구조

### 백엔드 파일

- [backend/app/main.py](/Users/ppillip/Projects/NiceCodex/backend/app/main.py)

### 프론트 파일

- [frontend/src/App.svelte](/Users/ppillip/Projects/NiceCodex/frontend/src/App.svelte)

### 설정 예시

- [backend/.env.example](/Users/ppillip/Projects/NiceCodex/backend/.env.example)

## 핵심 엔드포인트

### 1. 로그인 시작

- `GET /api/auth/openai`

역할:

- OpenAI authorize URL 생성
- PKCE verifier / challenge 생성
- pending OAuth 상태를 로컬 파일에 저장
- 브라우저를 OpenAI GUI 로그인 화면으로 이동

핵심 파라미터:

- `response_type=code`
- `client_id`
- `redirect_uri=http://localhost:7500/auth/callback`
- `scope=openid profile email offline_access`
- `state`
- `code_challenge`
- `code_challenge_method=S256`
- `id_token_add_organizations=true`
- `codex_cli_simplified_flow=true`

### 2. loopback callback

- `GET /auth/callback`

역할:

- OpenAI가 브라우저를 돌려보내는 loopback callback
- authorization code를 token으로 교환
- auth profile 저장
- 프론트 앱으로 다시 리다이렉트

### 3. 인증 상태 조회

- `GET /api/auth/status`

역할:

- 현재 활성 auth profile 읽기
- access token 만료 여부 확인
- 필요하면 refresh token으로 새 access token 발급
- 프론트에 인증 상태 반환

### 4. 로그아웃

- `POST /api/auth/logout`

역할:

- 현재 활성 profile을 저장소에서 제거
- 브라우저 세션의 active profile 참조 제거

## 로컬 저장소 구조

### 1. pending OAuth 상태

파일:

- `/Users/ppillip/.nicecodex/agent/pending-oauth.json`

용도:

- 로그인 시작 시점의 `state`
- PKCE `code_verifier`
- 생성 시간

예시 형태:

```json
{
  "state": "random-state",
  "verifier": "random-verifier",
  "created_at": 1773500000
}
```

### 2. auth profiles

파일:

- `/Users/ppillip/.nicecodex/agent/auth-profiles.json`

용도:

- access token / refresh token 저장
- 현재 재사용 가능한 OpenAI 인증 프로필 목록 저장
- provider별 우선순위 유지

예시 형태:

```json
{
  "profiles": {
    "openai-codex:user@example.com": {
      "profileId": "openai-codex:user@example.com",
      "credential": {
        "type": "oauth",
        "provider": "openai-codex",
        "access": "access-token",
        "refresh": "refresh-token",
        "expires": 1773509999000,
        "email": "user@example.com",
        "accountId": "account-id"
      }
    }
  },
  "order": {
    "openai-codex": [
      "openai-codex:user@example.com"
    ]
  }
}
```

## 세션 유지 방식

브라우저 세션이 실제 토큰을 보관하는 구조가 아니다.

브라우저 세션은 최소한만 보관한다.

- 현재 활성 profile id
- 마지막 오류 메시지

실제 인증 지속성은 auth profile 저장소가 담당한다.

즉:

- 브라우저 새로고침 후에도 auth profile이 있으면 복원 가능
- access token이 만료돼도 refresh token이 있으면 갱신 가능

## 토큰 갱신 방식

`/api/auth/status`에서 다음 순서로 처리한다.

1. 현재 활성 profile 확인
2. 저장된 `expires` 확인
3. 아직 유효하면 그대로 사용
4. 만료 임박 또는 만료라면 refresh token으로 token endpoint 호출
5. 새 토큰으로 auth profile 저장소 갱신

중요:

- refresh 응답에 refresh token이 다시 안 올 수 있으므로 기존 refresh token을 보존해야 한다
- refresh 응답에 email/accountId가 빠질 수 있으므로 기존 메타데이터를 보존해야 한다

## loopback 서버

현재 구현은 메인 FastAPI 앱 외에 별도 loopback 앱을 띄운다.

- 호스트: `localhost`
- 포트: `7500`
- 경로: `/auth/callback`

중요 포인트:

- 메인 앱이 `127.0.0.1:7300`
- callback 앱이 `localhost:7500`

이 구조에서는 브라우저 쿠키를 동일 세션으로 볼 수 없다.
그래서 pending OAuth 상태를 파일에 저장한 것이다.

## OAuth URL 관련 구현 포인트

로그인이 실패했을 때 가장 먼저 확인할 항목:

1. `redirect_uri`
2. `client_id`
3. authorize URL에 추가된 특수 파라미터

현재 구현에서 중요한 값:

- `client_id`: 환경변수 `OPENAI_OAUTH_CLIENT_ID`로 주입
- 기본 `redirect_uri`: `http://localhost:7500/auth/callback`

authorize URL에 반드시 포함한 값:

- `id_token_add_organizations=true`
- `codex_cli_simplified_flow=true`

이 값들은 Codex CLI/OpenClaw 스타일과 더 가깝게 맞추기 위해 포함했다.

## 프론트 동작

프론트는 복잡한 인증 로직을 직접 처리하지 않는다.

역할은 단순하다.

1. `OpenAI 로그인` 버튼 클릭
2. 브라우저를 `/api/auth/openai`로 이동
3. callback 후 프론트로 돌아오면 `/api/auth/status` 재조회
4. 로그인 상태 카드 갱신

즉 실제 인증 로직은 전부 백엔드가 담당한다.

## 안티그래비티 프로젝트에 적용할 때의 권장 원칙

### 유지해야 할 것

- OpenAI GUI 로그인
- loopback callback
- PKCE
- pending OAuth를 파일에 저장
- auth profile 저장소 기반 세션 복원
- refresh token 기반 자동 갱신

### 바꿔도 되는 것

- 프론트 UI 프레임워크
- auth profile 저장 경로
- profile JSON 스키마 세부 필드명
- 활성 profile 선택 UX

### 가능하면 유지할 것

- `localhost:7500/auth/callback`
- auth profile 파일 기반 지속성
- provider key: `openai-codex`

## 구현 순서 체크리스트

1. OpenAI authorize URL 생성
2. PKCE verifier/challenge 생성
3. pending OAuth 파일 저장
4. loopback callback 서버 실행
5. callback에서 code/state 수신
6. pending OAuth 파일과 state 대조
7. token endpoint 호출
8. auth profile JSON 저장
9. `/api/auth/status`에서 프로필 복원
10. refresh token 갱신 구현
11. 로그아웃 시 현재 profile 제거

## 디버깅 체크리스트

### 증상: OpenAI 화면에서 `unknown_error`

확인 순서:

1. authorize URL의 `redirect_uri`가 정확한가
2. `localhost`와 `127.0.0.1`를 섞고 있지 않은가
3. `id_token_add_organizations=true`가 들어가는가
4. `codex_cli_simplified_flow=true`가 들어가는가
5. callback 포트 `7500`가 실제로 열려 있는가

### 증상: 로그인 후 앱이 미연결 상태

확인 순서:

1. callback 요청이 실제로 들어왔는가
2. pending OAuth 파일에 state/verifier가 저장됐는가
3. callback 시 state 검증이 통과했는가
4. `auth-profiles.json`에 profile이 저장됐는가
5. `/api/auth/status`가 저장된 profile을 읽는가

### 증상: 로그아웃 후 다시 로그인된 것처럼 보임

확인 순서:

1. 브라우저 세션만 지운 것이 아닌가
2. auth profile 저장소에서 현재 profile을 제거했는가
3. `/api/auth/status`가 저장소를 다시 읽어 복원하고 있지 않은가

## 현재 프로젝트 기준 주의사항

- 이 구현은 OpenAI의 일반적인 공개 OAuth 가이드라기보다 OpenClaw/Codex CLI 흐름에 맞춘 호환 구현이다.
- 실제 `client_id`는 코드나 문서에 하드코딩하지 말고 환경변수로만 주입한다.
- 향후 OpenAI 측 흐름이 바뀌면 가장 먼저 authorize URL 파라미터와 redirect URI 정책을 다시 확인해야 한다.

## 참고 파일

- [backend/app/main.py](/Users/ppillip/Projects/NiceCodex/backend/app/main.py)
- [frontend/src/App.svelte](/Users/ppillip/Projects/NiceCodex/frontend/src/App.svelte)
- [backend/.env.example](/Users/ppillip/Projects/NiceCodex/backend/.env.example)
- [README.md](/Users/ppillip/Projects/NiceCodex/README.md)
