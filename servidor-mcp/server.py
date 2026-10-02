from __future__ import annotations

import json
import sys
from typing import Any
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from crypto_state import InvalidRequestStateError, seal_request_state, unseal_request_state
from domain import DomainError, RoomManager

room_manager = RoomManager()

SERVER_INFO = {
    "io.modelcontextprotocol/serverInfo": {
        "name": "central-de-salas",
        "version": "1.0.0",
    }
}


def build_tools_list() -> list[dict[str, Any]]:
    return [
        {
            "name": "listar_salas",
            "description": "Lista todas as salas com capacidade e recursos.",
            "inputSchema": {
                "type": "object",
                "properties": {},
                "title": "listar_salasArguments",
            },
            "outputSchema": {
                "$defs": {
                    "SalaOut": {
                        "properties": {
                            "id": {"title": "Id", "type": "string"},
                            "nome": {"title": "Nome", "type": "string"},
                            "capacidade": {"title": "Capacidade", "type": "integer"},
                            "recursos": {
                                "items": {"type": "string"},
                                "title": "Recursos",
                                "type": "array",
                            },
                        },
                        "required": ["id", "nome", "capacidade", "recursos"],
                        "title": "SalaOut",
                        "type": "object",
                    }
                },
                "properties": {
                    "salas": {
                        "items": {"$ref": "#/$defs/SalaOut"},
                        "title": "Salas",
                        "type": "array",
                    }
                },
                "required": ["salas"],
                "title": "ListaDeSalas",
                "type": "object",
            },
        },
        {
            "name": "consultar_disponibilidade",
            "description": "Diz se uma sala esta livre no intervalo, e quais reservas conflitam.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "sala": {"title": "Sala", "type": "string"},
                    "inicio": {"title": "Inicio", "type": "string"},
                    "fim": {"title": "Fim", "type": "string"},
                },
                "required": ["sala", "inicio", "fim"],
                "title": "consultar_disponibilidadeArguments",
            },
            "outputSchema": {
                "$defs": {
                    "ConflitoOut": {
                        "properties": {
                            "id": {"title": "Id", "type": "string"},
                            "inicio": {"title": "Inicio", "type": "string"},
                            "fim": {"title": "Fim", "type": "string"},
                            "responsavel": {"title": "Responsavel", "type": "string"},
                        },
                        "required": ["id", "inicio", "fim", "responsavel"],
                        "title": "ConflitoOut",
                        "type": "object",
                    }
                },
                "properties": {
                    "sala": {"title": "Sala", "type": "string"},
                    "livre": {"title": "Livre", "type": "boolean"},
                    "conflitos": {
                        "items": {"$ref": "#/$defs/ConflitoOut"},
                        "title": "Conflitos",
                        "type": "array",
                    },
                },
                "required": ["sala", "livre", "conflitos"],
                "title": "Disponibilidade",
                "type": "object",
            },
        },
        {
            "name": "reservar_sala",
            "description": "Reserva uma sala. Se o intervalo estiver ocupado, pergunta qual alternativa usar.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "sala": {"title": "Sala", "type": "string"},
                    "inicio": {"title": "Inicio", "type": "string"},
                    "fim": {"title": "Fim", "type": "string"},
                    "responsavel": {"title": "Responsavel", "type": "string"},
                },
                "required": ["sala", "inicio", "fim", "responsavel"],
                "title": "reservar_salaArguments",
            },
            "outputSchema": {
                "properties": {
                    "reserva": {
                        "anyOf": [{"type": "string"}, {"type": "null"}],
                        "default": None,
                        "title": "Reserva",
                    },
                    "reservado": {
                        "default": True,
                        "title": "Reservado",
                        "type": "boolean",
                    },
                    "sala": {
                        "anyOf": [{"type": "string"}, {"type": "null"}],
                        "default": None,
                        "title": "Sala",
                    },
                    "inicio": {
                        "anyOf": [{"type": "string"}, {"type": "null"}],
                        "default": None,
                        "title": "Inicio",
                    },
                    "fim": {
                        "anyOf": [{"type": "string"}, {"type": "null"}],
                        "default": None,
                        "title": "Fim",
                    },
                    "responsavel": {
                        "anyOf": [{"type": "string"}, {"type": "null"}],
                        "default": None,
                        "title": "Responsavel",
                    },
                    "politica": {
                        "anyOf": [{"type": "string"}, {"type": "null"}],
                        "default": None,
                        "title": "Politica",
                    },
                    "motivo": {
                        "anyOf": [{"type": "string"}, {"type": "null"}],
                        "default": None,
                        "title": "Motivo",
                    },
                },
                "title": "ResultadoDaReserva",
                "type": "object",
            },
        },
    ]


async def handle_mcp(request: Request) -> JSONResponse:
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
    meta = params.get("_meta") if isinstance(params, dict) else {}
    if not isinstance(meta, dict):
        meta = {}

    traceparent = meta.get("traceparent")

    # Stderr logging requirement
    sys.stderr.write(f"MCP Request: method={method} id={req_id} traceparent={traceparent}\n")
    sys.stderr.flush()

    # Mirror header validation
    header_method = request.headers.get("Mcp-Method")
    if header_method and header_method != method:
        return JSONResponse(
            {"jsonrpc": "2.0", "id": req_id, "error": {"code": -32020, "message": "Mcp-Method header mismatch"}},
            status_code=200,
        )

    header_name = request.headers.get("Mcp-Name")
    if header_name:
        expected_name = params.get("name") if method == "tools/call" else params.get("uri")
        if expected_name and header_name != expected_name:
            return JSONResponse(
                {"jsonrpc": "2.0", "id": req_id, "error": {"code": -32020, "message": "Mcp-Name header mismatch"}},
                status_code=200,
            )

    # Mandatory _meta fields check
    has_proto = "io.modelcontextprotocol/protocolVersion" in meta
    has_caps = "io.modelcontextprotocol/clientCapabilities" in meta
    if not has_proto or not has_caps:
        return JSONResponse(
            {
                "jsonrpc": "2.0",
                "id": req_id,
                "error": {
                    "code": -32602,
                    "message": "Parametro _meta obrigatorio ausente (protocolVersion ou clientCapabilities)",
                },
            },
            status_code=400,
        )

    client_caps = meta.get("io.modelcontextprotocol/clientCapabilities") or {}

    # Method routing
    if method == "tools/list":
        return JSONResponse(
            {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "cacheScope": "private",
                    "resultType": "complete",
                    "tools": build_tools_list(),
                    "_meta": SERVER_INFO,
                },
            }
        )

    if method == "resources/read":
        uri = params.get("uri")
        if uri == "politica://uso":
            return JSONResponse(
                {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "result": {
                        "cacheScope": "private",
                        "contents": [
                            {
                                "mimeType": "text/markdown",
                                "text": room_manager.politica_texto,
                                "uri": "politica://uso",
                            }
                        ],
                        "resultType": "complete",
                        "ttlMs": 0,
                        "_meta": SERVER_INFO,
                    },
                }
            )
        else:
            return JSONResponse(
                {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "error": {
                        "code": -32602,
                        "message": f"Recurso nao encontrado: {uri}",
                    },
                }
            )

    if method == "tools/call":
        tool_name = params.get("name")
        args = params.get("arguments") or {}

        if tool_name == "listar_salas":
            structured = {"salas": room_manager.salas}
            text_block = json.dumps(structured, indent=2)
            return JSONResponse(
                {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "result": {
                        "content": [{"type": "text", "text": text_block}],
                        "isError": False,
                        "resultType": "complete",
                        "structuredContent": structured,
                        "_meta": SERVER_INFO,
                    },
                }
            )

        if tool_name == "consultar_disponibilidade":
            sala = args.get("sala", "")
            inicio_str = args.get("inicio", "")
            fim_str = args.get("fim", "")
            try:
                inicio, fim = room_manager.validate_interval(sala, inicio_str, fim_str)
            except DomainError as e:
                return JSONResponse(
                    {
                        "jsonrpc": "2.0",
                        "id": req_id,
                        "result": {
                            "content": [{"type": "text", "text": e.message}],
                            "isError": True,
                            "resultType": "complete",
                            "_meta": SERVER_INFO,
                        },
                    }
                )

            conflitos = room_manager.find_conflicts(sala, inicio, fim)
            if conflitos:
                conflitos_out = [
                    {
                        "id": c["id"],
                        "inicio": c["inicio"],
                        "fim": c["fim"],
                        "responsavel": c["responsavel"],
                    }
                    for c in conflitos
                ]
                structured = {"conflitos": conflitos_out, "livre": False, "sala": sala}
            else:
                structured = {"conflitos": [], "livre": True, "sala": sala}

            text_block = json.dumps(structured, indent=2)
            return JSONResponse(
                {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "result": {
                        "content": [{"type": "text", "text": text_block}],
                        "isError": False,
                        "resultType": "complete",
                        "structuredContent": structured,
                        "_meta": SERVER_INFO,
                    },
                }
            )

        if tool_name == "reservar_sala":
            # Check for retry case with requestState
            request_state_token = params.get("requestState")
            input_responses = params.get("inputResponses")

            if request_state_token is not None:
                # Unseal and verify requestState
                try:
                    sealed = unseal_request_state(request_state_token)
                except InvalidRequestStateError as e:
                    return JSONResponse(
                        {
                            "jsonrpc": "2.0",
                            "id": req_id,
                            "error": {
                                "code": -32602,
                                "message": f"requestState invalido ou expirado: {e}",
                            },
                        }
                    )

                input_key = sealed.get("input_key", "__main__:escolha_de_sala")
                resp = {}
                if isinstance(input_responses, dict):
                    resp = input_responses.get(input_key) or next(iter(input_responses.values()), {})

                action = resp.get("action")
                if action in ("decline", "cancel"):
                    structured = {
                        "reserva": None,
                        "reservado": False,
                        "sala": None,
                        "inicio": None,
                        "fim": None,
                        "responsavel": None,
                        "politica": None,
                        "motivo": "recusado",
                    }
                    text_block = json.dumps(structured, indent=2)
                    return JSONResponse(
                        {
                            "jsonrpc": "2.0",
                            "id": req_id,
                            "result": {
                                "content": [{"type": "text", "text": text_block}],
                                "isError": False,
                                "resultType": "complete",
                                "structuredContent": structured,
                                "_meta": SERVER_INFO,
                            },
                        }
                    )

                if action == "accept":
                    chosen_sala = (resp.get("content") or {}).get("sala")
                    # Sealed arguments win over unverified arguments passed by client
                    sealed_args = sealed.get("arguments", {})
                    inicio_str = sealed_args.get("inicio")
                    fim_str = sealed_args.get("fim")
                    responsavel = sealed_args.get("responsavel", "Validador")

                    # Add reservation for the chosen alternative room
                    nova = room_manager.add_reserva(chosen_sala, inicio_str, fim_str, responsavel)
                    structured = {
                        "reserva": nova["id"],
                        "reservado": True,
                        "sala": chosen_sala,
                        "inicio": inicio_str,
                        "fim": fim_str,
                        "responsavel": responsavel,
                        "politica": room_manager.politica_versao,
                        "motivo": None,
                    }
                    text_block = json.dumps(structured, indent=2)
                    return JSONResponse(
                        {
                            "jsonrpc": "2.0",
                            "id": req_id,
                            "result": {
                                "content": [{"type": "text", "text": text_block}],
                                "isError": False,
                                "resultType": "complete",
                                "structuredContent": structured,
                                "_meta": SERVER_INFO,
                            },
                        }
                    )

                # Unknown action
                return JSONResponse(
                    {
                        "jsonrpc": "2.0",
                        "id": req_id,
                        "error": {
                            "code": -32602,
                            "message": f"Acao de elicitation desconhecida: {action}",
                        },
                    }
                )

            # Initial call case
            sala = args.get("sala", "")
            inicio_str = args.get("inicio", "")
            fim_str = args.get("fim", "")
            responsavel = args.get("responsavel", "Validador")

            try:
                inicio, fim = room_manager.validate_interval(sala, inicio_str, fim_str)
            except DomainError as e:
                return JSONResponse(
                    {
                        "jsonrpc": "2.0",
                        "id": req_id,
                        "result": {
                            "content": [{"type": "text", "text": e.message}],
                            "isError": True,
                            "resultType": "complete",
                            "_meta": SERVER_INFO,
                        },
                    }
                )

            conflitos = room_manager.find_conflicts(sala, inicio, fim)
            if not conflitos:
                # Room is free: create reservation immediately
                nova = room_manager.add_reserva(sala, inicio_str, fim_str, responsavel)
                structured = {
                    "reserva": nova["id"],
                    "reservado": True,
                    "sala": sala,
                    "inicio": inicio_str,
                    "fim": fim_str,
                    "responsavel": responsavel,
                    "politica": room_manager.politica_versao,
                    "motivo": None,
                }
                text_block = json.dumps(structured, indent=2)
                return JSONResponse(
                    {
                        "jsonrpc": "2.0",
                        "id": req_id,
                        "result": {
                            "content": [{"type": "text", "text": text_block}],
                            "isError": False,
                            "resultType": "complete",
                            "structuredContent": structured,
                            "_meta": SERVER_INFO,
                        },
                    }
                )
            else:
                # Room is occupied: MRTR cycle begins!
                # Check form elicitation capability
                form_cap = client_caps.get("elicitation", {}).get("form") if isinstance(client_caps, dict) else None
                if form_cap is None:
                    return JSONResponse(
                        {
                            "jsonrpc": "2.0",
                            "id": req_id,
                            "error": {
                                "code": -32021,
                                "message": "Client did not declare the form elicitation capability required by resolver '__main__:escolha_de_sala'",
                                "data": {
                                    "requiredCapabilities": {
                                        "elicitation": {
                                            "form": {},
                                        }
                                    }
                                },
                            },
                        },
                        status_code=400,
                    )

                alternativas = room_manager.find_alternatives(sala, inicio, fim)
                if not alternativas:
                    return JSONResponse(
                        {
                            "jsonrpc": "2.0",
                            "id": req_id,
                            "result": {
                                "content": [{"type": "text", "text": "Sem alternativas disponiveis no intervalo"}],
                                "isError": True,
                                "resultType": "complete",
                                "_meta": SERVER_INFO,
                            },
                        }
                    )

                input_key = "__main__:escolha_de_sala"
                sealed_data = {
                    "arguments": {
                        "sala": sala,
                        "inicio": inicio_str,
                        "fim": fim_str,
                        "responsavel": responsavel,
                    },
                    "input_key": input_key,
                    "alternatives": alternativas,
                }
                token = seal_request_state(sealed_data)

                requested_schema = {
                    "properties": {
                        "sala": {
                            "description": "Sala alternativa escolhida",
                            "enum": alternativas,
                            "title": "Sala",
                            "type": "string",
                        }
                    },
                    "required": ["sala"],
                    "type": "object",
                }

                return JSONResponse(
                    {
                        "jsonrpc": "2.0",
                        "id": req_id,
                        "result": {
                            "inputRequests": {
                                input_key: {
                                    "method": "elicitation/create",
                                    "params": {
                                        "message": "A sala pedida esta ocupada nesse intervalo. Escolha uma alternativa.",
                                        "mode": "form",
                                        "requestedSchema": requested_schema,
                                    },
                                }
                            },
                            "requestState": token,
                            "resultType": "input_required",
                            "_meta": SERVER_INFO,
                        },
                    }
                )

        # Tool unknown
        return JSONResponse(
            {
                "jsonrpc": "2.0",
                "id": req_id,
                "error": {
                    "code": -32602,
                    "message": f"Ferramenta desconhecida: {tool_name}",
                },
            }
        )

    # Unknown JSON-RPC method
    return JSONResponse(
        {
            "jsonrpc": "2.0",
            "id": req_id,
            "error": {
                "code": -32601,
                "message": f"Metodo nao suportado: {method}",
            },
        }
    )


routes = [
    Route("/mcp", handle_mcp, methods=["POST"]),
]

app = Starlette(routes=routes)
