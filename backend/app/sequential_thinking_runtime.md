# sequential_thinking_runtime.py

Sequential Thinking(ST) 전용 런타임입니다.

역할:
- 사용자 지령이 모호하거나 전략 분기가 필요한지 먼저 판단
- 필요할 때만 ST 요약과 전략 옵션을 생성
- 생성한 handoff brief를 Deep Agents 같은 delegate runtime에 넘겨 실행 가능한 plan으로 재구성
- 실제 실행은 delegate runtime에 위임

핵심 포인트:
- ST는 항상 도는 것이 아니라 필요할 때만 개입합니다.
- planning 품질을 높이되, executor 책임은 가져가지 않습니다.
- 결과적으로 `전략 정리 -> Deep Agent planning -> MCP 실행` 순서를 만듭니다.
