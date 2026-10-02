from __future__ import annotations

import secrets
import sys
from typing import Any
import httpx

PROTOCOLO = "2026-07-28"
CLIENT_INFO = {
    "name": "agente-central-de-salas",
    "version": "1.0.0",
}
CAPABILITIES = {
    "elicitation": {
        "form": {},
    }
}


class McpClient:
    def __init__(self, base_url: str = "http://localhost:7301"):
        self.base_url = base_url.rstrip("/")
        self.endpoint = f"{self.base_url}/mcp"
        self._http = httpx.AsyncClient(timeout=30.0)
        self.discovered_tools: list[dict[str, Any]] = []
        self.politica_versao: str | None = None

    async def close(self):
        await self._http.aclose()

    def _build_meta(self, traceparent: str | None = None) -> dict[str, Any]:
        meta: dict[str, Any] = {
            "io.modelcontextprotocol/protocolVersion": PROTOCOLO,
            "io.modelcontextprotocol/clientInfo": CLIENT_INFO,
            "io.modelcontextprotocol/clientCapabilities": CAPABILITIES,
        }
        if traceparent:
            # Propagate trace-id with new span-id
            parts = traceparent.split("-")
            if len(parts) == 4:
                span_id = secrets.token_hex(8)
                meta["traceparent"] = f"{parts[0]}-{parts[1]}-{span_id}-{parts[3]}"
            else:
                meta["traceparent"] = traceparent
        return meta

    async def _post(
        self,
        method: str,
        params: dict[str, Any],
        mcp_name: str | None = None,
        traceparent: str | None = None,
        req_id: Any | None = None,
    ) -> dict[str, Any]:
        if req_id is None:
            req_id = secrets.token_hex(6)

        meta = self._build_meta(traceparent)
        full_params = {**params, "_meta": meta}

        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "MCP-Protocol-Version": PROTOCOLO,
            "Mcp-Method": method,
        }
        if mcp_name:
            headers["Mcp-Name"] = mcp_name

        payload = {
            "jsonrpc": "2.0",
            "id": req_id,
            "method": method,
            "params": full_params,
        }

        resp = await self._http.post(self.endpoint, json=payload, headers=headers)
        try:
            return resp.json()
        except Exception:
            return {"_bruto": resp.text, "http_status": resp.status_code}

    async def discover_tools(self, traceparent: str | None = None) -> list[dict[str, Any]]:
        res = await self._post("tools/list", {}, traceparent=traceparent)
        tools = (res.get("result") or {}).get("tools", [])
        self.discovered_tools = tools
        return tools

    async def read_politica(self, traceparent: str | None = None) -> str:
        res = await self._post("resources/read", {"uri": "politica://uso"}, mcp_name="politica://uso", traceparent=traceparent)
        contents = (res.get("result") or {}).get("contents", [{}])
        text = contents[0].get("text", "")
        # Extrai primeira linha (ex: versao: 2026-11-01)
        for line in text.splitlines():
            if line.startswith("versao:"):
                self.politica_versao = line.replace("versao:", "").strip()
                break
        return self.politica_versao or "2026-11-01"

    async def call_reservar_sala(
        self,
        arguments: dict[str, Any],
        traceparent: str | None = None,
        req_id: Any | None = None,
        input_responses: dict[str, Any] | None = None,
        request_state: str | None = None,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {
            "name": "reservar_sala",
            "arguments": arguments,
        }
        if input_responses is not None:
            params["inputResponses"] = input_responses
        if request_state is not None:
            params["requestState"] = request_state

        return await self._post(
            "tools/call",
            params,
            mcp_name="reservar_sala",
            traceparent=traceparent,
            req_id=req_id,
        )
