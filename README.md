# JARVIS

Python 백엔드와 Svelte 프론트엔드로 구성한 승인 기반 자비스형 챗봇입니다.

## 포트 정책

JARVIS는 앞으로 무조건 `7000`번대 포트만 사용합니다.

- MCP Registry: `7100`
- Planner MCP: `7200`
- Main Backend: `7300`
- Frontend: `7400`
- LLM Bridge: `7600`
- OAuth loopback callback: `1455` (OpenAI/Codex 호환 예외)

다른 프로젝트 에이전트는 이 포트대를 피해서 사용해야 합니다.
단, OpenAI GUI OAuth는 현재 Codex 호환을 위해 `1455` loopback callback을 사용합니다.

## 구조

- `backend/`
  FastAPI API 서버
- `backend/app/agent_runtime.py`
  에이전트 런타임 공통 인터페이스
- `backend/app/deepagents_runtime.py`
  Deep Agents 연동 대상 경계
- `backend/app/llm_bridge.py`
  OpenAI API 호환 브리지 클라이언트
- `backend/app/llm_bridge_server.py`
  OAuth/API key를 수용하는 LLM Bridge 서버
- `backend/app/mcp_layer.py`
  MCP adapter / skill / guardrail 인터페이스
- `backend/app/guardrails.py`
  실행 안전장치 전용 모듈
- `backend/app/registry.py`
  MCP registry 전용 서버
- `backend/app/planner.py`
  Planner MCP 전용 서버
- `data/jarvis.db`
  MCP 레지스트리와 프롬프트를 저장하는 SQLite DB
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
- 프롬프트 DB 기반 LLM 프롬프트 관리
- Filesystem MCP 실제 호출
- 브리지 기반 LLM 호출

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
export FRONTEND_APP_URL="http://127.0.0.1:7400"
export OPENAI_OAUTH_LOOPBACK_PORT="1455"
export SESSION_SECRET="replace-this-in-real-use"
export OPENAI_API_KEY="your-openai-api-key"
export LLM_BRIDGE_PROVIDER="openai_codex"
export LOCAL_OPENAI_BASE_URL="http://127.0.0.1:11434/v1"
export LOCAL_OPENAI_API_KEY="local-bridge"
export MCP_REGISTRY_URL="http://127.0.0.1:7100/registry/mcps"
export PLANNER_MCP_URL="http://127.0.0.1:7200/planner/plan"
export LLM_BRIDGE_URL="http://127.0.0.1:7600"
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

### 1. MCP Registry 서버

```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.registry:app --reload --port 7100
```

기본 주소: `http://127.0.0.1:7100`

### 2. Planner MCP 서버

```bash
cd backend
source .venv/bin/activate
uvicorn app.planner:app --reload --port 7200
```

기본 주소: `http://127.0.0.1:7200`

### 3. LLM Bridge 서버

```bash
cd backend
source .venv/bin/activate
uvicorn app.llm_bridge_server:app --reload --port 7600
```

기본 주소: `http://127.0.0.1:7600`

Bridge는:
- OAuth 또는 `OPENAI_API_KEY`를 수용합니다.
- `local_openai_compatible` 백엔드도 수용합니다.
- OpenAI API 호환 `/v1/chat/completions` 경로를 제공합니다.
- 런타임과 planner, intent 분류, 일반 채팅의 공통 LLM 진입점입니다.

Bridge provider 모드:

- `auto`
  - `LOCAL_OPENAI_BASE_URL`
  - `OPENAI_API_KEY`
  - `OpenAI OAuth`
  - `codex fallback`
  순서로 선택합니다.
- `local_openai_compatible`
  - 로컬 OpenAI 호환 서버를 강제 사용합니다.
- `openai_api_key`
  - `OPENAI_API_KEY`를 강제 사용합니다.
- `openai_oauth`
  - 현재 OpenAI OAuth 프로필을 강제 사용합니다.
- `openai_codex`
  - 현재 OpenAI OAuth 프로필을 기반으로 Codex provider 스타일 fallback을 강제 사용합니다.
- `codex_fallback`
  - 단순 텍스트 요청만 `codex exec`로 처리합니다.

### 4. 메인 백엔드

```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload --port 7300
```

기본 주소: `http://127.0.0.1:7300`

메인 백엔드는 기본적으로:
- `MCP_REGISTRY_URL`로 registry server를 호출해 MCP 목록을 받습니다.
- `LLM_BRIDGE_URL`을 통해 LLM을 호출합니다.

Bridge 또는 live runtime이 불가능하면 runtime fallback 경로를 시도합니다.

### 5. 프론트엔드

```bash
cd frontend
npm install
npm run dev
```

기본 주소: `http://127.0.0.1:7400`

Vite dev server는 `/api` 요청을 백엔드로 프록시합니다.

관리자용 MCP 레지스트리 화면은 해시 경로로 분리되어 있습니다.

- 사용자 화면: `http://127.0.0.1:7400/`
- 관리자 화면: `http://127.0.0.1:7400/#/registry-admin`

관리자 화면에서는 두 가지를 관리할 수 있습니다.

- MCP Registry 활성/비활성 및 신규 MCP 추가
- Prompt DB 조회, 생성, 수정, 삭제
- Prompt 버전 이력 확인, 활성 버전 선택, 이전 버전 복구

현재 시스템은 코드에 박힌 문자열 대신 SQLite `data/jarvis.db`를 읽어 다음 프롬프트를 LLM에 전달합니다.

- `intent_classifier`
- `chat_system`
- `planner_system`

프롬프트를 수정하면 새 버전이 쌓이고, 관리자 화면에서 원하는 버전을 다시 활성화해 복구할 수 있습니다.

MCP 레지스트리도 같은 SQLite DB에 저장됩니다.

## 다음 확장 후보

- 실제 LLM API 연동
- 세션 저장과 복원
- 태스크별 실제 MCP 호출 로그
- 자유 입력형 플랜 수정 요청

## Deep Agents 방향

JARVIS는 장기적으로 `Deep Agents + MCP Layer + Guardrails + UI` 구조로 이행하는 것이 목표입니다.

- 공식 벤치마킹 및 권장 아키텍처: [DeepAgents벤치마킹.md](/Users/ppillip/Projects/NiceCodex/DeepAgents벤치마킹.md)
- 현재는 준비 단계로 다음 골격이 추가되어 있습니다.
  - `agent_runtime.py`
  - `deepagents_runtime.py`
  - `mcp_layer.py`
  - `guardrails.py`

즉 앞으로는:

- Deep Agents: planning / orchestration / memory
- MCP Layer: MCP adapter / skill
- Guardrails: 안전 검증
- UI: 승인 / 보고

로 역할을 분리해 갈 수 있습니다.

## soul.md

채팅 처리 시 백엔드는 프로젝트 루트의 `soul.md`를 항상 읽어서 시스템 프롬프트와 함께 전달합니다.

예:

- `자비스는 항상 존댓말을 한다`
- `자비스는 사용자를 코칭하듯 말하지 않는다`
- `자비스는 항상 짧고 단호하게 말한다`

즉 말투나 태도를 바꾸고 싶으면 `soul.md`를 수정하면 됩니다.
## Agent Runtime

JARVIS core orchestration now runs behind a runtime boundary.

- default: `classic`
- optional: `deepagents`
- 공통 LLM 호출 계층: `llm_bridge_server`

Set runtime explicitly:

```bash
export JARVIS_AGENT_RUNTIME=classic
```

or

```bash
export JARVIS_AGENT_RUNTIME=deepagents
```

Current behavior:

- `classic`: bridge를 통해 직접 planning을 수행하고 MCP를 실행
- `deepagents`: bridge를 통해 모델을 사용하려 시도하고, live 실행이 실패하면 `classic`으로 폴백

원칙:

- 런타임은 로그인/OAuth를 직접 다루지 않습니다.
- 모든 LLM 호출은 Bridge를 거칩니다.
- 인증과 토큰 소유권은 Bridge에 있습니다.
