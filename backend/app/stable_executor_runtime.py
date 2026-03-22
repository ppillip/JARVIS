from __future__ import annotations

"""승인된 태스크를 안정적으로 실행하는 executor 구현.

planner 책임을 갖지 않고, 확정된 task만 집행하며 evidence, trace, report를
안정적으로 축적하는 집행 전용 런타임이다.
"""

from typing import Any, Dict, List, Optional

from app.agent_runtime import ExecutorRuntime, RuntimeExecutionResult, RuntimePlan, RuntimeTask
from app.classic_runtime import ClassicRuntimeConfig, StdioMcpClient, load_classic_runtime_config
from app.filesystem_skill import execute_filesystem_task
from app.report_builder import build_execution_report
from app.trace_logger import add_trace


class StableExecutorRuntime(ExecutorRuntime):
    """Planning 책임 없이 확정된 task만 집행하는 안정 실행기."""

    def __init__(self, config: Optional[ClassicRuntimeConfig] = None) -> None:
        """Filesystem MCP 실행에 필요한 안정 경로 설정을 보관한다."""
        self.config = config or load_classic_runtime_config()

    async def execute_plan(
        self,
        plan: RuntimePlan,
        context: Optional[Dict[str, Any]] = None,
    ) -> RuntimeExecutionResult:
        """승인된 plan에서 proposed task를 읽어 그대로 실행한다."""
        return await self.execute_tasks(plan.proposed_tasks, context)

    async def execute_tasks(
        self,
        tasks: List[RuntimeTask],
        context: Optional[Dict[str, Any]] = None,
    ) -> RuntimeExecutionResult:
        """구조화된 task 배열을 순차 실행하고 evidence/report를 축적한다."""
        context = context or {}
        add_trace(
            context,
            "executor.started",
            stage="execution",
            runtime="stable_executor",
            task_count=len(tasks),
        )
        execution_log: List[str] = []
        evidence: List[str] = []
        findings: List[str] = []
        result_items: List[str] = []
        task_statuses: List[str] = []

        for index, task in enumerate(tasks, start=1):
            execution_log.append(f"Task {index}: {task.title}")
            selected_mcp_id = task.selected_mcp_id
            tool_name = task.tool_name
            add_trace(
                context,
                "executor.task_started",
                stage="execution",
                runtime="stable_executor",
                task_index=index,
                title=task.title,
                selected_mcp_id=selected_mcp_id,
                tool_name=tool_name,
            )
            try:
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
                    add_trace(
                        context,
                        "executor.task_completed",
                        stage="execution",
                        runtime="stable_executor",
                        task_index=index,
                        title=task.title,
                        result="success",
                        selected_mcp_id=selected_mcp_id,
                        tool_name=tool_name,
                    )
                    continue

                evidence.append(
                    f"실행 계획 미확정: {task.title} (selected_mcp_id={selected_mcp_id or '없음'}, tool_name={tool_name or '없음'})"
                )
                findings.append(f"{task.title}: MCP와 tool이 구조화되어 내려오지 않아 실제 호출 근거를 만들지 못했습니다.")
                task_statuses.append("failed")
                add_trace(
                    context,
                    "executor.task_failed",
                    stage="execution",
                    runtime="stable_executor",
                    task_index=index,
                    title=task.title,
                    result="unbound_task",
                    selected_mcp_id=selected_mcp_id,
                    tool_name=tool_name,
                )
            except Exception as exc:
                evidence.append(f"실행 실패: {task.title} - {exc}")
                findings.append(f"{task.title}: 실행 중 오류가 발생했습니다. 오류 내용: {exc}")
                execution_log.append(f"실행 실패: {task.title}")
                task_statuses.append("failed")
                add_trace(
                    context,
                    "executor.task_failed",
                    stage="execution",
                    runtime="stable_executor",
                    task_index=index,
                    title=task.title,
                    result="exception",
                    error=str(exc),
                    selected_mcp_id=selected_mcp_id,
                    tool_name=tool_name,
                )

        used_mcp_ids = list(dict.fromkeys(task.selected_mcp_id for task in tasks if task.selected_mcp_id))
        mcp_catalog = context.get("mcp_catalog", [])
        used_mcp_names = [
            str(item.get("name"))
            for item in mcp_catalog
            if item.get("id") in used_mcp_ids and item.get("name")
        ]
        execution_log.append("모든 태스크 수행이 끝났습니다.")
        add_trace(
            context,
            "report.built",
            stage="reporting",
            runtime="stable_executor",
            findings_count=len(findings),
            evidence_count=len(evidence),
            result_items_count=len(result_items),
        )
        add_trace(
            context,
            "executor.completed",
            stage="execution",
            runtime="stable_executor",
            status="completed" if all(status == "done" for status in task_statuses) else "failed",
        )

        return RuntimeExecutionResult(
            status="completed" if all(status == "done" for status in task_statuses) else "failed",
            execution_log=execution_log,
            findings=findings,
            result_items=result_items,
            evidence=evidence,
            report=build_execution_report(tasks, used_mcp_names, evidence, findings, result_items),
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

        import json

        return json.dumps(result, ensure_ascii=False)
