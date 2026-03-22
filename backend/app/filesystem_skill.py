from __future__ import annotations

"""Filesystem MCP 결과를 사용자 의도에 맞는 보고 형태로 해석하는 skill layer."""

import json
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any, Dict, List

from app.guardrails import (
    build_filesystem_access_denied_message,
    is_filesystem_access_denied,
    resolve_tool_arguments,
    wants_file_count,
)


def summarize_filesystem_listing(text: str) -> Dict[str, Any]:
    """Filesystem MCP의 목록 응답을 디렉터리/파일 목록으로 정규화한다."""
    # directory_tree는 JSON, list_directory는 [DIR]/[FILE] 텍스트를 반환하므로 둘 다 흡수한다.
    stripped = text.strip()
    if stripped.startswith("["):
        try:
            payload = json.loads(stripped)
            if isinstance(payload, list):
                directories = [str(item.get("name", "")).strip() for item in payload if isinstance(item, dict) and item.get("type") == "directory" and str(item.get("name", "")).strip()]
                files = [str(item.get("name", "")).strip() for item in payload if isinstance(item, dict) and item.get("type") == "file" and str(item.get("name", "")).strip()]
                highlights = directories[:5] + files[:3]
                return {
                    "directories": directories,
                    "files": files,
                    "highlights": highlights,
                    "entry_count": len(directories) + len(files),
                }
        except Exception:
            pass

    entries = [line.strip() for line in text.splitlines() if line.strip()]
    directories: List[str] = []
    files: List[str] = []

    for entry in entries:
        if entry.startswith("[DIR] "):
            directories.append(entry.replace("[DIR] ", "", 1).strip())
        elif entry.startswith("[FILE] "):
            files.append(entry.replace("[FILE] ", "", 1).strip())

    highlights = directories[:5] + files[:3]
    return {
        "directories": directories,
        "files": files,
        "highlights": highlights,
        "entry_count": len(entries),
    }


def wants_latest_file_name(task_title: str, expected_result: str) -> bool:
    """최신 파일명 확인 의도인지 판정한다."""
    normalized = f"{task_title} {expected_result}".lower()
    return "파일" in normalized and ("최근" in normalized or "최신" in normalized)


def parse_file_info_text(text: str) -> Dict[str, str]:
    """`get_file_info`의 `key: value` 텍스트를 dict로 변환한다."""
    parsed: Dict[str, str] = {}
    for line in text.splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        parsed[key.strip()] = value.strip()
    return parsed


def build_latest_file_summary(target_path: str, latest_file_name: str | None, file_count: int) -> Dict[str, Any]:
    """파일 개수와 최신 파일 결과를 보고용 구조로 만든다."""
    if latest_file_name:
        return {
            "evidence": [f"Filesystem MCP get_file_info 성공 ({target_path}) - 최신 파일: {latest_file_name}, 파일 {file_count}개"],
            "findings": [
                f"{target_path} 경로에서 파일 {file_count}개를 확인했습니다.",
                f"{target_path} 경로에서 가장 최근 파일은 {latest_file_name} 입니다.",
            ],
            "result_items": [latest_file_name],
        }
    return {
        "evidence": [f"Filesystem MCP get_file_info 부분 실패 ({target_path}) - 파일 {file_count}개, 최신 파일 판별 실패"],
        "findings": [
            f"{target_path} 경로에서 파일 {file_count}개를 확인했습니다.",
            f"{target_path} 경로의 파일 수정 시각을 확인하지 못해 최근 파일명을 특정하지 못했습니다.",
        ],
        "result_items": [],
    }


async def execute_filesystem_task(
    task: Any,
    call_tool,
    extract_tool_text,
    home_root,
    project_root,
) -> Dict[str, Any]:
    """Filesystem MCP 실행 결과를 사용자 보고 형식으로 해석한다."""
    # tool arguments의 $HOME, $PROJECT_ROOT 는 guardrail에서 실제 경로로 해석한다.
    tool_arguments = resolve_tool_arguments(task.tool_arguments or {}, home_root, project_root)
    tool_name = task.tool_name
    target_path = str(tool_arguments.get("path", project_root))

    result = await call_tool(tool_name, tool_arguments)
    text = extract_tool_text(result)

    if is_filesystem_access_denied(text):
        return {
            "status": "denied",
            "evidence": [f"Filesystem MCP {tool_name} 실패 ({target_path}): {text}"],
            "findings": [build_filesystem_access_denied_message(target_path)],
            "result_items": [],
            "log": f"filesystem.{tool_name} 접근 거부: {task.title}",
        }

    if tool_name not in {"list_directory", "directory_tree", "read_text_file"}:
        return {
            "status": "unsupported",
            "evidence": [f"Filesystem MCP 미지원 tool: {tool_name}"],
            "findings": [f"{task.title}: Filesystem MCP에서 아직 지원하지 않는 tool 입니다."],
            "result_items": [],
            "log": f"filesystem.{tool_name} 미지원: {task.title}",
        }

    if tool_name == "read_text_file":
        preview = "\n".join(text.splitlines()[:8]).strip()
        return {
            "status": "completed",
            "evidence": [f"Filesystem MCP read_text_file 성공 ({target_path}): {preview}"],
            "findings": [f"{target_path} 파일을 읽었고, 상위 8줄을 기준으로 내용을 확인했습니다."],
            "result_items": [preview] if preview else [],
            "log": f"filesystem.read_text_file 호출 완료: {task.title}",
        }

    listing_summary = summarize_filesystem_listing(text)
    directories = listing_summary["directories"]
    files = listing_summary["files"]
    visible_directories = ", ".join(directories[:12]) or "하위 폴더 없음"

    wants_latest = wants_latest_file_name(task.title, task.expected_result)
    wants_count = wants_file_count(task.title, task.expected_result)

    if wants_latest:
        latest_file_name = None
        latest_modified = None
        for file_name in files:
            # 최신 파일 판별은 파일마다 get_file_info를 추가 조회해서 수정 시각을 비교한다.
            file_path = str(Path(target_path) / file_name)
            file_info_result = await call_tool("get_file_info", {"path": file_path})
            file_info_text = extract_tool_text(file_info_result)
            info = parse_file_info_text(file_info_text)
            modified = info.get("modified")
            if not modified:
                continue
            modified_dt = parsedate_to_datetime(modified)
            if latest_modified is None or modified_dt > latest_modified:
                latest_modified = modified_dt
                latest_file_name = file_name

        summary = build_latest_file_summary(target_path, latest_file_name, len(files))
        return {
            "status": "completed",
            "evidence": summary["evidence"],
            "findings": summary["findings"],
            "result_items": summary["result_items"],
            "log": f"filesystem.{tool_name} 호출 완료: {task.title}",
        }

    if wants_count:
        visible_files = ", ".join(files[:20]) or "파일 없음"
        findings = [f"{target_path} 경로에서 파일 {len(files)}개를 확인했습니다. 파일 목록: {visible_files}"]
        if directories:
            findings.append(f"같은 경로에서 폴더 {len(directories)}개도 함께 감지됐지만, 본 보고는 파일 개수 기준으로 정리했습니다.")
        return {
            "status": "completed",
            "evidence": [f"Filesystem MCP {tool_name} 성공 ({target_path}) - 파일 {len(files)}개: {visible_files}"],
            "findings": findings,
            "result_items": files,
            "log": f"filesystem.{tool_name} 호출 완료: {task.title}",
        }

    findings = [f"{target_path} 경로에서 폴더 {len(directories)}개를 확인했습니다. 폴더 목록: {visible_directories}"]
    if files:
        findings.append(f"같은 경로에서 파일 {len(files)}개도 함께 감지됐지만, 본 보고는 폴더 목록 기준으로 정리했습니다.")
    return {
        "status": "completed",
        "evidence": [f"Filesystem MCP {tool_name} 성공 ({target_path}) - 폴더 {len(directories)}개: {visible_directories}"],
        "findings": findings,
        "result_items": directories,
        "log": f"filesystem.{tool_name} 호출 완료: {task.title}",
    }
