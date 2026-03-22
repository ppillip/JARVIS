# plan_schema.py

Planner와 executor 사이에서 공통으로 사용하는 표준 plan schema입니다.

포함 모델:
- `NormalizedPlan`
- `NormalizedTaskDraft`
- `PlannerMetadata`

목적:
- planner 구현체가 달라도 동일한 schema로 후속 계층이 동작하게 하기
