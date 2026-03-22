# llm_bridge_server.py

LLM Bridge 서버 구현입니다.

- OAuth, API key, local OpenAI-compatible backend 선택
- OpenAI-compatible `/v1/chat/completions` 제공
- `openai_codex`, `openai_oauth`, `openai_api_key`, `local_openai_compatible` 모드 지원
- 런타임이 인증을 직접 다루지 않도록 경계 제공
