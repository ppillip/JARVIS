# JARVIS

Python 백엔드와 Svelte 프론트엔드로 구성한 세션 기반 JARVIS 콘솔입니다.
질문을 받으면 먼저 MCP-aware 관점으로 해석하고, 현재 연결된 capability로 더 직접적으로 처리할 수 있으면 MCP를 사용합니다.
그렇지 않으면 LLM으로 답변하고, 실제 상태 변경이 필요한 요청은 승인 기반 플랜/실행 경로로 보냅니다.

## 개발 기록

- [2026.04.02.devHistory.md](./2026.04.02.devHistory.md)
- [2026.03.22.devHistory.md](./2026.03.22.devHistory.md)

## 핵심 문서

- [`README.md`](./README.md)
  프로젝트 개요, 실행 방법, 현재 라우팅 계약
- [`TestCase.md`](./TestCase.md)
  라우팅 시퀀스, 기대 동작, 실패 조건, 핸드오프 가이드
- [`테이블정의서.md`](./테이블정의서.md)
  SQLite 테이블 구조와 저장 대상 설명
- [`LoginProcess.md`](./LoginProcess.md)
  OpenAI OAuth loopback 로그인 구조와 로컬 auth profile 저장 방식 설명
- [`요구사항검토서.md`](./요구사항검토서.md)
  JARVIS 요구사항 목록과 구현 여부, 실행 검증 결과, 결함/리스크 기록

## 잔여 작업들

- [TODO.md](./TODO.md)

## 포트 정책

JARVIS는 앞으로 무조건 `7000`번대 포트만 사용합니다.

- MCP Registry: `7100`
- Planner Service: `7200`
- Main Backend: `7300`
- Frontend: `7400`
- LLM Bridge: `7600`
- OAuth loopback callback: `1455` (OpenAI/Codex 호환 예외)

다른 프로젝트 에이전트는 이 포트대를 피해서 사용해야 합니다.
단, OpenAI GUI OAuth는 현재 Codex 호환을 위해 `1455` loopback callback을 사용합니다.

## 구조

### Planner 축

- `backend/app/agent_runtime.py`
  planner/executor 공통 인터페이스와 공통 데이터 구조
- `backend/app/deepagent_planner_runtime.py`
  최종 MCP-aware planner
- `backend/app/fallback_planner_runtime.py`
  planner fallback
- `backend/app/sequential_thinking_assist.py`
  선택적 전략 보조 (assist 계층)
- `backend/app/sequential_thinking_runtime.py`
  초기 Sequential Thinking runtime 실험 구현 (현재 assist 계층으로 이동 중인 과거 경계)
- `backend/app/plan_schema.py`
  공통 normalized plan schema
- `backend/app/plan_normalizer.py`
  planner 출력 정규화

### Executor 축

- `backend/app/task_compiler.py`
  normalized plan -> executable tasks
- `backend/app/stable_executor_runtime.py`
  안정적인 실행 계층
- `backend/app/filesystem_skill.py`
  Filesystem MCP 결과 해석
- `backend/app/korean_law_skill.py`
  Korean Law MCP 결과를 evidence/findings/result_items 구조로 변환하는 skill layer
- `backend/app/playwright_skill.py`
  Playwright CLI 기반 브라우저 자동화 skill layer (open/snapshot/click/fill/press/screenshot/read_text)
- `backend/app/report_builder.py`
  findings / evidence / result_items 기반 최종 보고 생성

### 라우팅 축

- `backend/app/intent_router.py`
  사용자 의도 해석 및 adjudication
- `backend/app/capability_resolver.py`
  MCP capability graph 기반 처리 경로 결정
- `backend/app/capability_router.py`
  retrieval MCP 선택 로직
- `backend/app/tool_answer_runtime.py`
  조회형 질문을 MCP 결과 기반 답변으로 변환하는 runtime
- `backend/app/decline_policy.py`
  지원 불가/우회 안내 응답을 일관되게 생성하는 정책 계층
- `backend/app/fallback_policy.py`
  LLM fallback 경로 정책

### 공통 인프라 축

- `backend/app/capability_map_service.py`
  MCP registry -> capability layer 변환
- `backend/app/runtime_factory.py`
  환경변수에 따라 planner/executor 구현체를 조합하는 팩토리
- `backend/app/trace_logger.py`
  structured trace 생성
- `backend/app/fallback_reasons.py`
  fallback reason taxonomy
- `backend/app/guardrails.py`
  실행 안전장치
- `backend/app/sqlite_store.py`
  SQLite 저장 계층
- `backend/app/prompt_store.py`
  Prompt DB 접근 계층

### 서비스 계층

- `backend/app/main.py`
  메인 API
- `backend/app/registry.py`
  MCP registry 서버
- `backend/app/planner.py`
  planner service
- `backend/app/llm_bridge.py`
  브리지 클라이언트
- `backend/app/llm_bridge_server.py`
  OAuth / API key / local provider를 수용하는 OpenAI 호환 브리지 서버
- `backend/app/mcp_layer.py`
  MCP adapter/skill 경계
- `backend/app/deepagents_runtime.py`
  Deep Agents 연동 경계
- `backend/app/classic_runtime.py`
  레거시 호환 경계 (StdioMcpClient 포함)

### 프론트와 데이터

- `frontend/src/App.svelte`
  사용자용 JARVIS UI
- `frontend/src/RegistryAdmin.svelte`
  관리자 화면
- `frontend/src/app.css`
  아이언맨 HUD 스타일 디자인 시스템
- `frontend/src/main.js`
  프론트 진입점 및 해시 기반 라우팅
- `data/jarvis.db`
  MCP registry, prompts, workflow runs, trace, conversation timeline 저장소
- `data/prompts.json`
  프롬프트 초기 마이그레이션 소스
- `soul.md`
  자비스 말투와 태도 규칙

### MCP Runtime

- `mcp-runtime/`
  Node.js 기반 MCP 서버 런타임 패키지
  - `@modelcontextprotocol/server-filesystem` 의존
  - `npm run filesystem` 으로 Filesystem MCP 서버 실행

## 현재 기능

- OpenAI GUI OAuth 로그인
- 지령 입력 시 adjudication -> capability resolution -> planner / tool / LLM / decline 경로 선택
- 좌측 패널의 이전 대화 목록 조회 및 세션 전환
- 우측 상단 `↻`로 새 대화 세션 시작
- 입력창 아래 비영속 내부 상태 텍스트 표시
- 플랜 수정 요청 / 승인
- 승인 후 task compiler를 통한 실행 태스크 확정
- Stable Executor를 통한 MCP 실행
- Filesystem MCP 실제 호출 및 retrieval answer
- Korean Law MCP 실제 호출 및 retrieval answer
- Playwright MCP 기반 브라우저 자동화 (open/snapshot/click/fill/press/screenshot/read_text)
- structured trace 생성 및 저장
- SQLite 기반 대화 타임라인 저장 및 렌더링
- Prompt DB 기반 프롬프트 관리
- MCP Registry 관리 화면
- Runs / Trace 조회
- 브리지 기반 LLM 호출
- decline 시 지원 불가 사유 및 우회 안내 일관 생성

## 로그인 설정

이 프로젝트의 로그인은 `OpenAI GUI OAuth + loopback callback + 로컬 auth profile 저장` 구조입니다.

중요:

- 로그인 상세 구조와 주의사항은 [`LoginProcess.md`](./LoginProcess.md)에서만 관리합니다.
- `README.md`에는 실행에 필요한 최소 환경변수와 진입점만 남깁니다.

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
export JARVIS_AGENT_RUNTIME="sequential"
export JARVIS_SEQUENTIAL_MODEL="default"
export JARVIS_SEQUENTIAL_ENABLED="true"
export LAW_OC="your-law-oc-key"
```

또는 `backend/.env.example`를 복사해 `backend/.env`로 둘 수 있습니다.

로그인 진입점:

- `GET /api/auth/openai`
- 프론트 UI의 `OpenAI 로그인` 버튼

상세한 redirect URI, pending OAuth 저장, auth profile 저장, refresh 정책, logout 정책은 [`LoginProcess.md`](./LoginProcess.md)를 기준으로 봐야 합니다.

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

### 2. Planner 서비스

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

### 4. MCP Runtime (Filesystem MCP 서버)

```bash
cd mcp-runtime
npm install
npm run filesystem
```

Filesystem MCP는 `@modelcontextprotocol/server-filesystem` 패키지를 사용합니다.
`/Users/ppillip` 경로를 기준으로 파일시스템 접근을 제공합니다.

### 5. 메인 백엔드

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
- `LLM_BRIDGE_URL`을 통해 planner/chat LLM을 호출합니다.
- `JARVIS_AGENT_RUNTIME`에 따라 planner 축을 선택합니다.
- `runtime_factory.py`가 환경변수 기반으로 planner/executor 구현체를 조합합니다.

지원 runtime 모드:
- `classic`
- `deepagents`
- `sequential`

현재 권장:
```bash
export JARVIS_AGENT_RUNTIME=sequential
```

### 6. 프론트엔드

```bash
cd frontend
npm install
npm run dev
```

기본 주소: `http://127.0.0.1:7400`

Vite dev server는 `/api` 및 `/ws` 요청을 백엔드로 프록시합니다.

현재 사용자 UI는 다음 흐름을 지원합니다.

- 좌측 패널: 이전 대화 세션 목록
- 중앙 패널: 현재 대화 / 플랜 / 실행 / 보고 타임라인
- 입력창 아래: 현재 내부 처리 상태 텍스트
- 우측 패널: MCP 상세와 Task 라우팅 상태
- 우측 상단 `↻`: 새 대화 세션 시작

관리자용 MCP 레지스트리 화면은 해시 경로로 분리되어 있습니다.

- 사용자 화면: `http://127.0.0.1:7400/`
- 관리자 화면: `http://127.0.0.1:7400/#/registry-admin`

관리자 화면에서는 다음을 관리할 수 있습니다.

- MCP Registry 활성/비활성 및 신규 MCP 추가
- Prompt DB 조회, 생성, 수정, 삭제
- Prompt 버전 이력 확인, 활성 버전 선택, 이전 버전 복구
- 최근 run 및 trace 조회

현재 시스템은 코드에 박힌 문자열 대신 SQLite `data/jarvis.db`를 읽어 다음 프롬프트를 LLM에 전달합니다.

- `intent_adjudicator`
- `capability_resolver`
- `tool_answer_system`
- `filesystem_tool_answer_planner`
- `chat_system`
- `planner_system`
- `deepagent_planner_system`
- `sequential_thinking_router`
- `sequential_thinking_system`

참고:

- `intent_classifier`도 `backend/app/main.py`에 레거시 helper로 남아 있지만, 현재 주 라우팅 경로는 `intent_adjudicator -> capability_resolver`입니다.

프롬프트를 수정하면 새 버전이 쌓이고, 관리자 화면에서 원하는 버전을 다시 활성화해 복구할 수 있습니다.

MCP 레지스트리도 같은 SQLite DB에 저장됩니다.

## 저장 구조

주요 저장 대상:

- `mcp_registry`
  활성 MCP registry
- `prompt_definitions`, `prompt_versions`
  Prompt DB
- `workflow_runs`
  플랜/실행/보고 스냅샷
- `workflow_trace_events`
  structured trace
- `conversation_events`
  채팅/플랜/승인/실행/보고 타임라인

즉 프론트 대화창은 더 이상 임시 메모리만으로 그리지 않고, DB에 저장된 타임라인을 기준으로 시간순 렌더링합니다.

테이블 단위 정의는 [`테이블정의서.md`](./테이블정의서.md)에서 확인할 수 있습니다.

주의:

- 입력창 아래의 내부 상태 텍스트는 현재 UI 상태 표시용이며 DB에 저장하지 않습니다.
- 좌측 대화 목록은 첫 질문 기반 제목과 최근 시각을 표시합니다.

## 현재 라우팅 계약

JARVIS는 더 이상 단순 `question | command` 분기로 처리하지 않습니다.

현재 메인 경로는 다음 순서입니다.

1. `adjudicate_intent()`
   사용자 요청을 해석해 판단 메모를 만듭니다.
2. `resolve_capability()`
   현재 MCP capability graph와 비교해 실제 처리 가능한 경로를 고릅니다.
3. 최종 처리
   - `mcp_action`
   - `mcp_retrieval`
   - `llm_fallback`
   - `decline`

라우팅 테스트 기준 문서:

- [`TestCase.md`](./TestCase.md)
  현재 라우팅 시퀀스, 기대 동작, 실패 조건, 에이전트 핸드오프 가이드
- [`backend/tests/test_routing_spec.py`](./backend/tests/test_routing_spec.py)
  위 스펙을 실제로 검증하는 pytest 회귀 테스트

핵심 원칙:

- 정책상 금지면 `decline`
- MCP가 더 직접적으로 처리 가능하면 MCP 사용
- MCP가 없거나 적절하지 않으면 LLM fallback
- 둘 다 아니면 `decline`
- 사용자가 `LLM한테 물어봐라`처럼 처리 수단을 명시하면 그 의도를 우선 반영

관련 파일:

- `backend/app/intent_router.py`
- `backend/app/capability_resolver.py`
- `backend/app/capability_router.py`
- `backend/app/tool_answer_runtime.py`
- `backend/app/decline_policy.py`
- `backend/app/fallback_policy.py`
- `backend/app/main.py`

현재 retrieval executor가 붙어 있는 MCP:

- `filesystem`
- `korean_law`

현재 action executor가 붙어 있는 MCP:

- `filesystem`
- `playwright` (open/snapshot/click/fill/press/screenshot/read_text)

아직 미구현 또는 미연결인 대표 retrieval 영역:

- `weather.read`
- `finance.read`
- `browser.read` (일반 웹 검색)

## 현재 아키텍처 원칙

JARVIS는 다음 원칙을 기준으로 정리되어 있습니다.

- 생각은 PlannerRuntime
- 실행은 ExecutorRuntime
- 번역은 TaskCompiler
- 가능성 판단은 CapabilityMap
- 조립은 RuntimeFactory
- 운영 가시성은 Structured Trace

즉:
- `Sequential Thinking`: 선택적 전략 보조
- `Deep Agent`: 최종 planner / replanner
- `Executor`: 안정적인 집행 / evidence / report

## 기술 스택

### 백엔드

- Python 3 (FastAPI + uvicorn)
- 주요 의존: `fastapi`, `uvicorn`, `httpx`, `itsdangerous`, `python-dotenv`, `deepagents`, `langchain-openai`, `pytest`
- SQLite (파일 기반 저장)

### 프론트엔드

- Svelte 5 + Vite 7
- Vanilla CSS (아이언맨 HUD 스타일)

### MCP Runtime

- Node.js (ES Module)
- `@modelcontextprotocol/server-filesystem`

## 다음 확장 후보

- 메모리 구조 구현 (GPT류 + Claude Code식 계층형)
- planner 품질 고도화
- capability taxonomy 정교화
- MCP별 skill/adapter 확장
- local OpenAI-compatible provider 검증
- 보고 품질 개선
- `browser.read`, `weather.read` 등 retrieval executor 확장
- 관리자/설정 화면 강화
- 다중 사용자 지원 (로그인 기반 사용자별 메모리/설정)
- 가드레일 고도화

## Deep Agents 방향

JARVIS는 현재 `Deep Agent planner + Stable Executor + Bridge + Registry` 구조를 향해 정리되어 있습니다.

- 참고: [DeepAgents벤치마킹.md](./DeepAgents벤치마킹.md)

현재 해석:
- `SequentialThinkingAssist`
  - planner가 아니라 전략 보조
- `DeepAgentPlannerRuntime`
  - MCP-aware planning / replanning
- `StableExecutorRuntime`
  - 실행 / evidence / report

즉 Deep Agents는 executor가 아니라 planner 축에 우선 고정하는 방향입니다.

## soul.md

채팅 처리 시 백엔드는 프로젝트 루트의 `soul.md`를 항상 읽어서 시스템 프롬프트와 함께 전달합니다.

현재 규칙:

- 자비스는 항상 한국어로 대화한다
- 자비스는 항상 침착하고 예의 바른 존댓말을 사용한다
- 자비스는 불필요하게 공격적이거나 냉소적인 표현을 사용하지 않는다
- 자비스는 실무적으로 도움이 되는 방향으로 짧고 명확하게 답한다

즉 말투나 태도를 바꾸고 싶으면 `soul.md`를 수정하면 됩니다.

## Agent Runtime

현재 JARVIS는 planner/executor 분리 구조를 가집니다.

- planner runtime
  - `deepagents` → `DeepAgentPlannerRuntime`
  - `classic` → `FallbackPlannerRuntime`
  - `sequential` → `DeepAgentPlannerRuntime` + `SequentialThinkingAssist`
- executor runtime
  - `stable_executor` → `StableExecutorRuntime`
- runtime 조립
  - `runtime_factory.py` → 환경변수 기준으로 planner/executor 조합

실행 흐름:

1. intent adjudication
2. capability resolution
3. `mcp_action`이면 optional sequential thinking assist
4. deep agent planner 또는 fallback planner
5. plan normalizer
6. approval
7. task compiler
8. stable executor
9. trace / report / timeline 저장

질문형 요청 흐름:

1. intent adjudication
2. capability resolution
3. `mcp_retrieval`이면 `ToolAnswerRuntime`
4. 아니면 `llm_fallback`
5. 필요 시 `decline` (decline_policy 통해 일관된 안내 메시지 생성)

runtime 동작 원칙:

- `classic`
  - fallback planner와 stable executor 중심 경로
- `deepagents`
  - Deep Agent planner를 우선 사용하고 planner fallback이 가능
- `sequential`
  - 필요 시 Sequential Thinking이 전략 보조 후 Deep Agent planner에 handoff

공통 원칙:

- 런타임은 로그인/OAuth를 직접 다루지 않습니다.
- 모든 LLM 호출은 Bridge를 거칩니다.
- 인증과 토큰 소유권은 Bridge에 있습니다.

## 테스트

사람이 읽는 라우팅 스펙은 루트의 [`TestCase.md`](./TestCase.md)에 정리되어 있습니다.

실행 가능한 회귀 테스트는 [`backend/tests/test_routing_spec.py`](./backend/tests/test_routing_spec.py)입니다.

pytest 기본 설정 파일:

- [`backend/pytest.ini`](./backend/pytest.ini)

백엔드 테스트 실행:

```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
pytest -q
```

특정 라우팅 스펙만 실행:

```bash
cd backend
source .venv/bin/activate
pytest tests/test_routing_spec.py -q
```

테스트가 검증하는 핵심 계약:

- 상식형 질문은 기본적으로 `llm_fallback`
- 법령 조회는 `korean_law`가 있으면 `mcp_retrieval`
- 로컬 파일시스템 조회는 `filesystem`이 있으면 `mcp_retrieval`
- 사용자가 `LLM` 처리를 명시하면 그 의도가 우선
- 실제 수정 요청은 `mcp_action`

문서와 테스트를 함께 볼 때의 권장 순서:

1. [`TestCase.md`](./TestCase.md)로 기대 동작 확인
2. [`backend/tests/test_routing_spec.py`](./backend/tests/test_routing_spec.py)로 실제 회귀 스펙 확인
3. 실패 시 `intent_router -> capability_resolver -> tool_answer_runtime` 순서로 원인 분리
