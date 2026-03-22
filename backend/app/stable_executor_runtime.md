# stable_executor_runtime.py

Stable Executor 계층입니다.

역할:
- 승인된 task 실행
- MCP adapter 호출
- evidence / finding / result_items 축적
- 상태 전이와 최종 보고 생성

원칙:
- planning을 하지 않습니다.
- 사용자 의도 해석을 하지 않습니다.
- executor는 집행과 기록만 담당합니다.
