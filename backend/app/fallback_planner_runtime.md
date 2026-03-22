# fallback_planner_runtime.py

Deep Agent planner가 실패했을 때 사용하는 최소 planner fallback입니다.

역할:
- bridge 기반 텍스트 planning 시도
- 실패 시 템플릿 기반 최소 plan 생성
- planning 계층의 가용성을 보장

주의:
- 최종 planner는 아닙니다.
- Deep Agent planning 실패 시에만 안전망으로 사용합니다.
