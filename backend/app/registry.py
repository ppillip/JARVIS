from __future__ import annotations

"""MCP registry를 조회/추가/활성화하는 전용 서비스."""

from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from app.sqlite_store import create_registry_entry, list_registry_entries, update_registry_enabled

app = FastAPI(title="JARVIS MCP Registry")


class RegistryEntryUpdate(BaseModel):
    """레지스트리 항목의 활성 상태 변경 요청."""

    enabled: bool


class RegistryEntryCreate(BaseModel):
    """신규 MCP 레지스트리 항목 생성 요청."""

    id: str
    name: str
    scope: str
    description: str
    capabilities: List[str]
    expected_input: str
    expected_output: str
    source_url: Optional[str] = None
    package_name: Optional[str] = None
    transport: Optional[str] = None
    auth_required: bool = False
    risk_level: str = "low"
    enabled: bool = True
@app.get("/health")
def health_check() -> Dict[str, str]:
    """레지스트리 서버 상태를 반환한다."""
    return {"status": "ok"}


@app.get("/registry/mcps")
def list_mcps() -> List[Dict[str, Any]]:
    """저장된 MCP 레지스트리 전체를 반환한다."""
    return list_registry_entries()


@app.post("/registry/mcps")
def create_mcp(entry: RegistryEntryCreate) -> Dict[str, Any]:
    """새 MCP 레지스트리 항목을 생성한다."""
    try:
        return create_registry_entry(entry.model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.patch("/registry/mcps/{mcp_id}")
def update_mcp(mcp_id: str, payload: RegistryEntryUpdate) -> Dict[str, Any]:
    """레지스트리 항목의 활성/비활성 상태를 변경한다."""
    try:
        return update_registry_enabled(mcp_id, payload.enabled)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
