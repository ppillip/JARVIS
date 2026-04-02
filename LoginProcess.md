# Login Process Guide

이 문서는 JARVIS의 현재 OpenAI 로그인 흐름을 구현 기준으로 정리한 문서입니다.
목표는 다른 에이전트나 다른 툴이 들어와도, 로그인 구조를 잘못 건드리지 않고 같은 방식으로 이해하고 유지보수할 수 있게 하는 것입니다.

기준 소스:

- [`backend/app/main.py`](/Users/ppillip/Projects/NiceCodex/backend/app/main.py)
- [`frontend/src/App.svelte`](/Users/ppillip/Projects/NiceCodex/frontend/src/App.svelte)

## 1. 포트 정책

JARVIS는 로그인 관련 구성까지 포함해 기본적으로 `7000`번대 포트를 사용합니다.

- 메인 백엔드: `7300`
- 프론트엔드: `7400`
- OAuth loopback callback: `1455`

주의:

- loopback callback은 OpenAI/Codex 호환을 위해 예외적으로 `1455`를 사용합니다.
- 메인 앱은 보통 `127.0.0.1:7300`, loopback callback은 기본적으로 `localhost:1455`입니다.

## 2. 목표

- OpenAI GUI 로그인 화면을 사용한다.
- 로그인 후 토큰을 로컬 auth profile 저장소에 보관한다.
- 앱 재시작 후에도 인증 상태를 복원한다.
- OpenClaw / Codex CLI와 유사한 loopback callback 흐름을 사용한다.
- 브라우저 세션에는 최소 정보만 두고, 실제 인증 지속성은 로컬 저장소가 담당한다.

## 3. 현재 결론

브라우저 쿠키 세션만으로는 안정적으로 구현되지 않았습니다.

현재 정상 동작하는 방식은 다음입니다.

1. 앱이 OpenAI OAuth authorize URL로 리다이렉트한다.
2. redirect URI는 기본적으로 `http://localhost:1455/auth/callback`를 사용한다.
3. 로그인 중간 상태(`state`, `code_verifier`)는 브라우저 세션이 아니라 로컬 파일에 저장한다.
4. callback에서 authorization code를 access token / refresh token으로 교환한다.
5. 토큰은 `auth-profiles.json` 형태로 저장한다.
6. 이후 인증 상태 조회는 저장된 auth profile을 읽고, 필요하면 refresh token으로 갱신한다.

## 4. 왜 이 구조가 필요한가

처음에는 일반적인 웹앱처럼 다음 구조를 시도할 수 있습니다.

- 시작: `http://127.0.0.1:7300/api/auth/openai`
- callback: `http://127.0.0.1:7300/api/auth/openai/callback`
- `state`, `code_verifier`는 브라우저 세션 쿠키에 저장

이 방식은 OpenAI 인증 화면에서 안정적으로 동작하지 않았고, Codex/OpenClaw 스타일 흐름과도 맞지 않았습니다.

그 다음에는 callback을 `localhost:1455`로 바꾼 loopback 방식을 사용했습니다.

- 시작: `127.0.0.1:7300`
- callback: `localhost:1455`

이때 핵심 문제는 브라우저 쿠키 스코프입니다.

- `127.0.0.1`과 `localhost`는 브라우저 입장에서 같은 쿠키 스코프가 아닙니다.
- 따라서 시작 시점의 세션 쿠키가 callback 시점에 그대로 복원되지 않습니다.
- 결과적으로 `state` 검증 실패나 인증 완료 후 앱 상태 복원 실패가 발생할 수 있습니다.

그래서 현재 원칙은 다음과 같습니다.

- loopback callback을 쓸 때 로그인 중간 상태를 브라우저 쿠키에 의존하지 않는다.
- 중간 상태는 로컬 파일 저장소로 관리한다.

## 5. 현재 구현 구조

### 백엔드 파일

- [`backend/app/main.py`](/Users/ppillip/Projects/NiceCodex/backend/app/main.py)

### 프론트 파일

- [`frontend/src/App.svelte`](/Users/ppillip/Projects/NiceCodex/frontend/src/App.svelte)

### 설정 예시

- [`backend/.env.example`](/Users/ppillip/Projects/NiceCodex/backend/.env.example)

## 6. 핵심 엔드포인트

### 1. 로그인 시작

- `GET /api/auth/openai`

역할:

- loopback callback 서버를 보장 기동한다.
- OpenAI authorize URL을 생성한다.
- PKCE verifier / challenge를 만든다.
- pending OAuth 상태를 로컬 파일에 저장한다.
- 브라우저를 OpenAI GUI 로그인 화면으로 리다이렉트한다.

현재 주요 authorize 파라미터:

- `response_type=code`
- `client_id`
- `redirect_uri=http://localhost:1455/auth/callback` 기본값
- `scope=openid profile email offline_access api.connectors.read api.connectors.invoke`
- `state`
- `code_challenge`
- `code_challenge_method=S256`
- `id_token_add_organizations=true`
- `codex_cli_simplified_flow=true`
- `originator=codex_cli_rs`

### 2. OAuth callback

- `GET /auth/callback`
- `GET /api/auth/openai/callback`

역할:

- OpenAI가 돌려보낸 authorization code를 받는다.
- pending OAuth 파일에 저장된 `state`, `verifier`를 검증한다.
- token endpoint에 code exchange를 수행한다.
- auth profile을 저장한다.
- 브라우저 세션에 최소 인증 참조를 기록한다.
- 프론트 앱으로 다시 리다이렉트한다.

현재 기본 프론트 복귀 URL:

- `http://127.0.0.1:7400/?auth=complete`

### 3. 인증 상태 조회

- `GET /api/auth/status`

역할:

- 현재 세션의 `profile_id`를 읽는다.
- 로컬 auth profile 저장소에서 해당 profile을 찾는다.
- access token 만료 여부를 확인한다.
- 필요하면 refresh token으로 access token을 갱신한다.
- 프론트에 인증 상태를 반환한다.

응답에는 다음 정보가 포함됩니다.

- `authenticated`
- `provider`
- `profile_id`
- `account_id`
- `email`
- `name`
- `expires_at`
- `error`

### 4. 로그아웃

- `POST /api/auth/logout`

역할:

- 현재 세션의 `auth` 참조를 제거한다.
- `auth_error`를 정리한다.
- 현재 활성 profile id가 있으면 로컬 auth profile 저장소에서도 제거한다.

주의:

- 현재 구현은 단순 세션 로그아웃이 아니라, 해당 profile 자체를 저장소에서 제거합니다.
- 즉 다음 새로고침 때 자동 복원되지 않게 하려는 정책입니다.

## 7. 로컬 저장소 구조

### 1. pending OAuth 상태

파일:

- `~/.nicecodex/agent/pending-oauth.json`
- lock: `~/.nicecodex/agent/pending-oauth.lock`

용도:

- 로그인 시작 시점의 `state`
- PKCE `code_verifier`
- 생성 시간

예시:

```json
{
  "state": "random-state",
  "verifier": "random-verifier",
  "created_at": 1773500000
}
```

### 2. auth profiles

파일:

- `~/.nicecodex/agent/auth-profiles.json`
- lock: `~/.nicecodex/agent/auth-profiles.lock`

용도:

- access token / refresh token 저장
- 현재 재사용 가능한 OpenAI 인증 프로필 목록 저장
- provider별 우선순위 유지

예시:

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

## 8. 브라우저 세션에 저장하는 값

브라우저 세션이 실제 토큰을 보관하는 구조는 아닙니다.

현재 세션에는 최소 정보만 둡니다.

- `auth.provider`
- `auth.profile_id`
- `auth_error`

실제 인증 지속성은 auth profile 저장소가 담당합니다.

즉:

- 브라우저 새로고침 후에도 profile이 있으면 복원 가능합니다.
- access token이 만료돼도 refresh token이 있으면 갱신 가능합니다.

## 9. 토큰 갱신 방식

`/api/auth/status`와 실제 chat 진입 전 credential 획득 시 다음 순서로 처리합니다.

1. 현재 활성 profile 확인
2. 저장된 `expires` 확인
3. 아직 유효하면 그대로 사용
4. 만료 임박 또는 만료라면 refresh token으로 token endpoint 호출
5. 새 토큰으로 auth profile 저장소 갱신

중요 구현 포인트:

- refresh 응답에 refresh token이 다시 안 올 수 있으므로 기존 refresh token을 보존해야 합니다.
- refresh 응답에 email/accountId가 빠질 수 있으므로 기존 메타데이터를 보존해야 합니다.
- 새 token_data로 만든 profile의 `profileId`는 기존 profile id를 유지합니다.

## 10. loopback 서버

현재 구현은 메인 FastAPI 앱 외에 별도 loopback 앱을 백그라운드로 띄웁니다.

- 앱 이름: `loopback_app`
- 호스트 기본값: `localhost`
- 포트 기본값: `1455`
- 경로: `/auth/callback`

기동 방식:

- `/api/auth/openai` 진입 시 `ensure_loopback_server()`가 한 번만 기동합니다.
- 내부적으로 daemon thread에서 uvicorn server를 실행합니다.

핵심 포인트:

- 메인 앱은 `127.0.0.1:7300`
- callback 앱은 `localhost:1455`
- 이 구조에서는 동일 브라우저 세션 쿠키를 그대로 기대하면 안 됩니다.

## 11. OAuth URL 관련 구현 포인트

로그인이 실패했을 때 가장 먼저 확인할 항목:

1. `OPENAI_OAUTH_CLIENT_ID`
2. `OPENAI_OAUTH_REDIRECT_URI`
3. `FRONTEND_APP_URL`
4. authorize URL에 포함한 특수 파라미터

현재 중요한 값:

- `OPENAI_OAUTH_CLIENT_ID`
- `OPENAI_OAUTH_REDIRECT_URI`
  기본값: `http://localhost:1455/auth/callback`
- `OPENAI_OAUTH_LOOPBACK_HOST`
  기본값: `localhost`
- `OPENAI_OAUTH_LOOPBACK_PORT`
  기본값: `1455`
- `FRONTEND_APP_URL`
  기본값: `http://127.0.0.1:7400`
- `SESSION_SECRET`

authorize URL에 포함하는 중요한 값:

- `id_token_add_organizations=true`
- `codex_cli_simplified_flow=true`
- `originator=codex_cli_rs`

## 12. 프론트 동작

프론트는 `App.svelte`에서 다음 흐름으로 로그인 상태를 반영합니다.

1. 초기 mount 시 `/api/auth/status` 호출
2. URL 쿼리에 `auth=complete`가 있으면 다시 `/api/auth/status` 호출
3. 로그인되지 않았으면 `OpenAI 로그인` 버튼 표시
4. 로그인되면 이메일 또는 account id와 함께 연결 상태 표시
5. 로그아웃 버튼으로 `/api/auth/logout` 호출

즉 프론트는 토큰을 직접 다루지 않고, 백엔드 세션/상태 API만 사용합니다.

## 13. 민감한 주의사항

- 토큰 원문은 브라우저에 저장하지 않습니다.
- `pending-oauth.json`과 `auth-profiles.json`은 로컬 민감 정보 파일입니다.
- 로그/문서/스크린샷에 access token, refresh token이 노출되지 않게 해야 합니다.
- logout은 현재 profile을 저장소에서 제거하므로, 단순 UI 로그아웃과 의미가 다릅니다.
- callback host와 app host가 다를 수 있으므로 쿠키 기반 state 복원 가정은 위험합니다.

## 14. 다른 에이전트를 위한 유지보수 원칙

다른 에이전트가 이 로그인 구조를 수정할 때는 다음 순서를 지켜야 합니다.

1. redirect URI와 loopback host/port를 먼저 확인한다.
2. pending OAuth 상태가 브라우저 세션이 아니라 파일 저장소에 있다는 점을 유지한다.
3. `auth-profiles.json` 구조를 함부로 바꾸지 않는다.
4. logout 정책이 profile 삭제라는 점을 알고 변경 여부를 신중히 판단한다.
5. 변경 전후로 최소한 다음을 확인한다.
   - `/api/auth/openai`
   - `/auth/callback`
   - `/api/auth/status`
   - `/api/auth/logout`
