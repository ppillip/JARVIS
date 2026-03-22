from __future__ import annotations

"""여러 LLM provider를 OpenAI-compatible 인터페이스로 감싸는 bridge 서버.

OAuth, API key, local OpenAI-compatible provider, codex fallback 같은 백엔드를
하나의 `/v1/chat/completions` 스타일 인터페이스 뒤에 숨긴다.
"""

import asyncio
import json
import os
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from openai import AsyncOpenAI
from pydantic import BaseModel, Field

load_dotenv()

PROJECT_ROOT = Path(__file__).resolve().parents[2]
STATE_DIR = Path(os.getenv("NICECODEX_STATE_DIR", str(Path.home() / ".nicecodex")))
AGENT_DIR = STATE_DIR / "agent"
AUTH_PROFILES_PATH = AGENT_DIR / "auth-profiles.json"
DEFAULT_LOCAL_BASE_URL = "http://127.0.0.1:11434/v1"

app = FastAPI(title="JARVIS LLM Bridge")


class BridgeChatMessage(BaseModel):
    """OpenAI-compatible chat message 구조."""

    role: str
    content: Any


def read_auth_profiles() -> Dict[str, Any]:
    """로컬 auth profile 저장소를 읽는다."""
    if not AUTH_PROFILES_PATH.exists():
        return {"profiles": {}, "order": {"openai-codex": []}}
    return json.loads(AUTH_PROFILES_PATH.read_text())


def get_active_oauth_credential() -> Optional[Dict[str, Any]]:
    """활성 OpenAI OAuth credential 하나를 반환한다."""
    store = read_auth_profiles()
    order = store.get("order", {}).get("openai-codex", [])
    profiles = store.get("profiles", {})
    for profile_id in order:
        profile = profiles.get(profile_id)
        if not isinstance(profile, dict):
            continue
        credential = profile.get("credential")
        if isinstance(credential, dict) and credential.get("access"):
            return credential
    for profile in profiles.values():
        if isinstance(profile, dict):
            credential = profile.get("credential")
            if isinstance(credential, dict) and credential.get("access"):
                return credential
    return None


def get_bridge_credential() -> Dict[str, Any]:
    """기본 OpenAI upstream에 사용할 credential을 반환한다."""
    api_key = os.getenv("OPENAI_API_KEY")
    if api_key:
        return {"type": "api_key", "access": api_key}
    oauth = get_active_oauth_credential()
    if oauth:
        return {"type": "oauth", **oauth}
    raise RuntimeError("No OpenAI credential is available in bridge.")


def get_bridge_provider_mode() -> str:
    """Bridge provider 모드 환경변수를 읽는다."""
    return os.getenv("LLM_BRIDGE_PROVIDER", "auto").strip().lower()


def get_local_openai_config() -> Optional[Dict[str, Any]]:
    """로컬 OpenAI-compatible backend 설정이 있으면 반환한다."""
    base_url = os.getenv("LOCAL_OPENAI_BASE_URL", "").strip() or os.getenv("LLM_BRIDGE_LOCAL_BASE_URL", "").strip()
    if not base_url:
        return None
    return {
        "type": "local_openai_compatible",
        "base_url": base_url.rstrip("/"),
        "api_key": os.getenv("LOCAL_OPENAI_API_KEY", "").strip() or os.getenv("LLM_BRIDGE_LOCAL_API_KEY", "").strip() or "local-bridge",
        "provider": "local_openai_compatible",
    }


def get_available_bridge_backends() -> List[str]:
    """현재 환경에서 선택 가능한 backend 목록을 나열한다."""
    backends: List[str] = []
    if get_local_openai_config():
        backends.append("local_openai_compatible")
    if os.getenv("OPENAI_API_KEY", "").strip():
        backends.append("openai_api_key")
    if get_active_oauth_credential():
        backends.append("openai_oauth")
        backends.append("openai_codex")
    backends.append("codex_fallback")
    return backends


def resolve_upstream_backend() -> Dict[str, Any]:
    """provider 정책과 현재 환경을 바탕으로 실제 upstream backend를 결정한다."""
    # provider 선택은 bridge 내부 책임이다. 런타임은 이 정책을 몰라도 된다.
    mode = get_bridge_provider_mode()
    local_config = get_local_openai_config()
    if mode == "local_openai_compatible":
        if local_config:
            return local_config
        raise RuntimeError("LOCAL_OPENAI_BASE_URL is not configured.")
    if mode == "openai_api_key":
        api_key = os.getenv("OPENAI_API_KEY", "").strip()
        if api_key:
            return {"type": "openai_api_key", "api_key": api_key, "provider": "openai"}
        raise RuntimeError("OPENAI_API_KEY is not configured.")
    if mode == "openai_oauth":
        oauth = get_active_oauth_credential()
        if oauth and oauth.get("access"):
            return {"type": "openai_oauth", "api_key": oauth["access"], "provider": oauth.get("provider", "openai")}
        raise RuntimeError("OpenAI OAuth credential is not available.")
    if mode == "openai_codex":
        oauth = get_active_oauth_credential()
        if oauth and oauth.get("access"):
            return {"type": "openai_codex", "provider": "openai-codex"}
        raise RuntimeError("OpenAI Codex OAuth credential is not available.")
    if mode == "codex_fallback":
        return {"type": "codex_fallback", "provider": "codex"}
    if local_config:
        return local_config
    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    if api_key:
        return {"type": "openai_api_key", "api_key": api_key, "provider": "openai"}
    oauth = get_active_oauth_credential()
    if oauth and oauth.get("access"):
        return {"type": "openai_oauth", "api_key": oauth["access"], "provider": oauth.get("provider", "openai")}
    return {"type": "codex_fallback", "provider": "codex"}


def auth_status_payload() -> Dict[str, Any]:
    """Bridge 상태 API에 내릴 인증/백엔드 요약 정보를 만든다."""
    upstream = resolve_upstream_backend()
    return {
        "available": upstream.get("type") != "codex_fallback" or True,
        "mode": get_bridge_provider_mode(),
        "type": upstream.get("type"),
        "provider": upstream.get("provider"),
        "base_url": upstream.get("base_url"),
        "available_backends": get_available_bridge_backends(),
    }


def stringify_message_content(content: Any) -> str:
    """문자열 또는 멀티파트 content를 codex prompt용 텍스트로 평탄화한다."""
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts: List[str] = []
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text" and item.get("text"):
                parts.append(str(item["text"]).strip())
        return "\n".join(part for part in parts if part)
    return str(content).strip()


def format_messages_as_prompt(messages: List[BridgeChatMessage]) -> str:
    """chat 메시지 배열을 codex fallback용 단일 프롬프트로 변환한다."""
    lines: List[str] = []
    for item in messages:
        role = item.role.strip().upper()
        content = stringify_message_content(item.content)
        if not content:
            continue
        lines.append(f"{role}: {content}")
    return "\n\n".join(lines)


async def run_codex_exec(prompt: str, model: str) -> str:
    """Codex CLI를 사용해 단순 텍스트 요청을 처리한다."""
    with tempfile.NamedTemporaryFile(delete=False) as output_file:
        output_path = output_file.name

    cmd = [
        "codex",
        "exec",
        "--skip-git-repo-check",
        "--sandbox",
        "read-only",
        "-C",
        str(PROJECT_ROOT),
        "-o",
        output_path,
    ]
    if model != "default":
        cmd.extend(["-m", model])
    cmd.append(prompt)

    process = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await process.communicate()

    try:
        reply = Path(output_path).read_text().strip()
    finally:
        Path(output_path).unlink(missing_ok=True)

    if process.returncode != 0:
        detail = stderr.decode("utf-8", errors="ignore").strip() or "codex exec failed"
        raise RuntimeError(detail)
    if not reply:
        raise RuntimeError("codex exec returned empty output")
    return reply


async def call_upstream_chat_completions(payload: Dict[str, Any]) -> str:
    """선택된 upstream backend에 OpenAI chat completions 요청을 보낸다."""
    upstream = resolve_upstream_backend()
    if upstream["type"] in {"codex_fallback", "openai_codex"}:
        raise RuntimeError("codex_fallback")
    client = AsyncOpenAI(
        api_key=upstream["api_key"],
        base_url=upstream.get("base_url"),
        timeout=30,
    )
    response = await client.chat.completions.create(**payload)
    content = response.choices[0].message.content
    if not content:
        raise RuntimeError("OpenAI returned empty content.")
    return content


async def resolve_chat_completion(payload: Dict[str, Any]) -> str:
    """가능하면 upstream을 쓰고, 단순 요청이면 codex로 degrade한다."""
    messages = [BridgeChatMessage.model_validate(item) for item in payload.get("messages", [])]
    try:
        return await call_upstream_chat_completions(payload)
    except Exception:
        # 단순 chat 요청은 codex exec로 degrade 가능하지만, tool/function payload는 별도 지원이 필요하다.
        prompt = format_messages_as_prompt(messages)
        return await run_codex_exec(prompt=prompt, model=str(payload.get("model") or "default"))


@app.get("/health")
def health_check() -> Dict[str, str]:
    """Bridge healthcheck."""
    return {"status": "ok"}


@app.get("/bridge/auth/status")
def bridge_auth_status() -> Dict[str, Any]:
    """Bridge의 현재 provider/credential 상태를 반환한다."""
    return auth_status_payload()


@app.post("/v1/chat/completions")
async def bridge_chat_completions(payload: Dict[str, Any]) -> Dict[str, Any]:
    """OpenAI-compatible `/v1/chat/completions` 엔드포인트."""
    if not isinstance(payload.get("messages"), list) or not payload["messages"]:
        raise HTTPException(status_code=400, detail="messages must not be empty")
    content = await resolve_chat_completion(payload)
    created = int(time.time())
    return {
        "id": f"chatcmpl-{uuid.uuid4().hex}",
        "object": "chat.completion",
        "created": created,
        "model": str(payload.get("model") or "unknown"),
        "choices": [
            {
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": content,
                },
                "finish_reason": "stop",
            }
        ],
        "usage": {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
        },
    }
