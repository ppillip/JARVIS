# Deep Agents 벤치마킹

## 목적

JARVIS를 다음 구조로 재편할 수 있는지 판단합니다.

- 사용자 의도 분석
- MCP 레지스트리 이해
- MCP 조합 기반 플랜 생성
- 승인 후 실행
- 결과/리포트 생성

## 결론

Deep Agents는 `JARVIS Core`를 직접 구현하는 것보다 더 적합합니다.

이유:

- 장기 실행 작업용 agent harness
- planning / task decomposition 내장
- context management 내장
- subagent orchestration 내장
- memory / filesystem backend 개념 내장
- LangGraph runtime 기반 durable execution 지원

즉, 지금 우리가 직접 `main.py`에 넣고 있는:

- 플랜 생성
- 태스크 분해
- 컨텍스트 관리
- 장기 메모리
- 다단계 오케스트레이션

은 Deep Agents가 더 잘 맡는 영역입니다.

## 공식 근거

공식 자료 기준 Deep Agents는 다음을 지원합니다.

- 복잡한 멀티스텝 작업 계획 및 분해
- 파일 시스템 기반 컨텍스트 관리
- 서브에이전트 위임
- 장기 메모리
- durable execution / streaming / human-in-the-loop

참고:

- https://docs.langchain.com/oss/python/deepagents/overview
- https://www.langchain.com/deep-agents
- https://pypi.org/project/deepagents/

## 무엇이 Deep Agents에 맞고, 무엇이 커스텀이어야 하는가

### Deep Agents에 맡길 것

- planning
- task decomposition
- subagent orchestration
- long-running execution
- context / memory runtime
- human-in-the-loop 기본 루프

### JARVIS 커스텀으로 남길 것

- MCP registry
- MCP adapter
- MCP skill / interpreter
- guardrails
- JARVIS UI
- 승인/보고 UX

## 현재 문제와 Deep Agents 적합성

현재 문제는 세 층이 섞여 있다는 점입니다.

1. JARVIS Core
2. MCP 도구 호출
3. Filesystem MCP용 결과 해석

이 세 층이 섞이면 `main.py`에 계속 도메인 로직이 추가됩니다.

예:

- 파일 개수
- 최근 파일
- 폴더 목록
- 허용 경로

이런 로직이 코어에 쌓이면 JARVIS가 아니라 `Filesystem MCP 전용 앱`이 됩니다.

Deep Agents를 도입하면 최소한 다음 분리가 가능합니다.

- Deep Agents Runtime: 계획/오케스트레이션
- MCP Layer: MCP 도구 접근
- Guardrails: 허용 경로/정책 검증
- Skills: MCP 결과 해석

## 추천 아키텍처

### 1. JARVIS UI

- 사용자 지령 입력
- 승인 / 수정 요청
- 실행 상태 표시
- 단일 보고 화면

### 2. JARVIS API

- 세션 관리
- 승인 상태 관리
- UI와 agent runtime 연결

### 3. Deep Agents Runtime

- command -> plan
- plan -> proposed tasks
- task orchestration
- long-running context
- subagent spawn

### 4. MCP Layer

- MCP registry 로딩
- MCP adapter 선택
- tool invocation

### 5. Guardrails

- 허용 경로 검증
- 위험 tool 제한
- 입력 정규화
- 정책 위반 시 차단

### 6. MCP Skills

- filesystem skill
- fetch skill
- github skill
- memory skill

## 현재 코드베이스에 추가한 준비물

이번 작업으로 아래 골격을 추가했습니다.

- `backend/app/agent_runtime.py`
  - 런타임 공통 인터페이스
- `backend/app/deepagents_runtime.py`
  - Deep Agents 연동 대상 경계
- `backend/app/mcp_layer.py`
  - MCP adapter / skill / guardrail 인터페이스
- `backend/app/guardrails.py`
  - 실행 안전장치 전용 모듈

이 골격의 의미는 다음과 같습니다.

- 앞으로 `main.py`에서 orchestration 로직을 빼낼 수 있음
- MCP별 후처리를 skill 계층으로 이동할 수 있음
- Deep Agents를 런타임 교체점으로 붙일 수 있음

## 권장 이행 순서

1. `main.py`의 planning/execution orchestration을 runtime 계층으로 이동
2. filesystem 관련 후처리를 `filesystem_skill.py`로 분리
3. MCP adapter 레지스트리 도입
4. Deep Agents runtime 실제 연결
5. 승인/보고 UI를 runtime 응답 구조에 맞게 정리

## 최종 판단

JARVIS의 목표가:

- 복합 의도 분석
- MCP-aware planning
- MCP 조합 실행
- 장기 실행
- 결과 보고

라면, Deep Agents는 적합합니다.

하지만 Deep Agents만으로 충분하지는 않습니다.

필수 커스텀 계층:

- MCP registry
- MCP skills
- guardrails
- JARVIS 승인/보고 UI

한 줄 결론:

Deep Agents는 `JARVIS Core`에 적합하고, MCP 해석/가드레일/리포트는 우리가 유지해야 합니다.
## 현재 적용 상태

- `AgentRuntime` 인터페이스 도입
- `ClassicAgentRuntime` 구현 및 실제 라우트 연결 완료
- `DeepAgentsRuntime` 경계 연결 완료
- `JARVIS_AGENT_RUNTIME=deepagents` 설정 시 Deep Agents 경계를 통과하되, 아직은 `ClassicAgentRuntime`으로 안전 폴백
- `filesystem_skill.py`로 Filesystem MCP 후처리 분리 완료

즉 현재는 "Deep Agents 이행 준비 문서" 수준이 아니라, 실제 코드 경계가 이미 적용된 상태입니다.
