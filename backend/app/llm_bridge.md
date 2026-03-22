# llm_bridge.py

JARVIS 내부 코드가 공통으로 사용하는 LLM Bridge 클라이언트입니다.

- Bridge의 `/v1/chat/completions` 호출
- 텍스트/JSON 응답 helper 제공
- LangChain `ChatOpenAI`를 Bridge base URL에 맞게 생성
