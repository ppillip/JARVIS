# playwright_skill.py

Playwright wrapper CLI를 사용해 실제 브라우저 자동화 태스크를 수행하는 skill layer입니다.

주요 역할:
- `open`, `snapshot`, `click`, `fill`, `press`, `screenshot` 실행
- CLI stdout/stderr를 evidence로 정리
- 실행 결과를 findings / result_items / log 구조로 변환

이 파일은 planner가 결정한 `playwright` 태스크를 executor가 안정적으로 집행할 수 있게 만드는 어댑터 역할을 합니다.
