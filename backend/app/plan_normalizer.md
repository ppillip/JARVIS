# plan_normalizer.py

planner 출력과 trace를 공통 normalized plan으로 바꾸는 계층입니다.

역할:
- planner 구현체별 차이를 흡수
- trace 기반 planner metadata 구성
- 후속 계층이 planner 구체 구현을 몰라도 되게 만들기
