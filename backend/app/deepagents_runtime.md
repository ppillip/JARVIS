# deepagents_runtime.py

Deep Agents 연동 경계입니다.

- Deep Agents 기반 planning/execution 시도
- Bridge 뒤의 OpenAI-compatible 모델 사용
- 실패 시 `ClassicAgentRuntime`으로 폴백
- 현재 JARVIS의 차세대 런타임 후보
