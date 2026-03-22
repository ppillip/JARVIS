from __future__ import annotations

"""런타임과 서비스가 공통으로 사용하는 LLM Bridge 클라이언트.

planner, classifier, 일반 채팅이 인증 방식을 직접 알지 않도록,
브리지 서버에 대한 공통 호출 인터페이스를 제공한다.
"""

import json
import os
from typing import Any, Dict, List, Optional, Tuple

import httpx
from langchain_openai import ChatOpenAI


LLM_BRIDGE_URL = os.getenv("LLM_BRIDGE_URL", "http://127.0.0.1:7600")


def extract_json_object(text: str) -> Dict[str, Any]:
    """자유 텍스트에서 JSON 객체 하나를 최대한 복원해 파싱한다."""
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = stripped.split("\n", 1)[-1]
        if stripped.endswith("```"):
            stripped = stripped[:-3]
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        import re

        match = re.search(r"\{.*\}", stripped, re.DOTALL)
        if not match:
            raise
        return json.loads(match.group(0))


def parse_model_name(model: str) -> str:
    """`provider:model` 형식에서 실제 모델명만 추출한다."""
    if ":" in model:
        _, name = model.split(":", 1)
        return name
    return model


def create_openai_chat_model(model: str, *, use_responses_api: bool = False) -> ChatOpenAI:
    """LangChain용 ChatOpenAI 객체를 Bridge base URL 기준으로 생성한다."""
    # 실제 인증은 bridge가 들고 있고, 런타임은 OpenAI-compatible base_url만 본다.
    return ChatOpenAI(
        model=parse_model_name(model),
        api_key="bridge",
        base_url=f"{LLM_BRIDGE_URL}/v1",
        use_responses_api=use_responses_api,
        timeout=30,
    )


async def invoke_bridge_chat(
    model: str,
    messages: List[Dict[str, str]],
    *,
    temperature: float = 0,
) -> Tuple[str, Optional[str]]:
    """Bridge의 chat completions 엔드포인트를 호출하고 텍스트를 반환한다."""
    async with httpx.AsyncClient(timeout=60.0) as client:
        response = await client.post(
            f"{LLM_BRIDGE_URL}/v1/chat/completions",
            json={
                "model": parse_model_name(model),
                "messages": messages,
                "temperature": temperature,
            },
        )
        response.raise_for_status()
        payload = response.json()
    choices = payload.get("choices", [])
    if not choices:
        raise RuntimeError("LLM bridge returned no choices.")
    message = choices[0].get("message", {})
    content = message.get("content")
    if not isinstance(content, str) or not content.strip():
        raise RuntimeError("LLM bridge returned empty content.")
    return content.strip(), payload.get("id")


async def invoke_bridge_text(model: str, prompt: str) -> Tuple[str, Optional[str]]:
    """단일 user prompt를 Bridge에 보내고 텍스트 응답을 받는다."""
    return await invoke_bridge_chat(
        model=model,
        messages=[{"role": "user", "content": prompt}],
        temperature=0,
    )


async def invoke_bridge_json(model: str, prompt: str) -> Dict[str, Any]:
    """Bridge 응답을 JSON 객체로 해석해 반환한다."""
    text, _ = await invoke_bridge_text(model=model, prompt=prompt)
    return extract_json_object(text)


async def get_bridge_auth_status() -> Dict[str, Any]:
    """Bridge가 현재 사용할 수 있는 인증/백엔드 상태를 조회한다."""
    async with httpx.AsyncClient(timeout=10.0) as client:
        response = await client.get(f"{LLM_BRIDGE_URL}/bridge/auth/status")
        response.raise_for_status()
        return response.json()


def bridge_credentials_available() -> bool:
    """Bridge가 즉시 사용할 수 있는 LLM backend가 있는지 빠르게 판정한다."""
    try:
        with httpx.Client(timeout=3.0) as client:
            response = client.get(f"{LLM_BRIDGE_URL}/bridge/auth/status")
            response.raise_for_status()
            payload = response.json()
        return bool(payload.get("available"))
    except Exception:
        return False
