from __future__ import annotations

"""레거시 호환 classic runtime 구현.

초기 구조와의 호환을 위해 남아 있는 runtime이며, fallback planner와
일부 안정 실행 경로를 제공한다.
"""

import asyncio
import json
import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.agent_runtime import AgentRuntime, RuntimeExecutionResult, RuntimePlan, RuntimeTask
from app.filesystem_skill import execute_filesystem_task
from app.llm_bridge import extract_json_object, invoke_bridge_text
from app.prompt_store import get_prompt_content, render_prompt_template


@dataclass
class ClassicRuntimeConfig:
    """Classic runtime이 사용하는 경로와 MCP 실행 설정."""

    project_root: Path
    home_root: Path
    filesystem_mcp_bin: Path
    codex_home: Path
    playwright_cli: Path
    korean_law_mcp_command: str
    mcp_protocol_version: str


def load_classic_runtime_config() -> ClassicRuntimeConfig:
    """프로젝트 루트, 홈 경로, MCP 바이너리 위치를 읽어 설정을 만든다."""
    project_root = Path(__file__).resolve().parents[2]
    home_root = Path.home()
    codex_home = Path(os.getenv("CODEX_HOME", str(home_root / ".codex")))
    mcp_runtime_root = project_root / "mcp-runtime"
    filesystem_mcp_bin = mcp_runtime_root / "node_modules" / ".bin" / "mcp-server-filesystem"
    playwright_cli = codex_home / "skills" / "playwright" / "scripts" / "playwright_cli.sh"
    korean_law_mcp_command = os.getenv("KOREAN_LAW_MCP_COMMAND", shutil.which("korean-law-mcp") or "korean-law-mcp")
    return ClassicRuntimeConfig(
        project_root=project_root,
        home_root=home_root,
        filesystem_mcp_bin=filesystem_mcp_bin,
        codex_home=codex_home,
        playwright_cli=playwright_cli,
        korean_law_mcp_command=korean_law_mcp_command,
        mcp_protocol_version="2025-11-25",
    )


def is_reporting_only_step(item: RuntimeTask) -> bool:
    """실행 대상이 아니라 보고/정리용 메타 단계인지 판정한다."""
    normalized = item.title.lower()
    markers = ["정리", "요약", "보고", "보여", "전달", "출력 형식", "간단한 보고"]
    return any(marker in normalized for marker in markers)


def normalize_plan_steps(plan: List[RuntimeTask]) -> List[RuntimeTask]:
    """중복 태스크와 메타 단계를 제거해 실행 가능한 플랜으로 정규화한다."""
    if not plan:
        return plan

    # 실행 가능한 태스크가 이미 있으면, 중복 단계와 보고용 메타 단계를 줄인다.
    has_executable_step = any(item.tool_name for item in plan)
    normalized: List[RuntimeTask] = []
    seen_signatures: set[str] = set()

    for item in plan:
        signature = json.dumps(
            {
                "selected_mcp_id": item.selected_mcp_id,
                "tool_name": item.tool_name,
                "tool_arguments": item.tool_arguments,
            },
            ensure_ascii=False,
            sort_keys=True,
        )

        if item.tool_name and signature in seen_signatures:
            continue
        if not item.tool_name and has_executable_step and is_reporting_only_step(item):
            continue

        if item.tool_name:
            seen_signatures.add(signature)
        normalized.append(item)

    return normalized or plan[:1]


def fallback_plan(command: str, detailed: bool) -> List[RuntimeTask]:
    """LLM planning 실패 시 사용할 기본 플랜을 만든다."""
    if detailed:
        return [
            RuntimeTask(
                title=f'지령의 핵심 목표를 정의합니다: "{command}"',
                rationale="LLM 플랜 생성 실패로 기본 플랜을 사용합니다.",
                recommended_mcp_ids=[],
                selected_mcp_id=None,
                expected_result="세분화된 목표 정의",
            ),
            RuntimeTask(
                title="실행 전에 필요한 확인 포인트를 정리합니다.",
                rationale="LLM 플랜 생성 실패로 기본 플랜을 사용합니다.",
                recommended_mcp_ids=[],
                selected_mcp_id=None,
                expected_result="검토 기준",
            ),
        ]
    return [
        RuntimeTask(
            title=f'지령의 목표와 실행 대상을 정리합니다: "{command}"',
            rationale="LLM 플랜 생성 실패로 기본 플랜을 사용합니다.",
            recommended_mcp_ids=[],
            selected_mcp_id=None,
            expected_result="실행 가능한 작업 정의",
        )
    ]


def parse_runtime_tasks(items: List[Dict[str, Any]]) -> List[RuntimeTask]:
    """planner JSON 응답을 RuntimeTask 목록으로 변환한다."""
    parsed: List[RuntimeTask] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or item.get("step") or "").strip()
        if not title:
            continue
        parsed.append(
            RuntimeTask(
                title=title,
                rationale=str(item.get("rationale", "")).strip(),
                recommended_mcp_ids=[str(mcp_id) for mcp_id in item.get("recommended_mcp_ids", []) if str(mcp_id).strip()],
                selected_mcp_id=(str(item.get("selected_mcp_id", "")).strip() or None),
                tool_name=(str(item.get("tool_name", "")).strip() or None),
                tool_arguments=item.get("tool_arguments", {}) if isinstance(item.get("tool_arguments"), dict) else {},
                expected_result=str(item.get("expected_result", "")).strip(),
            )
        )
    return parsed


def serialize_mcps_for_prompt(mcp_catalog: List[Dict[str, Any]]) -> str:
    """MCP registry를 planner prompt에 넣기 좋은 JSON 문자열로 직렬화한다."""
    return json.dumps(
        [
            {
                "id": item.get("id"),
                "name": item.get("name"),
                "scope": item.get("scope"),
                "description": item.get("description"),
                "capabilities": item.get("capabilities"),
                "expected_input": item.get("expected_input"),
                "expected_output": item.get("expected_output"),
                "transport": item.get("transport"),
                "auth_required": item.get("auth_required"),
                "risk_level": item.get("risk_level"),
            }
            for item in mcp_catalog
        ],
        ensure_ascii=False,
        indent=2,
    )


def build_planner_prompt(command: str, soul: str, mcp_catalog: List[Dict[str, Any]], detailed: bool) -> str:
    """Classic runtime planning에 사용할 planner prompt를 만든다."""
    template = get_prompt_content(
        "planner_system",
        fallback=(
            "너는 JARVIS의 Planner MCP 서비스다. "
            "주어진 지령과 MCP registry를 읽고, 현재 사용 가능한 MCP를 기준으로만 검토 가능한 플랜과 실행계획을 세워라.\n\n"
            "반드시 JSON 객체 하나만 출력한다.\n"
            '형식: {"plan":[{"step":"...", "rationale":"...", "recommended_mcp_ids":["filesystem"], "selected_mcp_id":"filesystem", "tool_name":"list_directory", "tool_arguments":{"path":"$HOME"}, "expected_result":"홈디렉터리 폴더 목록"}]}\n'
            "규칙:\n"
            "- 단계 수는 {{target_steps}}로 맞춘다.\n"
            "- recommended_mcp_ids에는 registry에 존재하는 id만 넣는다.\n"
            "- selected_mcp_id는 recommended_mcp_ids 중 하나여야 한다.\n"
            "- 실제로 바로 수행 가능한 단순 조회/읽기 지령이면 1~2단계로 줄이고 메타 검토 단계를 만들지 않는다.\n"
            "- 사용자가 명시하지 않은 추가 범위나 추가 경로를 임의로 탐색하지 않는다.\n"
            "- '최종 보고가 하나'라는 이유만으로 실행 단계를 1개로 제한하지 않는다.\n"
            "- 사용자가 여러 결과값을 요구하면 데이터 취득/필터링/정렬/비교/집계를 분리해 여러 단계로 만들 수 있다.\n"
            "- 동일 MCP 하나만 쓰더라도 중간 계산이 필요하면 여러 실행 단계를 만든다.\n"
            "- 조회 단계와 계산/판단 단계를 한 단계에 뭉개지 않는다.\n"
            "- expected_result에는 각 단계가 직접 산출하는 중간 결과 또는 최종 결과를 적는다.\n"
            "- 최종 보고는 시스템이 수행하므로 '보고용 정리 단계'를 별도 step으로 만들지 않는다.\n"
            "- MCP capability에 tool 정보가 있으면 tool_name과 tool_arguments를 반드시 채운다.\n"
            "- Filesystem MCP의 허용 경로가 $HOME, $PROJECT_ROOT 로 제한되어 있으면 그 범위를 벗어나는 path를 계획하지 않는다.\n"
            "- Playwright MCP를 선택했으면 tool_name은 반드시 open, snapshot, click, fill, press, screenshot 중 하나만 사용한다.\n"
            "- open_page, goto, navigate 같은 별칭은 사용하지 않는다. 반드시 open을 쓴다.\n"
            "- Playwright open의 tool_arguments는 최소 {\"url\":\"https://...\"} 형태를 포함해야 한다.\n"
            "- Playwright fill은 한 task에 한 입력란만 다룬다. 여러 입력란이면 fill task를 여러 개로 분해한다.\n"
            "- Playwright fill의 tool_arguments는 {\"target\":\"입력란 라벨 또는 의미\",\"value\":\"실제 입력값\"} 형태를 사용한다.\n"
            "- Playwright click에서 ref를 모르면 target 또는 label만 넘기고, executor가 snapshot으로 ref를 찾게 한다.\n"
            "- 경로는 필요하면 $HOME 또는 $PROJECT_ROOT 변수를 사용한다.\n"
            "- 각 step은 승인 후 실행 가능한 작업 단계여야 한다.\n"
            "- rationale은 한 문장으로 짧게 쓴다.\n"
            "- expected_result에는 사용자가 최종 보고에서 받게 될 결과를 짧게 적는다.\n"
            "- 한국어로 출력한다.\n"
            "- 불가능한 도구를 가정하지 않는다.\n\n"
            "[SOUL]\n{{soul}}\n\n"
            "[MCP REGISTRY]\n{{mcps}}\n\n"
            "[사용자 지령]\n{{command}}"
        ),
    )
    return render_prompt_template(
        template,
        {
            "target_steps": "4~6개" if detailed else "3~4개",
            "soul": soul,
            "mcps": serialize_mcps_for_prompt(mcp_catalog),
            "command": command.strip(),
        },
    )


def validate_runtime_plan_mcp_ids(items: List[RuntimeTask], mcp_catalog: List[Dict[str, Any]]) -> List[RuntimeTask]:
    """planner가 반환한 MCP id가 실제 registry에 있는지 정리한다."""
    known_ids = {str(item.get("id")) for item in mcp_catalog}
    normalized: List[RuntimeTask] = []
    for item in items:
        recommended = [mcp_id for mcp_id in item.recommended_mcp_ids if mcp_id in known_ids]
        selected = item.selected_mcp_id if item.selected_mcp_id in known_ids else None
        normalized.append(
            RuntimeTask(
                title=item.title,
                rationale=item.rationale,
                recommended_mcp_ids=recommended,
                selected_mcp_id=selected,
                tool_name=item.tool_name,
                tool_arguments=item.tool_arguments,
                expected_result=item.expected_result,
            )
        )
    return normalized


class StdioMcpClient:
    """stdio 기반 MCP 서버와 JSON-RPC로 통신하는 최소 클라이언트."""

    def __init__(self, command: List[str], protocol_version: str, env: Optional[Dict[str, str]] = None) -> None:
        """실행 커맨드와 프로토콜 버전을 저장한다."""
        self.command = command
        self.protocol_version = protocol_version
        self.env = env
        self.process: Optional[asyncio.subprocess.Process] = None
        self._request_id = 0

    async def __aenter__(self) -> "StdioMcpClient":
        """프로세스를 띄우고 initialize까지 마친 뒤 client를 반환한다."""
        self.process = await asyncio.create_subprocess_exec(
            *self.command,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=self.env,
        )
        await self.initialize()
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        """사용이 끝난 MCP 프로세스를 정리한다."""
        if not self.process:
            return
        if self.process.returncode is None:
            self.process.terminate()
            await self.process.wait()

    async def initialize(self) -> None:
        """MCP initialize/initialized 핸드셰이크를 수행한다."""
        # stdio MCP는 initialize/initialized 순서를 맞춰야 정상적으로 tool call을 받을 수 있다.
        await self.request(
            "initialize",
            {
                "protocolVersion": self.protocol_version,
                "capabilities": {},
                "clientInfo": {"name": "jarvis", "version": "0.1.0"},
            },
        )
        await self.notify("notifications/initialized", {})

    async def notify(self, method: str, params: Dict[str, Any]) -> None:
        """응답이 필요 없는 MCP notification을 전송한다."""
        if not self.process or not self.process.stdin:
            raise RuntimeError("MCP process is not running.")
        payload = {"jsonrpc": "2.0", "method": method, "params": params}
        self.process.stdin.write((json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8"))
        await self.process.stdin.drain()

    async def request(self, method: str, params: Dict[str, Any]) -> Dict[str, Any]:
        """응답이 필요한 MCP request를 전송하고 결과를 기다린다."""
        if not self.process or not self.process.stdin or not self.process.stdout:
            raise RuntimeError("MCP process is not running.")

        self._request_id += 1
        payload = {
            "jsonrpc": "2.0",
            "id": self._request_id,
            "method": method,
            "params": params,
        }
        self.process.stdin.write((json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8"))
        await self.process.stdin.drain()

        while True:
            line = await self.process.stdout.readline()
            if not line:
                stderr = ""
                if self.process.stderr:
                    stderr = (await self.process.stderr.read()).decode("utf-8", errors="ignore").strip()
                raise RuntimeError(f"MCP process closed unexpectedly. {stderr}".strip())

            message = json.loads(line.decode("utf-8"))
            if message.get("id") != self._request_id:
                continue
            if "error" in message:
                raise RuntimeError(str(message["error"]))
            return message.get("result", {})


class ClassicAgentRuntime(AgentRuntime):
    """Bridge planning과 Filesystem MCP 실행을 담당하는 기본 런타임."""

    def __init__(self, config: ClassicRuntimeConfig) -> None:
        """런타임 전역 설정과 MCP 실행 경로를 보관한다."""
        self.config = config

    async def build_plan(
        self,
        command: str,
        soul: str,
        mcp_catalog: List[Dict[str, Any]],
        detailed: bool = False,
        context: Optional[Dict[str, Any]] = None,
    ) -> RuntimePlan:
        """Bridge를 통해 MCP-aware 실행 계획을 생성한다."""
        _ = context
        try:
            # Classic runtime도 planning은 bridge를 통해 공통 LLM 계층을 사용한다.
            prompt = build_planner_prompt(command=command, soul=soul, mcp_catalog=mcp_catalog, detailed=detailed)
            raw, _ = await invoke_bridge_text(model=os.getenv("CODEX_CHAT_MODEL", "default"), prompt=prompt)
            payload = extract_json_object(raw)
            items = payload.get("plan")
            if isinstance(items, list) and items:
                return RuntimePlan(
                    objective=command.strip(),
                    summary="요청된 목표를 수행하기 위한 승인 전 실행 계획입니다.",
                    proposed_tasks=normalize_plan_steps(
                        validate_runtime_plan_mcp_ids(parse_runtime_tasks(items), mcp_catalog)
                    ),
                )
        except Exception:
            pass

        return RuntimePlan(
            objective=command.strip(),
            summary="요청된 목표를 수행하기 위한 승인 전 실행 계획입니다.",
            proposed_tasks=normalize_plan_steps(fallback_plan(command, detailed)),
        )

    async def execute_plan(
        self,
        plan: RuntimePlan,
        context: Optional[Dict[str, Any]] = None,
    ) -> RuntimeExecutionResult:
        """승인된 plan의 proposed task들을 그대로 실행한다."""
        return await self.execute_tasks(plan.proposed_tasks, context)

    async def execute_tasks(
        self,
        tasks: List[RuntimeTask],
        context: Optional[Dict[str, Any]] = None,
    ) -> RuntimeExecutionResult:
        """구조화된 task 배열을 순차 실행하고 단일 보고서를 만든다."""
        context = context or {}
        execution_log: List[str] = []
        evidence: List[str] = []
        findings: List[str] = []
        result_items: List[str] = []
        task_statuses: List[str] = []

        for index, task in enumerate(tasks, start=1):
            execution_log.append(f"Task {index}: {task.title}")
            selected_mcp_id = task.selected_mcp_id
            tool_name = task.tool_name
            try:
                # 현재 실동작하는 MCP executor는 Filesystem 쪽이며, 나머지 MCP는 후속 확장 대상이다.
                if selected_mcp_id == "filesystem" and tool_name in {"list_directory", "directory_tree", "read_text_file"}:
                    skill_result = await execute_filesystem_task(
                        task=task,
                        call_tool=self.call_filesystem_mcp,
                        extract_tool_text=self.extract_tool_text,
                        home_root=self.config.home_root,
                        project_root=self.config.project_root,
                    )
                    evidence.extend(skill_result["evidence"])
                    findings.extend(skill_result["findings"])
                    if skill_result["result_items"]:
                        result_items = skill_result["result_items"]
                    execution_log.append(skill_result["log"])
                    task_statuses.append("done")
                    continue

                evidence.append(
                    f"실행 계획 미확정: {task.title} (selected_mcp_id={selected_mcp_id or '없음'}, tool_name={tool_name or '없음'})"
                )
                findings.append(f"{task.title}: MCP와 tool이 구조화되어 내려오지 않아 실제 호출 근거를 만들지 못했습니다.")
                task_statuses.append("failed")
            except Exception as exc:
                evidence.append(f"실행 실패: {task.title} - {exc}")
                findings.append(f"{task.title}: 실행 중 오류가 발생했습니다. 오류 내용: {exc}")
                execution_log.append(f"실행 실패: {task.title}")
                task_statuses.append("failed")

        used_mcp_ids = list(dict.fromkeys(task.selected_mcp_id for task in tasks if task.selected_mcp_id))
        mcp_catalog = context.get("mcp_catalog", [])
        used_mcp_names = [
            str(item.get("name"))
            for item in mcp_catalog
            if item.get("id") in used_mcp_ids and item.get("name")
        ]
        report = self.build_execution_report(tasks, used_mcp_names, evidence, findings, result_items)
        execution_log.append("모든 태스크 수행이 끝났습니다.")

        return RuntimeExecutionResult(
            status="completed" if all(status == "done" for status in task_statuses) else "failed",
            execution_log=execution_log,
            findings=findings,
            result_items=result_items,
            evidence=evidence,
            report=report,
            task_statuses=task_statuses,
        )

    async def call_filesystem_mcp(self, tool_name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
        """Filesystem MCP stdio 서버를 띄워 단일 tool call을 수행한다."""
        if not self.config.filesystem_mcp_bin.exists():
            raise RuntimeError("Filesystem MCP binary is not installed.")

        command = [
            str(self.config.filesystem_mcp_bin),
            str(self.config.project_root),
            str(self.config.home_root),
        ]
        async with StdioMcpClient(command, self.config.mcp_protocol_version) as client:
            return await client.request("tools/call", {"name": tool_name, "arguments": arguments})

    @staticmethod
    def extract_tool_text(result: Dict[str, Any]) -> str:
        """MCP tool result에서 사람이 읽을 수 있는 핵심 텍스트만 뽑는다."""
        content = result.get("content")
        if isinstance(content, list):
            texts = [item.get("text", "") for item in content if isinstance(item, dict) and item.get("type") == "text"]
            joined = "\n".join(text for text in texts if text.strip())
            if joined.strip():
                return joined.strip()

        structured = result.get("structuredContent")
        if isinstance(structured, dict):
            content_text = structured.get("content")
            if isinstance(content_text, str) and content_text.strip():
                return content_text.strip()

        return json.dumps(result, ensure_ascii=False)

    @staticmethod
    def build_execution_report(
        tasks: List[RuntimeTask],
        used_mcp_names: List[str],
        evidence: List[str],
        findings: List[str],
        result_items: List[str],
    ) -> Dict[str, Any]:
        """실행 로그를 UI 보고 카드에 맞는 단일 report 딕셔너리로 정리한다."""
        task_titles = [f"Task {index}. {task.title}" for index, task in enumerate(tasks, start=1)]
        if findings:
            conclusion = "요청한 실행은 완료되었고, 아래 발견 사항을 기준으로 후속 판단이 가능한 상태입니다."
        else:
            conclusion = "요청한 실행은 완료되었지만, 아직 의미 있는 발견 사항을 구조화하지 못했습니다."

        return {
            "status": "보고 완료",
            "summary": f"보고합니다. 총 {len(tasks)}개의 실행 태스크를 수행했고, 사용 MCP는 {', '.join(used_mcp_names) or '없음'}입니다.",
            "objective": "지령에 따라 필요한 실행을 수행하고, 실행 결과에서 확인된 사실과 결론을 보고합니다.",
            "tasks": task_titles,
            "result_items": result_items,
            "findings": findings,
            "conclusion": conclusion,
            "evidence": evidence,
            "nextAction": "필요하면 이 보고를 기준으로 추가 조사, 수정, 또는 후속 지령을 내리십시오.",
        }
