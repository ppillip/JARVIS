from __future__ import annotations

"""Playwright CLI 기반 브라우저 자동화 skill layer.

Playwright wrapper script를 통해 실제 브라우저를 열고, 페이지 탐색/입력/클릭/
스냅샷/스크린샷을 수행한 뒤 executor가 바로 사용할 수 있는 evidence/findings/
result_items 구조로 정리한다.
"""

import asyncio
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List


SUPPORTED_PLAYWRIGHT_TOOLS = {"open", "snapshot", "click", "fill", "press", "screenshot", "read_text"}
PLAYWRIGHT_TOOL_ALIASES = {
    "open_page": "open",
    "goto": "open",
    "navigate": "open",
    "take_snapshot": "snapshot",
    "capture_snapshot": "snapshot",
    "take_screenshot": "screenshot",
    "capture_screenshot": "screenshot",
    "type": "fill",
    "input_text": "fill",
    "tap": "click",
}


def _normalize_screenshot_path(path: str | None, project_root: Path) -> str:
    """스크린샷 경로가 비어 있으면 프로젝트 내부 기본 출력 경로를 만든다."""
    if path and path.strip():
        return path.strip()
    output_dir = project_root / "output" / "playwright"
    output_dir.mkdir(parents=True, exist_ok=True)
    filename = f"jarvis-{datetime.now().strftime('%Y%m%d-%H%M%S')}.png"
    return str(output_dir / filename)


def _extract_snapshot_path(output: str, project_root: Path) -> Path | None:
    """CLI 출력에서 snapshot 파일 경로를 추출한다."""
    match = re.search(r"\[Snapshot\]\(([^)]+)\)", output)
    if not match:
        return None
    raw_path = match.group(1).strip()
    path = Path(raw_path)
    if path.is_absolute():
        return path

    candidates = [
        Path.cwd() / raw_path,
        project_root / raw_path,
        project_root / "backend" / raw_path,
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return candidates[0]


def _parse_snapshot_refs(snapshot_text: str) -> List[Dict[str, str]]:
    """snapshot YAML 유사 텍스트에서 ref/label/role 후보를 추출한다."""
    entries: List[Dict[str, str]] = []
    for line in snapshot_text.splitlines():
        if "[ref=" not in line:
            continue
        ref_match = re.search(r"\[ref=(e\d+)\]", line)
        if not ref_match:
            continue
        ref = ref_match.group(1)
        role_match = re.search(r"-\s*([a-zA-Z]+)", line.strip())
        label_match = re.search(r'"([^"]+)"', line)
        trailing_match = re.search(r":\s*(.+)$", line)
        label = ""
        if label_match:
            label = label_match.group(1).strip()
        elif trailing_match and "[ref=" in line:
            candidate = trailing_match.group(1).strip()
            if "[ref=" not in candidate and candidate.lower() != candidate.upper():
                label = candidate
        entries.append(
            {
                "ref": ref,
                "role": role_match.group(1).lower() if role_match else "",
                "label": label.strip(),
                "line": line.strip(),
            }
        )
    return entries


def _extract_click_target(task: Any, arguments: Dict[str, Any]) -> str:
    """click/read_text 작업에서 찾고 싶은 텍스트를 추출한다."""
    if str(arguments.get("target", "")).strip():
        return str(arguments["target"]).strip()
    if str(arguments.get("label", "")).strip():
        return str(arguments["label"]).strip()
    combined = f"{getattr(task, 'title', '')} {getattr(task, 'expected_result', '')}"
    patterns = [
        r"([A-Za-z0-9가-힣._-]+)\s*버튼",
        r"([A-Za-z0-9가-힣._-]+)\s*클릭",
        r"([A-Za-z0-9가-힣._-]+)\s*텍스트",
    ]
    for pattern in patterns:
        match = re.search(pattern, combined)
        if match:
            return match.group(1).strip()
    return ""


def _extract_fill_target(task: Any, arguments: Dict[str, Any]) -> str:
    """fill 작업에서 입력값(text)과 분리된 필드 타깃을 추출한다."""
    if str(arguments.get("target", "")).strip():
        return str(arguments["target"]).strip()
    if str(arguments.get("label", "")).strip():
        return str(arguments["label"]).strip()
    combined = f"{getattr(task, 'title', '')} {getattr(task, 'expected_result', '')}"
    patterns = [
        r"([A-Za-z0-9가-힣._-]+)\s*입력",
        r"([A-Za-z0-9가-힣._-]+)\s*입력란",
        r"([A-Za-z0-9가-힣._-]+)\s*필드",
    ]
    for pattern in patterns:
        match = re.search(pattern, combined)
        if match:
            return match.group(1).strip()
    return ""


def _normalize_target_text(value: str) -> str:
    """버튼/입력란 같은 일반 꼬리표를 제거해 snapshot 검색용 핵심 타깃으로 정리한다."""
    normalized = value.strip()
    selector_aliases = {
        'input[name="loginid"]': "계정 정보 입력",
        'input#loginkey': "계정 정보 입력",
        'input[type="email"]': "계정 정보 입력",
        'input[name="password"]': "비밀번호 입력",
        'input#password': "비밀번호 입력",
        'input[type="password"]': "비밀번호 입력",
        'button[type="submit"]': "로그인",
        '.btn_login': "로그인",
    }
    lowered = normalized.lower()
    for pattern, alias in selector_aliases.items():
        if pattern in lowered:
            normalized = alias
            lowered = normalized.lower()

    replacements = [
        "버튼",
        "링크",
        "입력란",
        "입력 필드",
        "제출",
        "폼",
        "페이지",
        "메인",
    ]
    for token in replacements:
        normalized = normalized.replace(token, " ")
    normalized = re.sub(r"\s+", " ", normalized).strip()
    aliases = {
        "아이디": "계정 정보 입력",
        "아이디 입력": "계정 정보 입력",
        "아이디 입력란": "계정 정보 입력",
        "이메일": "계정 정보 입력",
        "이메일 입력": "계정 정보 입력",
        "검색어": "검색",
        "검색어 입력": "검색",
        "검색어 입력란": "검색",
        "검색창": "검색",
        "비밀번호 입력": "비밀번호 입력",
        "비밀번호 입력란": "비밀번호 입력",
        "비밀번호": "비밀번호 입력",
        "로그인 제출": "로그인",
        "제출": "로그인",
    }
    return aliases.get(normalized, normalized)


def _is_ref_token(value: str) -> bool:
    """Playwright snapshot ref 형식인지 확인한다."""
    return bool(re.fullmatch(r"e\d+", value.strip()))


def _looks_like_placeholder_ref(value: str) -> bool:
    """planner가 만든 가짜 ref/표현식인지 판정한다."""
    normalized = value.strip().lower()
    markers = ["${", "snapshot", ".ref", "_ref", "식별된", "placeholder"]
    return any(marker in normalized for marker in markers)


def _resolve_ref_from_snapshot(snapshot_text: str, target_text: str) -> str | None:
    """snapshot에서 target_text와 가장 잘 맞는 ref를 고른다."""
    target = _normalize_target_text(target_text).lower()
    if not target:
        return None

    entries = _parse_snapshot_refs(snapshot_text)

    def snippet_for(ref: str) -> str:
        marker = f"[ref={ref}]"
        index = snapshot_text.find(marker)
        if index < 0:
            return ""
        return snapshot_text[index:index + 320].lower()

    def score(entry: Dict[str, str]) -> int:
        label = entry["label"].strip().lower()
        role = entry["role"]
        snippet = snippet_for(entry["ref"])
        score_value = 0
        if label == target:
            score_value += 100
        if target in label:
            score_value += 30
        if role == "button":
            score_value += 20
        if role == "link":
            score_value += 10
        if "로그인" in label and "카카오계정" in label:
            score_value += 40
        if "login" in label:
            score_value += 20
        if "loginform" in snippet or "accounts/login" in snippet:
            score_value += 80
        if '/url: "#' in snippet:
            score_value -= 80
        if "바로가기" in label:
            score_value -= 60
        if "skip" in label:
            score_value -= 60
        return score_value

    candidates = [
        entry for entry in entries
        if entry["label"] and target in entry["label"].lower()
    ]
    if candidates:
        candidates.sort(key=score, reverse=True)
        return candidates[0]["ref"]
    return None


async def run_playwright_cli(cli_path: Path, args: List[str], codex_home: Path) -> str:
    """Playwright wrapper script를 실행하고 stdout 텍스트를 반환한다."""
    if not cli_path.exists():
        raise RuntimeError(f"Playwright CLI wrapper not found: {cli_path}")

    env = os.environ.copy()
    env["CODEX_HOME"] = str(codex_home)
    process = await asyncio.create_subprocess_exec(
        str(cli_path),
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=env,
    )
    stdout, stderr = await process.communicate()
    output = stdout.decode("utf-8", errors="ignore").strip()
    error = stderr.decode("utf-8", errors="ignore").strip()
    if process.returncode != 0:
        raise RuntimeError(error or output or f"Playwright CLI failed with code {process.returncode}")
    if "### Error" in output or output.startswith("Error:") or "\nError:" in output:
        raise RuntimeError(output)
    return output or "(no output)"


async def execute_playwright_task(
    task: Any,
    cli_path: Path,
    codex_home: Path,
    project_root: Path,
) -> Dict[str, Any]:
    """구조화된 Playwright task를 CLI 명령으로 실행하고 보고 형식으로 변환한다."""
    tool_name = PLAYWRIGHT_TOOL_ALIASES.get(str(task.tool_name or "").strip(), str(task.tool_name or "").strip())
    arguments = task.tool_arguments or {}
    resolved_ref = str(arguments.get("ref", "")).strip()
    resolved_target = _extract_fill_target(task, arguments) if tool_name == "fill" else _extract_click_target(task, arguments)
    if resolved_ref and not _is_ref_token(resolved_ref):
        if resolved_ref.startswith("text="):
            resolved_target = resolved_ref.split("=", 1)[1].strip() or resolved_target
        elif _looks_like_placeholder_ref(resolved_ref):
            resolved_target = resolved_target or ""
        else:
            resolved_target = resolved_ref
        resolved_ref = ""

    if tool_name not in SUPPORTED_PLAYWRIGHT_TOOLS:
        return {
            "status": "unsupported",
            "evidence": [f"Playwright MCP 미지원 tool: {tool_name or '없음'}"],
            "findings": [f"{task.title}: Playwright MCP에서 아직 지원하지 않는 tool 입니다."],
            "result_items": [],
            "log": f"playwright.{tool_name or 'unknown'} 미지원: {task.title}",
        }

    args: List[str] = [tool_name]
    if tool_name == "open":
        url = str(arguments.get("url", "")).strip()
        if not url:
            raise RuntimeError("Playwright open tool requires a url argument.")
        args.append(url)
        if arguments.get("headed", True) or arguments.get("headless") is False:
            args.append("--headed")
    elif tool_name == "click":
        ref = resolved_ref
        if not ref:
            snapshot_output = await run_playwright_cli(cli_path=cli_path, args=["snapshot"], codex_home=codex_home)
            snapshot_path = _extract_snapshot_path(snapshot_output, project_root)
            if not snapshot_path or not snapshot_path.exists():
                raise RuntimeError("Playwright click tool requires a ref argument or a readable snapshot.")
            ref = _resolve_ref_from_snapshot(snapshot_path.read_text(encoding="utf-8"), resolved_target or "로그인")
            if not ref:
                raise RuntimeError(f"Playwright click target '{resolved_target or 'unknown'}' not found in snapshot.")
            resolved_ref = ref
        args.append(ref)
    elif tool_name == "fill":
        ref = resolved_ref
        fill_value = str(arguments.get("value", "") or arguments.get("text", ""))
        if not ref:
            snapshot_output = await run_playwright_cli(cli_path=cli_path, args=["snapshot"], codex_home=codex_home)
            snapshot_path = _extract_snapshot_path(snapshot_output, project_root)
            if not snapshot_path or not snapshot_path.exists():
                raise RuntimeError("Playwright fill tool requires a ref argument or a readable snapshot.")
            ref = _resolve_ref_from_snapshot(snapshot_path.read_text(encoding="utf-8"), resolved_target)
            if not ref:
                raise RuntimeError(f"Playwright fill target '{resolved_target or 'unknown'}' not found in snapshot.")
            resolved_ref = ref
        args.extend([ref, fill_value])
    elif tool_name == "press":
        key = str(arguments.get("key", "")).strip()
        if not key:
            raise RuntimeError("Playwright press tool requires a key argument.")
        args.append(key)
    elif tool_name == "screenshot":
        args.append(_normalize_screenshot_path(arguments.get("path"), project_root))
    elif tool_name == "read_text":
        ref = resolved_ref
        if not ref:
            snapshot_output = await run_playwright_cli(cli_path=cli_path, args=["snapshot"], codex_home=codex_home)
            snapshot_path = _extract_snapshot_path(snapshot_output, project_root)
            if not snapshot_path or not snapshot_path.exists():
                raise RuntimeError("Playwright read_text tool requires a ref argument or a readable snapshot.")
            ref = _resolve_ref_from_snapshot(snapshot_path.read_text(encoding="utf-8"), resolved_target)
            if not ref:
                raise RuntimeError(f"Playwright read_text target '{resolved_target or 'unknown'}' not found in snapshot.")
            resolved_ref = ref
        args = ["eval", "el => el.textContent", ref]

    output = await run_playwright_cli(cli_path=cli_path, args=args, codex_home=codex_home)
    findings: List[str] = []
    result_items: List[str] = []

    if tool_name == "open":
        url = str(arguments.get("url", "")).strip()
        findings.append(f"{url} 페이지를 브라우저로 열었습니다.")
        result_items.append(url)
    elif tool_name == "snapshot":
        findings.append("현재 브라우저 화면의 요소 스냅샷을 수집했습니다.")
        result_items.extend([line for line in output.splitlines()[:10] if line.strip()])
    elif tool_name == "click":
        findings.append(f"{resolved_target or resolved_ref or '대상'} 요소를 클릭했습니다.")
    elif tool_name == "fill":
        findings.append(f"{resolved_target or resolved_ref or '대상'} 입력란에 값을 채웠습니다.")
    elif tool_name == "press":
        findings.append(f"{arguments.get('key')} 키 입력을 전송했습니다.")
    elif tool_name == "screenshot":
        screenshot_path = args[-1]
        findings.append(f"현재 브라우저 화면의 스크린샷을 저장했습니다.")
        result_items.append(screenshot_path)
    elif tool_name == "read_text":
        findings.append("대상 요소의 텍스트를 읽었습니다.")
        result_items.append(output)

    return {
        "status": "completed",
        "evidence": [f"Playwright MCP {tool_name} 성공: {output}"],
        "findings": findings or [f"{task.title} 작업을 Playwright MCP로 수행했습니다."],
        "result_items": result_items,
        "log": f"playwright.{tool_name} 호출 완료: {task.title}",
    }
