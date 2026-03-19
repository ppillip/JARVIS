from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel


PROJECT_ROOT = Path(__file__).resolve().parents[2]
MCP_REGISTRY_PATH = PROJECT_ROOT / "backend" / "app" / "mcp_registry.json"

app = FastAPI(title="JARVIS MCP Registry")


class RegistryEntryUpdate(BaseModel):
    enabled: bool


class RegistryEntryCreate(BaseModel):
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


def load_registry_entries() -> List[Dict[str, Any]]:
    try:
        raw = json.loads(MCP_REGISTRY_PATH.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise HTTPException(status_code=500, detail="Registry file is missing.") from exc
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=500, detail="Registry file is invalid JSON.") from exc

    if not isinstance(raw, list):
        raise HTTPException(status_code=500, detail="Registry file must contain a list.")
    return raw


def save_registry_entries(entries: List[Dict[str, Any]]) -> None:
    MCP_REGISTRY_PATH.write_text(json.dumps(entries, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


@app.get("/health")
def health_check() -> Dict[str, str]:
    return {"status": "ok"}


@app.get("/registry/mcps")
def list_mcps() -> List[Dict[str, Any]]:
    return load_registry_entries()


@app.post("/registry/mcps")
def create_mcp(entry: RegistryEntryCreate) -> Dict[str, Any]:
    entries = load_registry_entries()
    if any(item.get("id") == entry.id for item in entries):
        raise HTTPException(status_code=409, detail="MCP id already exists.")
    payload = entry.model_dump()
    entries.append(payload)
    save_registry_entries(entries)
    return payload


@app.patch("/registry/mcps/{mcp_id}")
def update_mcp(mcp_id: str, payload: RegistryEntryUpdate) -> Dict[str, Any]:
    entries = load_registry_entries()
    for item in entries:
        if item.get("id") == mcp_id:
            item["enabled"] = payload.enabled
            save_registry_entries(entries)
            return item
    raise HTTPException(status_code=404, detail="MCP not found.")
