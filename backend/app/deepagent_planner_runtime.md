# deepagent_planner_runtime.py

Deep Agent를 최종 planner / replanner로 사용하는 planner 런타임입니다.

역할:
- capability registry 참조
- MCP-aware planning
- task decomposition
- replanning 준비
- 최종 selected_mcp_id / tool_name / tool_arguments 구체화

원칙:
- executor 역할을 하지 않습니다.
- 실제 집행은 Stable Executor 계층이 맡습니다.
