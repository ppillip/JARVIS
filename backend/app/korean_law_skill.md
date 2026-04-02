# korean_law_skill.py

국가 법령 MCP(`korean-law-mcp`) 결과를 JARVIS 보고 형식으로 변환하는 skill 레이어입니다.

- `search_*`, `get_*` 계열 법령/판례/해석 조회 결과를 정리
- raw MCP 응답을 `evidence`, `findings`, `result_items`로 변환
- 법률적 의미 판단은 planner에 맡기고, 이 파일은 최소 실행 어댑터 역할만 수행
