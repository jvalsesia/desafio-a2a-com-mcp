from __future__ import annotations

import json
import re
import secrets
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from mcp_client import McpClient
from task_manager import TaskManager

task_manager = TaskManager()
mcp_client = McpClient()

AGENT_CARD = {
    "name": "Central de Salas",
    "description": "Reserva salas de reuniao da Hill Valley Tech.",
    "provider": {
        "organization": "Hill Valley Tech",
        "url": "https://hillvalley.example",
    },
    "version": "1.0.0",
    "supportedInterfaces": [
        {
            "url": "http://127.0.0.1:7300/a2a",
            "protocolBinding": "JSONRPC",
            "protocolVersion": "1.0",
        }
    ],
    "capabilities": {
        "streaming": False,
        "pushNotifications": False,
        "extendedAgentCard": False,
    },
    "defaultInputModes": [
        "text/plain",
    ],
    "defaultOutputModes": [
        "text/plain",
    ],
    "skills": [
        {
            "id": "reservar-sala",
            "name": "Reservar sala",
            "description": "Reserva uma sala em um intervalo. Se houver conflito, pergunta qual alternativa usar.",
            "tags": [
                "salas",
                "agenda",
            ],
            "inputModes": [
                "text/plain",
            ],
            "outputModes": [
                "text/plain",
            ],
            "examples": [
                "reservar sala=sala-garagem inicio=2026-11-03T14:00:00-03:00 fim=2026-11-03T15:00:00-03:00 responsavel=Marty",
            ],
        }
    ],
}


async def handle_agent_card(request: Request) -> JSONResponse:
    return JSONResponse(AGENT_CARD)


async def handle_a2a(request: Request) -> JSONResponse:
    if request.method != "POST":
        return JSONResponse({"error": "Method Not Allowed"}, status_code=405)

    try:
        body = await request.json()
    except Exception:
        return JSONResponse(
            {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Parse error"}},
            status_code=400,
        )

    req_id = body.get("id")
    method = body.get("method")
    params = body.get("params") or {}
    traceparent = request.headers.get("traceparent")

    if method == "GetTask":
        task_id = params.get("id") or params.get("taskId")
        task = task_manager.get_task(task_id) if task_id else None
        if not task:
            return JSONResponse(
                {"jsonrpc": "2.0", "id": req_id, "error": {"code": -32602, "message": "Task nao encontrada"}},
                status_code=200,
            )
        return JSONResponse({"jsonrpc": "2.0", "id": req_id, "result": {"task": task.to_dict()}})

    if method == "SendMessage":
        message = params.get("message") or {}
        task_id = message.get("taskId")
        parts = message.get("parts") or []
        text = " ".join(p.get("text", "") for p in parts).strip()

        if not task_id:
            # Novo pedido de reserva
            task = task_manager.create_task()
            task.state = "TASK_STATE_WORKING"
            task.traceparent = traceparent
            task.history.append(message)

            # Extrai argumentos do comando: reservar sala=<id> inicio=<iso> fim=<iso> responsavel=<nome>
            match_sala = re.search(r"sala=(\S+)", text)
            match_inicio = re.search(r"inicio=(\S+)", text)
            match_fim = re.search(r"fim=(\S+)", text)
            match_resp = re.search(r"responsavel=(.+)$", text)

            sala = match_sala.group(1) if match_sala else ""
            inicio = match_inicio.group(1) if match_inicio else ""
            fim = match_fim.group(1) if match_fim else ""
            responsavel = match_resp.group(1).strip() if match_resp else "Validador"

            args = {
                "sala": sala,
                "inicio": inicio,
                "fim": fim,
                "responsavel": responsavel,
            }
            task.original_arguments = args

            # Descoberta runtime obrigatória se ainda não executada
            if not mcp_client.discovered_tools:
                await mcp_client.discover_tools(traceparent=traceparent)
            if not mcp_client.politica_versao:
                await mcp_client.read_politica(traceparent=traceparent)

            # Chama tool de reserva no servidor MCP
            mcp_res = await mcp_client.call_reservar_sala(args, traceparent=traceparent)
            res_result = mcp_res.get("result") or {}

            # Caso 1: Erro de execução da tool (ex: Sala inexistente)
            if res_result.get("isError"):
                err_text = " ".join(p.get("text", "") for p in res_result.get("content", []))
                task.state = "TASK_STATE_FAILED"
                agent_msg = {
                    "messageId": f"msg-{secrets.token_hex(6)}",
                    "role": "ROLE_AGENT",
                    "parts": [{"text": err_text}],
                    "taskId": task.id,
                    "contextId": task.context_id,
                }
                task.status_message = agent_msg
                task.history.append(agent_msg)
                return JSONResponse({"jsonrpc": "2.0", "id": req_id, "result": {"task": task.to_dict()}})

            # Caso 2: Conflito / input_required (A PONTE - Pausa da Task)
            if res_result.get("resultType") == "input_required":
                task.request_state = res_result.get("requestState")
                input_requests = res_result.get("inputRequests") or {}
                task.input_key = next(iter(input_requests.keys()), "__main__:escolha_de_sala")

                req_params = input_requests[task.input_key].get("params", {})
                props = (
                    req_params.get("requestedSchema", {}).get("properties", {}).get("sala", {})
                )
                alternatives = props.get("enum") or ([props["const"]] if "const" in props else [])
                task.alternatives = alternatives

                task.state = "TASK_STATE_INPUT_REQUIRED"
                alt_text = f"alternativas: {', '.join(alternatives)}"
                agent_msg = {
                    "messageId": f"msg-{secrets.token_hex(6)}",
                    "role": "ROLE_AGENT",
                    "parts": [{"text": alt_text}],
                    "taskId": task.id,
                    "contextId": task.context_id,
                }
                task.status_message = agent_msg
                task.history.append(agent_msg)
                return JSONResponse({"jsonrpc": "2.0", "id": req_id, "result": {"task": task.to_dict()}})

            # Caso 3: Reserva livre concluída de primeira
            if res_result.get("resultType") == "complete":
                structured = res_result.get("structuredContent") or {}
                task.state = "TASK_STATE_COMPLETED"
                agent_msg = {
                    "messageId": f"msg-{secrets.token_hex(6)}",
                    "role": "ROLE_AGENT",
                    "parts": [
                        {
                            "text": f"Reserva {structured.get('reserva')} confirmada na {structured.get('sala')}."
                        }
                    ],
                    "taskId": task.id,
                    "contextId": task.context_id,
                }
                task.status_message = agent_msg
                task.history.append(agent_msg)
                artifact = {
                    "artifactId": f"art-{secrets.token_hex(6)}",
                    "name": "reserva",
                    "parts": [{"text": json.dumps(structured)}],
                }
                task.artifacts = [artifact]
                return JSONResponse({"jsonrpc": "2.0", "id": req_id, "result": {"task": task.to_dict()}})

        else:
            # Continuação de Task existente (SendMessage com taskId)
            task = task_manager.get_task(task_id)
            if not task:
                return JSONResponse(
                    {"jsonrpc": "2.0", "id": req_id, "error": {"code": -32602, "message": "Task nao encontrada"}},
                    status_code=200,
                )

            # Estado terminal é definitivo
            if task.is_terminal():
                return JSONResponse(
                    {
                        "jsonrpc": "2.0",
                        "id": req_id,
                        "error": {
                            "code": -32000,
                            "message": f"Task {task_id} esta em estado terminal ({task.state}) e nao aceita novas mensagens.",
                        },
                    },
                    status_code=200,
                )

            # Parser de escolha=<valor>
            match_escolha = re.search(r"escolha=(\S+)", text)
            escolha = match_escolha.group(1).strip() if match_escolha else ""

            if escolha == "recusar":
                task.history.append(message)
                # A PONTE - Retomada do MCP com declínio (novo id JSON-RPC)
                retry_id = secrets.token_hex(6)
                input_responses = {
                    task.input_key: {"action": "decline"}
                }
                await mcp_client.call_reservar_sala(
                    task.original_arguments,
                    traceparent=traceparent or task.traceparent,
                    req_id=retry_id,
                    input_responses=input_responses,
                    request_state=task.request_state,
                )
                task.state = "TASK_STATE_CANCELED"
                agent_msg = {
                    "messageId": f"msg-{secrets.token_hex(6)}",
                    "role": "ROLE_AGENT",
                    "parts": [{"text": "Reserva recusada pelo usuario."}],
                    "taskId": task.id,
                    "contextId": task.context_id,
                }
                task.status_message = agent_msg
                task.history.append(agent_msg)
                return JSONResponse({"jsonrpc": "2.0", "id": req_id, "result": {"task": task.to_dict()}})

            elif escolha in task.alternatives:
                task.history.append(message)
                # A PONTE - Retomada do MCP com aceite da alternativa (novo id JSON-RPC)
                retry_id = secrets.token_hex(6)
                input_responses = {
                    task.input_key: {
                        "action": "accept",
                        "content": {"sala": escolha},
                    }
                }
                mcp_res = await mcp_client.call_reservar_sala(
                    task.original_arguments,
                    traceparent=traceparent or task.traceparent,
                    req_id=retry_id,
                    input_responses=input_responses,
                    request_state=task.request_state,
                )
                structured = (mcp_res.get("result") or {}).get("structuredContent") or {}
                task.state = "TASK_STATE_COMPLETED"
                agent_msg = {
                    "messageId": f"msg-{secrets.token_hex(6)}",
                    "role": "ROLE_AGENT",
                    "parts": [
                        {
                            "text": f"Reserva {structured.get('reserva')} confirmada na {structured.get('sala')}."
                        }
                    ],
                    "taskId": task.id,
                    "contextId": task.context_id,
                }
                task.status_message = agent_msg
                task.history.append(agent_msg)
                artifact = {
                    "artifactId": f"art-{secrets.token_hex(6)}",
                    "name": "reserva",
                    "parts": [{"text": json.dumps(structured)}],
                }
                task.artifacts = [artifact]
                return JSONResponse({"jsonrpc": "2.0", "id": req_id, "result": {"task": task.to_dict()}})

            else:
                # Escolha fora do enum mantém a Task em TASK_STATE_INPUT_REQUIRED e repete alternativas
                task.history.append(message)
                agent_msg = {
                    "messageId": f"msg-{secrets.token_hex(6)}",
                    "role": "ROLE_AGENT",
                    "parts": [{"text": f"alternativas: {', '.join(task.alternatives)}"}],
                    "taskId": task.id,
                    "contextId": task.context_id,
                }
                task.status_message = agent_msg
                task.history.append(agent_msg)
                return JSONResponse({"jsonrpc": "2.0", "id": req_id, "result": {"task": task.to_dict()}})

    return JSONResponse(
        {"jsonrpc": "2.0", "id": req_id, "error": {"code": -32601, "message": f"Metodo nao suportado: {method}"}},
        status_code=200,
    )


routes = [
    Route("/.well-known/agent-card.json", handle_agent_card, methods=["GET"]),
    Route("/a2a", handle_a2a, methods=["POST"]),
]

app = Starlette(routes=routes)
