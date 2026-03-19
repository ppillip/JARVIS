# JARVIS

Python 백엔드와 Svelte 프론트엔드로 구성한 승인 기반 자비스형 챗봇입니다.

## 구조

- `backend/`
  FastAPI API 서버
- `frontend/`
  Svelte + Vite UI
- `soul.md`
  자비스의 말투와 태도 규칙

## 현재 기능

- 지령 입력
- 플랜 초안 생성
- 플랜 수정 요청
- 플랜 승인 후 태스크 생성
- OpenAI OAuth 로그인 상태 표시
- 태스크별 MCP 매핑 표시
- MCP 클릭 시 상세 정보 표시
- 태스크 순차 실행 시뮬레이션

## 로그인 설정

이 프로젝트의 로그인은 OpenClaw와 비슷한 방식의 `OpenAI GUI OAuth + 로컬 auth profile 저장` 구조입니다.

로그인 경로는 하나입니다.

1. `OpenAI 로그인`
   브라우저를 OpenAI 로그인 화면으로 보냅니다. 인증이 끝나면 토큰이 로컬 auth profile 저장소에 기록되고, 이후 앱이 그 프로필을 재사용해 세션을 유지합니다.

필수 환경변수:

```bash
export OPENAI_OAUTH_CLIENT_ID="your-openai-oauth-client-id"
```

선택 환경변수:

```bash
export OPENAI_OAUTH_REDIRECT_URI="http://localhost:1455/auth/callback"
export FRONTEND_APP_URL="http://127.0.0.1:5173"
export SESSION_SECRET="replace-this-in-real-use"
export OPENAI_API_KEY="your-openai-api-key"
```

또는 `backend/.env.example`를 복사해 `backend/.env`로 둘 수 있습니다.

주의:

- `OPENAI_OAUTH_REDIRECT_URI`는 OAuth redirect URI와 정확히 일치해야 합니다.
- 기본값은 OpenClaw/Codex CLI 스타일 loopback callback인 `http://localhost:1455/auth/callback`입니다.
- 로그인 중간 상태(`state`, `code_verifier`)도 loopback callback 호환을 위해 로컬 상태 파일에 보관합니다.
- 실제 세션 유지는 `~/.nicecodex/agent/auth-profiles.json`에 저장된 인증 프로필을 기준으로 이뤄집니다.
- 로그아웃은 현재 활성 프로필을 저장소에서 제거합니다.
- `OPENAI_OAUTH_CLIENT_ID`는 환경변수 또는 `backend/.env`로 주입해야 합니다.

## 실행 방법

### 1. 백엔드

```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload
```

기본 주소: `http://127.0.0.1:8000`

### 2. 프론트엔드

```bash
cd frontend
npm install
npm run dev
```

기본 주소: `http://127.0.0.1:5173`

Vite dev server는 `/api` 요청을 백엔드로 프록시합니다.

## 다음 확장 후보

- 실제 LLM API 연동
- 세션 저장과 복원
- 태스크별 실제 MCP 호출 로그
- 자유 입력형 플랜 수정 요청

## soul.md

채팅 처리 시 백엔드는 프로젝트 루트의 `soul.md`를 항상 읽어서 시스템 프롬프트와 함께 전달합니다.

예:

- `자비스는 항상 존댓말을 한다`
- `자비스는 사용자를 코칭하듯 말하지 않는다`
- `자비스는 항상 짧고 단호하게 말한다`

즉 말투나 태도를 바꾸고 싶으면 `soul.md`를 수정하면 됩니다.
