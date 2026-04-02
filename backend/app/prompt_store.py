from __future__ import annotations

"""Prompt DB 조회와 템플릿 치환만 담당하는 얇은 helper.

실제 저장은 sqlite_store가 담당하고, 이 모듈은 planner/bridge/main이
프롬프트 본문을 단순하게 읽고 렌더링하도록 돕는다.
"""

from typing import Any, Dict

from app.sqlite_store import get_prompt_entry


PLAYWRIGHT_PLANNER_RULES = (
    "- Playwright MCP를 선택했으면 tool_name은 반드시 open, snapshot, click, fill, press, screenshot 중 하나만 사용한다.\n"
    "- open_page, goto, navigate 같은 별칭은 사용하지 않는다. 반드시 open을 쓴다.\n"
    "- Playwright open의 tool_arguments는 최소 {\"url\":\"https://...\"} 형태를 포함해야 한다.\n"
    "- Playwright task 하나에는 브라우저 액션 하나만 담는다. click과 fill, fill과 submit을 한 task에 합치지 않는다.\n"
    "- Playwright fill은 한 입력란만 다룬다. 아이디/비밀번호처럼 여러 입력란이면 fill task를 여러 개로 분해한다.\n"
    "- Playwright click/fill/read_text의 target은 CSS selector나 표현식이 아니라 사람이 읽을 수 있는 의미 라벨로 적는다.\n"
    "- 예: {\"target\":\"로그인\",\"value\":\"...\"}, {\"target\":\"계정 정보 입력\",\"value\":\"...\"}, {\"target\":\"비밀번호 입력\",\"value\":\"...\"}\n"
)


def _augment_prompt_content(prompt_id: str, content: str) -> str:
    """현재 런타임 계약상 반드시 필요한 planner 규칙을 프롬프트에 보강한다."""
    if prompt_id not in {"planner_system", "deepagent_planner_system"}:
        return content
    if "Playwright MCP를 선택했으면 tool_name은 반드시 open, snapshot, click, fill, press, screenshot 중 하나만 사용한다." in content:
        return content
    if "- 한국어로 출력한다." in content:
        return content.replace("- 한국어로 출력한다.\n", f"{PLAYWRIGHT_PLANNER_RULES}- 한국어로 출력한다.\n")
    return f"{content.rstrip()}\n{PLAYWRIGHT_PLANNER_RULES}"


def get_prompt_content(prompt_id: str, fallback: str = "") -> str:
    """Prompt DB에서 활성 프롬프트 본문을 읽고 없으면 fallback을 반환한다."""
    item = get_prompt_entry(prompt_id)
    if item and str(item.get("content", "")).strip():
        return _augment_prompt_content(prompt_id, str(item["content"]))
    return _augment_prompt_content(prompt_id, fallback)


def render_prompt_template(template: str, values: Dict[str, Any]) -> str:
    """`{{name}}` 형태의 템플릿 변수를 단순 문자열 치환으로 렌더링한다."""
    rendered = template
    for key, value in values.items():
        rendered = rendered.replace(f"{{{{{key}}}}}", str(value))
    return rendered
