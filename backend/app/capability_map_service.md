# capability_map_service.py

MCP registry를 planner 친화적인 capability map으로 압축하는 서비스입니다.

역할:
- raw MCP metadata -> capability labels
- planner/ST/classifier에 전달할 축약 정보 생성
- 권한/위험도/사용 가능 상태를 한곳에 정리
