# fallback_reasons.py

역할:
- Planner fallback 사유를 표준 코드로 정규화합니다.
- Deep Agent 실패 원인을 운영 trace에서 비교 가능하게 만듭니다.

주요 내용:
- `classify_fallback_reason(message)`
  - 예외 문자열을 읽고 `fallback_reason_code`와 사용자용 설명으로 변환합니다.

대표 코드:
- `auth_scope_missing`
- `bridge_unavailable`
- `deepagent_unavailable`
- `deepagent_invalid_output`
- `deepagent_exception`
