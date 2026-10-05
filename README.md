# A Ponte: Agente A2A com MCP por dentro

Implementação da ponte entre o protocolo **Model Context Protocol (MCP)** e o protocolo **Agent-to-Agent (A2A)**, conectando a capacidade interna de gestão e reserva de salas de reunião via MCP com uma interface exposta para outros agentes via A2A v1.0.

---

## Como rodar

A solução foi desenvolvida em Python (3.10+) utilizando Starlette e Uvicorn, sem bibliotecas de LLM e com total conformidade com a especificação do desafio.

### 1. Pré-requisitos e Ambiente

Clone o repositório e crie o ambiente virtual com as dependências instaladas.

Com `uv` (recomendado):
```bash
uv sync
source .venv/bin/activate
```

Ou com `python3 -m venv` / `pip`:
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
```

### 2. Configurar a Chave de Integridade

Gere e exporte uma chave de integridade aleatória de no mínimo 32 bytes para a assinatura do `requestState`:

```bash
export REQUEST_STATE_SECRET=$(python3 -c "import secrets; print(secrets.token_hex(32))")
```

### 3. Subir os Serviços

Em dois terminais separados (ou usando os scripts auxiliares):

**Terminal 1 — Servidor MCP (porta 7301):**
```bash
./run_mcp.sh
# Ou manualmente:
# export REQUEST_STATE_SECRET="<sua_chave_gerada>"
# .venv/bin/python3 servidor-mcp/main.py
```
> O servidor MCP iniciará na porta `7301`, atendendo em `/mcp` e exibindo os registros de requisições (`method`, `id`, `traceparent`) diretamente no `stderr`.

**Terminal 2 — Agente A2A (porta 7300):**
```bash
./run_agente.sh
# Ou manualmente:
# .venv/bin/python3 agente/main.py
```
> O agente iniciará na porta `7300`, publicando o Agent Card em `/.well-known/agent-card.json` e o endpoint JSON-RPC em `/a2a`.

### 4. Executar o Validador

Em um terceiro terminal:
```bash
python3 validador/validar.py --agente http://localhost:7300 --mcp http://localhost:7301
```

---

## Onde a ponte acontece

A transição entre os dois protocolos ocorre centralmente no agente em [`agente/server.py`](file:///agente/server.py) e no servidor MCP em [`servidor-mcp/server.py`](file:///servidor-mcp/server.py):

1. **Interrupção da Task (`input_required` -> `TASK_STATE_INPUT_REQUIRED`):**
   No agente ([`agente/server.py`](file:///agente/server.py)), quando o cliente A2A envia um `SendMessage` pedindo uma sala em horário com sobreposição, o agente despacha a chamada `tools/call` (`reservar_sala`) ao servidor MCP. Ao identificar o conflito, o servidor MCP retorna `resultType: "input_required"`, contendo o `requestState` selado e a elicitation em form mode com o enum de salas alternativas. No agente, esse retorno é interceptado: o agente associa o `requestState` opaco e a chave da solicitação à `Task` em memória, coloca o estado da tarefa em `TASK_STATE_INPUT_REQUIRED` e devolve ao cliente A2A a mensagem com o formato estrito `alternativas: <ids separados por virgula e espaco>`, mantendo o `requestState` estritamente confidencial.

2. **Retomada e Conclusão (`requestState` -> Servidor MCP):**
   Quando o cliente A2A envia a resposta de continuação (`escolha=<id>` ou `escolha=recusar`) referenciando a `taskId`, o agente recupera o contexto da tarefa e seu `requestState` previamente guardado. Em seguida, gera um **novo id de requisição JSON-RPC** e emite um novo `tools/call` (`reservar_sala`) ao servidor MCP, ecoando o `requestState` e fornecendo `inputResponses` (`action: "accept"` com a sala escolhida ou `action: "decline"`). No servidor MCP ([`servidor-mcp/server.py`](file:///servidor-mcp/server.py)), a presença de `requestState` aciona a validação criptográfica; os parâmetros originais selados são recuperados (imunes a manipulação externa de argumentos no retry) e a reserva é concluída com `resultType: "complete"`. O agente então encerra a tarefa em `TASK_STATE_COMPLETED` (com o artifact de reserva contendo a versão da política) ou `TASK_STATE_CANCELED` (em caso de recusa).

---

## Decisões técnicas

* **Proteção e Integridade do `requestState`:**
  Implementada em [`servidor-mcp/crypto_state.py`](file:///servidor-mcp/crypto_state.py) utilizando HMAC-SHA256 com codificação Base64 URL-safe no formato estruturado `v1.<payload_b64>.<assinatura_b64>`. O payload selado contém os argumentos originais da reserva (`sala`, `inicio`, `fim`, `responsavel`), a chave interna do resolver de elicitation e a data de expiração (`exp`). A verificação utiliza comparação em tempo constante (`hmac.compare_digest`). Qualquer adulteração no token ou expiração resulta imediatamente na rejeição com o código de erro JSON-RPC `-32602`. Além disso, os argumentos selados no token prevalecem sobre quaisquer argumentos enviados no retry, garantindo a integridade da reserva.
* **Validade do `requestState`:**
  O token é emitido com TTL de **15 minutos** (900 segundos), cumprindo o requisito de expiração entre 5 e 30 minutos. Por ser autocontido e assinado com segredo externo (`REQUEST_STATE_SECRET`), o token permanece válido mesmo se o processo do servidor MCP for reiniciado entre o `input_required` e o retry.
* **Gerenciamento e Isolamento do Estado das Tasks:**
  O estado das tarefas do agente é gerenciado em memória pela classe `TaskManager` ([`agente/task_manager.py`](file:///agente/task_manager.py)), indexado por `taskId`. O `requestState` é armazenado exclusivamente como campo privado da tarefa em memória e nunca é propagado para o histórico, mensagens de status ou artifacts da resposta A2A. Tarefas concorrentes mantêm seus estados isolados sem interferência mútua.

---

## Saída do validador

Abaixo está a saída integral da execução de [`validador/validar.py`](file:///validador/validar.py) contra os dois serviços iniciados do zero:

```
trace-id desta execucao: e037b2763eae7a4bd74b754440c89353
procure esse valor no stderr do servidor MCP para conferir a propagacao do traceparent.

PASS 01 tools/list traz as tres tools
PASS 02 toda tool tem inputSchema de objeto
PASS 03 listar_salas devolve structuredContent e o mesmo JSON em texto
PASS 04 _meta sem protocolVersion devolve -32602 e HTTP 400
PASS 05 _meta sem clientCapabilities devolve -32602 e HTTP 400
PASS 06 tool inexistente e recusada, por -32602 ou por isError
PASS 07 resources/read de politica://uso devolve a politica
PASS 08 resources/read de URI inexistente devolve -32602
PASS 09 sala inexistente devolve isError com a mensagem exata
PASS 10 fora da janela devolve isError com a mensagem exata
PASS 11 duracao acima de 2h devolve isError com a mensagem exata
PASS 12 intervalo invertido devolve isError com a mensagem exata
PASS 13 conflito devolve input_required com inputRequests e requestState
PASS 14 a elicitation e form mode e oferece as alternativas na ordem certa
PASS 15 conflito sem a capability elicitation devolve -32021 e HTTP 400
PASS 16 retry com inputResponses e requestState conclui a reserva
PASS 17 requestState adulterado e rejeitado com -32602
PASS 18 argumentos adulterados no retry nao tomam efeito
PASS 19 recusa conclui sem reservar e sem isError
PASS 20 conflito sem alternativa possivel devolve isError com a mensagem exata

PASS 21 agent card responde 200 no well-known com JSON
PASS 22 o card declara a interface JSON-RPC com url e versao 1.0
PASS 23 o card declara a skill reservar-sala
PASS 24 SendMessage com sala livre conclui a Task
PASS 25 o artifact chama reserva e traz a versao da politica
PASS 26 GetTask devolve id, contextId e estado corrente
PASS 27 SendMessage com sala ocupada pausa a Task
PASS 28 a Task pausada lista as alternativas na ordem certa
PASS 29 escolha fora do enum mantem a Task pausada
PASS 30 a continuacao conclui a Task na sala escolhida
PASS 31 SendMessage em Task terminal e recusado
PASS 32 a recusa termina a Task em CANCELED
PASS 33 duas Tasks pausadas ao mesmo tempo concluem cada uma com a sua reserva
PASS 34 nenhuma resposta A2A carrega o requestState
PASS 35 sala inexistente termina a Task em FAILED com a mensagem da tool
PASS 36 o agente e deterministico: o mesmo pedido produz a mesma pausa

resumo: 36 passaram, 0 falharam, de 36 verificacoes
```
