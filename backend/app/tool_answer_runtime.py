from __future__ import annotations

"""조회형 질문을 MCP 결과 기반 답변으로 변환하는 runtime."""

import json
import os
from types import SimpleNamespace
from typing import Any, Dict, List

from app.capability_router import choose_retrieval_mcp
from app.classic_runtime import StdioMcpClient, load_classic_runtime_config
from app.filesystem_skill import execute_filesystem_task
from app.llm_bridge import extract_json_object, invoke_bridge_text
from app.prompt_store import get_prompt_content, render_prompt_template


class ToolAnswerRuntime:
    """질문형 요청 중 retrieval MCP가 적합한 케이스를 처리한다."""

    def __init__(self) -> None:
        self.config = load_classic_runtime_config()

    @staticmethod
    def extract_tool_text(result: Dict[str, Any]) -> str:
        """MCP tool 결과에서 핵심 텍스트를 뽑는다."""
        content = result.get("content")
        if isinstance(content, list):
            texts = [item.get("text", "") for item in content if isinstance(item, dict) and item.get("type") == "text"]
            joined = "\n".join(text for text in texts if str(text).strip())
            if joined.strip():
                return joined.strip()

        structured = result.get("structuredContent")
        if isinstance(structured, dict):
            content_text = structured.get("content")
            if isinstance(content_text, str) and content_text.strip():
                return content_text.strip()

        return json.dumps(result, ensure_ascii=False)

    async def _call_korean_law(self, tool_name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
        law_oc = str(os.getenv("LAW_OC", "")).strip()
        if not law_oc:
            raise RuntimeError("LAW_OC 환경변수가 없어 법령 조회형 질문을 처리할 수 없습니다.")

        env = os.environ.copy()
        env["LAW_OC"] = law_oc
        async with StdioMcpClient([self.config.korean_law_mcp_command], self.config.mcp_protocol_version, env=env) as client:
            return await client.request("tools/call", {"name": tool_name, "arguments": arguments})

    async def _call_filesystem(self, tool_name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
        if not self.config.filesystem_mcp_bin.exists():
            raise RuntimeError("Filesystem MCP binary is not installed.")
        command = [
            str(self.config.filesystem_mcp_bin),
            str(self.config.project_root),
            str(self.config.home_root),
        ]
        async with StdioMcpClient(command, self.config.mcp_protocol_version) as client:
            return await client.request("tools/call", {"name": tool_name, "arguments": arguments})

    def _select_korean_law_tool(self, message: str) -> tuple[str, Dict[str, Any]]:
        normalized = message.strip().lower()
        query = message.strip()
        if "판례" in normalized:
            return "search_precedents", {"query": query}
        if "법령해석" in normalized:
            return "search_interpretations", {"query": query}
        return "search_all", {"query": query}

    async def _plan_filesystem_lookup(self, model: str, message: str) -> Dict[str, Any]:
        template = get_prompt_content(
            "filesystem_tool_answer_planner",
            fallback=(
                "너는 JARVIS의 filesystem retrieval micro-planner다.\n"
                "사용자 질문을 읽고 Filesystem MCP의 조회형 tool 하나를 선택한다.\n"
                "허용 tool은 list_directory, directory_tree, read_text_file 중 하나다.\n"
                "수정형 tool은 절대 선택하지 않는다.\n"
                "path는 $HOME, $PROJECT_ROOT 변수를 사용할 수 있다.\n"
                "Downloads/다운로드 폴더처럼 홈 디렉터리 하위 폴더는 $HOME/Downloads 형태를 우선 사용한다.\n"
                "반드시 JSON 객체 하나만 출력한다.\n"
                '형식: {"tool_name":"list_directory","tool_arguments":{"path":"$HOME/Downloads"},"expected_result":"다운로드 폴더 하위 폴더 개수"}\n\n'
                "[사용자 질문]\n{{message}}"
            ),
        )
        prompt = render_prompt_template(template, {"message": message.strip()})
        text, _ = await invoke_bridge_text(model=model, prompt=prompt)
        return extract_json_object(text)

    async def _answer_with_filesystem(
        self,
        *,
        model: str,
        message: str,
        selected_mcp: str,
    ) -> Dict[str, Any]:
        plan = await self._plan_filesystem_lookup(model=model, message=message)
        tool_name = str(plan.get("tool_name") or "").strip() or "list_directory"
        tool_arguments = plan.get("tool_arguments") if isinstance(plan.get("tool_arguments"), dict) else {}
        expected_result = str(plan.get("expected_result") or "파일시스템 조회 결과").strip()

        task = SimpleNamespace(
            title=message.strip(),
            tool_name=tool_name,
            tool_arguments=tool_arguments,
            expected_result=expected_result,
        )
        interpreted = await execute_filesystem_task(
            task=task,
            call_tool=self._call_filesystem,
            extract_tool_text=self.extract_tool_text,
            home_root=self.config.home_root,
            project_root=self.config.project_root,
        )
        findings = "\n".join(interpreted.get("findings", []))
        evidence = "\n".join(interpreted.get("evidence", []))
        result_items = "\n".join(str(item) for item in interpreted.get("result_items", [])[:20])

        template = get_prompt_content(
            "tool_answer_system",
            fallback=(
                "너는 JARVIS의 조회형 답변 엔진이다.\n"
                "사용자 질문과 MCP 조회 결과를 바탕으로 근거 중심의 짧고 실제로 도움이 되는 답변을 작성한다.\n"
                "모르면 추측하지 말고, 조회 결과의 한계를 짧게 밝힌다.\n"
                "한국어로 답한다.\n\n"
                "[사용자 질문]\n{{message}}\n\n"
                "[사용한 MCP]\n{{mcp_id}}\n\n"
                "[조회 findings]\n{{findings}}\n\n"
                "[조회 evidence]\n{{evidence}}\n\n"
                "[result items]\n{{result_items}}"
            ),
        )
        prompt = render_prompt_template(
            template,
            {
                "message": message.strip(),
                "mcp_id": selected_mcp,
                "findings": findings,
                "evidence": evidence,
                "result_items": result_items,
            },
        )
        reply, response_id = await invoke_bridge_text(model=model, prompt=prompt)
        return {
            "reply": reply,
            "response_id": response_id,
            "selected_mcp_id": selected_mcp,
            "tool_name": tool_name,
            "tool_arguments": tool_arguments,
            "evidence_preview": evidence[:1200],
        }

    async def answer_question(
        self,
        *,
        model: str,
        message: str,
        required_capabilities: List[str],
        mcp_catalog: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """질문을 tool-backed answer로 처리한다."""
        selected_mcp = choose_retrieval_mcp(required_capabilities, mcp_catalog)
        if selected_mcp == "filesystem":
            return await self._answer_with_filesystem(model=model, message=message, selected_mcp=selected_mcp)
        if selected_mcp != "korean_law":
            raise RuntimeError("현재 연결된 retrieval capability로는 이 조회형 질문을 직접 처리할 수 없습니다.")

        tool_name, tool_arguments = self._select_korean_law_tool(message)
        raw_result = await self._call_korean_law(tool_name, tool_arguments)
        tool_text = self.extract_tool_text(raw_result)

        template = get_prompt_content(
            "tool_answer_system",
            fallback=(
                "너는 JARVIS의 조회형 답변 엔진이다.\n"
                "사용자 질문과 MCP 조회 결과를 바탕으로 근거 중심의 짧고 실제로 도움이 되는 답변을 작성한다.\n"
                "모르면 추측하지 말고, 조회 결과의 한계를 짧게 밝힌다.\n"
                "법률 자문처럼 단정하지 말고, 조회 결과에 근거한 요약으로 답한다.\n"
                "한국어로 답한다.\n\n"
                "[사용자 질문]\n{{message}}\n\n"
                "[사용한 MCP]\n{{mcp_id}}\n\n"
                "[조회 결과]\n{{tool_text}}"
            ),
        )
        prompt = render_prompt_template(
            template,
            {
                "message": message.strip(),
                "mcp_id": selected_mcp,
                "tool_text": tool_text,
            },
        )
        reply, response_id = await invoke_bridge_text(model=model, prompt=prompt)
        return {
            "reply": reply,
            "response_id": response_id,
            "selected_mcp_id": selected_mcp,
            "tool_name": tool_name,
            "tool_arguments": tool_arguments,
            "evidence_preview": tool_text[:1200],
        }
