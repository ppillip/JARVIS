from __future__ import annotations

"""Filesystem 실행 전에 적용되는 경로 정규화와 기본 안전장치."""

from pathlib import Path
from typing import Any, Dict


FILESYSTEM_ACCESS_DENIED_PREFIX = "Access denied - path outside allowed directories"


def resolve_runtime_path(value: Any, home_root: Path, project_root: Path) -> Any:
    """단일 값 안의 `$HOME`, `$PROJECT_ROOT` 변수를 실제 경로로 치환한다."""
    if not isinstance(value, str):
        return value
    return value.replace("$HOME", str(home_root)).replace("$PROJECT_ROOT", str(project_root))


def resolve_tool_arguments(arguments: Dict[str, Any], home_root: Path, project_root: Path) -> Dict[str, Any]:
    """tool arguments 전체에서 경로 변수를 재귀적으로 해석한다."""
    # 중첩 dict/list 안의 path 변수도 함께 치환한다.
    resolved: Dict[str, Any] = {}
    for key, value in arguments.items():
        if isinstance(value, dict):
            resolved[key] = resolve_tool_arguments(value, home_root, project_root)
        elif isinstance(value, list):
            resolved[key] = [resolve_runtime_path(item, home_root, project_root) for item in value]
        else:
            resolved[key] = resolve_runtime_path(value, home_root, project_root)
    return resolved


def is_filesystem_access_denied(text: str) -> bool:
    """Filesystem MCP 응답이 허용 경로 밖 접근 거부인지 판별한다."""
    return FILESYSTEM_ACCESS_DENIED_PREFIX in text


def build_filesystem_access_denied_message(target_path: str) -> str:
    """사용자에게 보여줄 Filesystem 접근 거부 메시지를 만든다."""
    return (
        f"{target_path} 경로는 현재 Filesystem MCP 허용 범위 밖이라 조회하지 못했습니다. "
        "허용 범위는 $HOME, $PROJECT_ROOT 입니다."
    )


def wants_file_count(task_title: str, expected_result: str) -> bool:
    """태스크 제목/예상 결과가 파일 개수 조회 의도인지 간단히 판정한다."""
    normalized = f"{task_title} {expected_result}".lower()
    return "파일" in normalized and ("몇" in normalized or "개수" in normalized or "count" in normalized)
