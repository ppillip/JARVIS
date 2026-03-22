from __future__ import annotations

"""Prompt DB 조회와 템플릿 치환만 담당하는 얇은 store helper."""

from typing import Any, Dict

from app.sqlite_store import get_prompt_entry


def get_prompt_content(prompt_id: str, fallback: str = "") -> str:
    """Prompt DB에서 활성 프롬프트 본문을 읽고 없으면 fallback을 반환한다."""
    item = get_prompt_entry(prompt_id)
    if item and str(item.get("content", "")).strip():
        return str(item["content"])
    return fallback


def render_prompt_template(template: str, values: Dict[str, Any]) -> str:
    """`{{name}}` 형태의 템플릿 변수를 단순 문자열 치환으로 렌더링한다."""
    rendered = template
    for key, value in values.items():
        rendered = rendered.replace(f"{{{{{key}}}}}", str(value))
    return rendered
