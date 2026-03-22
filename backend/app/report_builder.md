# report_builder.py

역할:
- Executor가 수집한 실행 결과를 최종 보고 구조로 조립합니다.
- 실행기와 보고 생성 로직을 분리해 결합도를 낮춥니다.

주요 내용:
- `build_execution_report(...)`
  - `tasks`, `used_mcp_names`, `evidence`, `findings`, `result_items`를 받아
    UI와 API가 공통으로 쓰는 보고 딕셔너리를 만듭니다.

의도:
- Stable Executor는 집행에 집중하고,
- 보고 포맷은 별도 모듈에서 관리하도록 분리합니다.
