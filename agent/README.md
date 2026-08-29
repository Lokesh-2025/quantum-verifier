# Quantum Verifier MCP Agent

A ReAct (Reason-Act) agent for the [quantum-verifier](../README.md) MCP server. Exposes the verifier's full tool set — circuit safety-gating, falsification, robustness selection, experiment templates, calibration intelligence, and job lifecycle — through a conversational interface backed by your choice of LLM.

## Features

- **Three-domain routing** — questions are automatically classified and dispatched to the right specialist subagent (Core, IBM, or IonQ)
- **Full ReAct loop** — each subagent reasons about which tool to call, calls it via the MCP server, observes the result, and repeats until it can give a final answer
- **Multi-provider LLM support** — Google Gemini, Ollama (local), OpenAI, Anthropic Claude, vLLM
- **HTTP dispatcher + REPL chat** — use the REST API from any client, or the included terminal chat
- **File injection** — use `@/path/to/file` in the chat to attach a circuit file or any other content inline
- **Job polling** — `/poll IBM|IonQ <job_id>` monitors a submitted job until it completes

---

## Prerequisites

- **Node.js** ≥ 18
- **Python** ≥ 3.10 with the quantum-verifier dependencies installed (`pip install -r ../requirements.txt`)
- One LLM provider (see [Choosing an LLM Provider](#choosing-an-llm-provider))
- API credentials: `IBM_QUANTUM_TOKEN` and/or `IONQ_API_KEY` (for the tools you intend to use)

---

## Choosing an LLM Provider

### Google Gemini

Cloud API, no local setup required.

```bash
# Free tier available at https://aistudio.google.com/app/apikey
GEMINI_API_KEY=your_key
GEMINI_MODEL=gemini-2.5-pro
LLM_PROVIDER=gemini
```

### Ollama (Recommended for Privacy)

Fully local — no API key, no data leaves your machine.

```bash
# Install: https://ollama.com
ollama pull llama3.1:8b
OLLAMA_MODEL=llama3.1:8b
LLM_PROVIDER=ollama
```

### OpenAI

```bash
OPENAI_API_KEY=your_key
OPENAI_MODEL=gpt-4o
LLM_PROVIDER=openai
```

### Anthropic Claude

```bash
ANTHROPIC_API_KEY=your_key
ANTHROPIC_MODEL=claude-3-5-sonnet-20241022
LLM_PROVIDER=anthropic
```

### vLLM

Self-hosted, OpenAI-compatible endpoint.

```bash
VLLM_BASE_URL=http://localhost:8000/v1
VLLM_MODEL=meta-llama/Llama-3.1-8B-Instruct
LLM_PROVIDER=vllm
```

---

## Installation

```bash
cd quantum-verifier/agent
npm install
cp .env.example .env
# Edit .env — fill in your LLM provider keys and quantum credentials
```

---

## Starting the MCP Server in SSE Mode

The quantum-verifier's `mcp_server.py` defaults to **stdio transport** (for Claude Desktop use). The agent connects via **SSE (Server-Sent Events)** instead. Start the server with the SSE flag:

```bash
# From the quantum-verifier root
source .venv/bin/activate
python mcp_server.py --transport sse --port 3031
```

The server will listen at `http://127.0.0.1:3031/sse`. This is what `QUANTUM_MCP_SERVER_URI` in `.env` should point to.

> **Port assignments** (no collisions with the sibling project):
>
> | Service | Default Port |
> | --------- | ------------- |
> | quantum-hardware-mcp server | 3020 |
> | quantum-hardware-mcp agent dispatcher | 3021 |
> | quantum-verifier MCP server (SSE) | 3031 |
> | quantum-verifier agent dispatcher | 3041 |

The quantum-verifier's stdio default is untouched — for Claude Desktop, continue using `mcp_server.py` with no flags.

---

## Running the Agent

**Step 1 — Start the quantum-verifier MCP server (SSE mode):**

```bash
# In quantum-verifier/
python mcp_server.py --transport sse --port 3031
```

**Step 2 — Start the dispatcher:**

```bash
# In quantum-verifier/agent/
npm start
# or: node agent-server.js
```

**Step 3 — Open the chat:**

```bash
node chat.js
```

-or-

```bash
npm run chat.js
```

Or call the REST API directly:

```bash
curl -X POST http://localhost:3041/chat \
  -H 'Content-Type: application/json' \
  -d '{"question":"Verify this 2-qubit circuit on IonQ forte-1: OPENQASM 2.0; qreg q[2]; ...","history":[]}'
```

---

## Using the REPL Chat Interface

```text
╔══════════════════════════════════════════════════════════╗
║       Quantum Verifier Agent Chat Interface              ║
╚══════════════════════════════════════════════════════════╝

Connected to: http://localhost:3041

⚛️  Verifier Query> _
```

### Commands

| Command | Description |
| --------- | ------------- |
| `<question>` | Ask anything about circuit verification, hardware, or jobs |
| `@/path/to/file` | Attach a file's contents inline (e.g. `@./my_circuit.qasm`) |
| `/poll IBM <job_id> [secs]` | Poll an IBM job until it completes (default: every 10s) |
| `/poll IonQ <job_id> [secs]` | Poll an IonQ job until it completes |
| `/nolocal` | Toggle bypassing the local Qiskit specialist model |
| `/save @/path/to/file` | Save the chat history as Markdown |
| `/clear` | Clear the chat history |
| `/help` | Show this message |
| `/exit` or `/quit` | End the session |

### Example Queries

```text
Verify this circuit on IonQ forte-1:
OPENQASM 2.0; include "qelib1.inc"; qreg q[2]; creg c[2]; h q[0]; cx q[0],q[1]; measure q -> c;

Falsify the claim that the above circuit achieves 0.75 amplification on "11"

Run a GHZ parity check on 6 qubits using the IonQ simulator

Find the most noise-robust of these two candidate circuits on IonQ forte-1: ...

Compare IBM ibm_fez and IonQ forte-1 for a 5-qubit search circuit

List available IBM quantum backends and their queue depth

Get calibration alerts for ibm_fez from the last 7 days

Check my IonQ account budget and device status before submitting

What is the recommendation for amplification_tolerance on IonQ forte-1?
```

---

## API Endpoint

### `POST /chat`

**Request:**

```json
{
  "question": "string (required)",
  "history": [
    { "role": "user", "content": "..." },
    { "role": "assistant", "content": "..." }
  ],
  "noLocal": false
}
```

**Response:**

```json
{
  "status": "complete",
  "answer": "string",
  "metadata": {
    "domain": "Core|IBM|IonQ",
    "toolsAvailable": ["verify_experiment", "falsify_claim", "..."]
  }
}
```

**Error response:**

```json
{ "status": "error", "answer": "string" }
```

### `GET /health`

```json
{ "status": "ok", "subagents": ["Core", "IBM", "IonQ"] }
```

---

## Architecture

```text
User terminal
     │ readline REPL
     ▼
chat.js ──── HTTP POST /chat ──── agent-server.js (dispatcher, port 3041)
                                        │
                                  LLM classifies question
                                  into Core / IBM / IonQ
                                        │
                    ┌───────────────────┼───────────────────┐
                    ▼                   ▼                   ▼
          core-subagent.js     ibm-subagent.js    ionq-subagent.js
          (stdio, spawned)      (stdio, spawned)   (stdio, spawned)
                    │                   │                   │
                    └───────────────────┴───────────────────┘
                                        │ SSE (MCP protocol)
                                        ▼
                            quantum-verifier/mcp_server.py
                              (SSE mode, port 3031)
                                        │
                              core/ and providers/
                              (zero MCP dependency)
```

### Subagent Domains

| Subagent | Tools | Notes |
| ---------- | ------- | ------- |
| **Core** | `verify_experiment`, `falsify_claim`, `find_robust_circuit`, `correct_for_multiple_comparisons`, `check_taxonomy`, `shadow_mode_disagreement_log`, `run_ghz_parity_check`, `run_graph_coloring_search`, `verify_stabilizer_circuit`, `verify_stabilizer_hardware_result`, `find_optimal_backend`, `diff_compilers`, `memory_summary`, `verdict_track_record`, `recommend_tolerance`, `ionq_sync_memory_for_job` | The verifier pipeline; routes questions spanning multiple domains |
| **IBM** | `list_devices`, `get_device_details`, `best_qubits`, `best_qubits_for_reproducibility`, `compare_devices`, `queue_status`, `device_history`, `device_profile`, `device_on_date`, `submit_job`, `job_status`, `job_results`, `cancel_job`, `list_jobs`, `estimate_runtime`, `route_job`, `get_alerts`, `start_repro_experiment`, `repro_score`, `job_analytics`, `ibm_account_check`, `check_chip_identity`, `audit_calibration_telemetry` | IBM device intelligence and job lifecycle |
| **IonQ** | `ionq_devices`, `ionq_compare_devices`, `ionq_account_check`, `ionq_preflight`, `ionq_submit_job`, `ionq_job_status`, `ionq_job_results`, `estimate_ionq_gates`, `estimate_ionq_cost`, `ionq_sync_memory_for_job` | IonQ trapped-ion hardware |

---

## Configuration

### Basic Configuration

Minimum `.env` to get started:

```env
LLM_PROVIDER=gemini
GEMINI_API_KEY=your_key
GEMINI_MODEL=gemini-2.5-pro

QUANTUM_MCP_SERVER_URI=http://127.0.0.1:3031/sse

IBM_QUANTUM_TOKEN=your_ibm_token
IBM_CHANNEL=ibm_quantum_platform
IONQ_API_KEY=your_ionq_key
```

### Provider-Specific Configuration

#### Gemini

```env
LLM_PROVIDER=gemini
GEMINI_API_KEY=your_key_here
GEMINI_MODEL=gemini-2.5-pro
```

#### Ollama (Local)

```bash
# Install and pull a model first
ollama serve
ollama pull llama3.1:8b
```

```env
LLM_PROVIDER=ollama
OLLAMA_MODEL=llama3.1:8b
OLLAMA_BASE_URL=http://localhost:11434
OLLAMA_TEMPERATURE=0.7
```

#### OpenAI configuration

```env
LLM_PROVIDER=openai
OPENAI_API_KEY=your_key_here
OPENAI_MODEL=gpt-4o
OPENAI_TEMPERATURE=0.7
```

#### Anthropic Claude configuration

```env
LLM_PROVIDER=anthropic
ANTHROPIC_API_KEY=your_key_here
ANTHROPIC_MODEL=claude-3-5-sonnet-20241022
ANTHROPIC_TEMPERATURE=0.7
ANTHROPIC_MAX_TOKENS=4096
```

#### vLLM configuration

```bash
# Start vLLM server first
python -m vllm.entrypoints.openai.api_server \
  --model meta-llama/Llama-3.1-8B-Instruct \
  --port 8000
```

```env
LLM_PROVIDER=vllm
VLLM_BASE_URL=http://localhost:8000/v1
VLLM_MODEL=meta-llama/Llama-3.1-8B-Instruct
VLLM_API_KEY=EMPTY
```

---

## Qiskit Specialist Code Model (Optional)

The IBM subagent can optionally route quantum circuit-writing questions to a local Ollama model tuned specifically for Qiskit. IBM publishes two purpose-built models:

```bash
# Full model (~24B parameters, best quality, requires ~16GB RAM or GPU)
ollama pull hf.co/Qiskit/mistral-small-3.2-24b-qiskit-GGUF:latest

# Smaller model (~8B parameters, good for CPU-only machines)
ollama pull hf.co/Qiskit/granite-3.3-8b-qiskit-GGUF:latest
```

```env
QISKIT_CODE_MODEL=hf.co/Qiskit/mistral-small-3.2-24b-qiskit-GGUF:latest
QISKIT_CODE_MODEL_URL=http://localhost:11434
# Default timeout is 10 minutes — appropriate for CPU-only inference
QISKIT_MODEL_TIMEOUT_MS=600000
```

Leave `QISKIT_CODE_MODEL` unset to disable this feature. Use `/nolocal` in the chat to bypass it for a session.

---

## Environment Variables Reference

### LLM Provider

| Variable        | Required | Default   | Description                                          |
|-----------------|----------|-----------|------------------------------------------------------|
| `LLM_PROVIDER`  | Yes      | `gemini`  | `gemini`, `ollama`, `openai`, `anthropic`, or `vllm` |

### Gemini Variables

| Variable | Required | Description |
| -------- | -------- | ----------- |
| `GEMINI_API_KEY` | Yes | Google AI Studio API key |
| `GEMINI_MODEL` | Yes | Model name (e.g. `gemini-2.5-pro`) |

### Ollama Variables

| Variable | Required | Default | Description |
| ---------- | ---------- | --------- | ------------- |
| `OLLAMA_MODEL` | Yes | — | Model name (e.g. `llama3.1:8b`) |
| `OLLAMA_BASE_URL` | No | `http://localhost:11434` | Ollama server URL |
| `OLLAMA_TEMPERATURE` | No | `0.7` | Generation temperature |
| `OLLAMA_KEEP_ALIVE` | No | `5m` | How long to keep model loaded |

### OpenAI Variables

| Variable | Required | Default | Description |
| ---------- | ---------- | --------- | ------------- |
| `OPENAI_API_KEY` | Yes | — | OpenAI API key |
| `OPENAI_MODEL` | Yes | — | Model name (e.g. `gpt-4o`) |
| `OPENAI_BASE_URL` | No | OpenAI default | For compatible APIs |
| `OPENAI_TEMPERATURE` | No | `0.7` | Generation temperature |

### Anthropic Variables

| Variable | Required | Default | Description |
| ---------- | ---------- | --------- | ------------- |
| `ANTHROPIC_API_KEY` | Yes | — | Anthropic API key |
| `ANTHROPIC_MODEL` | Yes | — | Model name (e.g. `claude-3-5-sonnet-20241022`) |
| `ANTHROPIC_TEMPERATURE` | No | `0.7` | Generation temperature |
| `ANTHROPIC_MAX_TOKENS` | No | `4096` | Max tokens in response |

### vLLM Variables

| Variable | Required | Default | Description |
| ---------- | ---------- | --------- | ------------- |
| `VLLM_BASE_URL` | Yes | — | vLLM server URL (e.g. `http://localhost:8000/v1`) |
| `VLLM_MODEL` | Yes | — | Model name as loaded in vLLM |
| `VLLM_API_KEY` | No | `EMPTY` | API key (usually not needed locally) |
| `VLLM_TEMPERATURE` | No | `0.7` | Generation temperature |
| `VLLM_MAX_TOKENS` | No | `4096` | Max tokens in response |

### Verifier MCP Server

| Variable                 | Required | Default                      | Description                       |
|--------------------------|----------|------------------------------|-----------------------------------|
| `QUANTUM_MCP_SERVER_URI` | Yes      | `http://127.0.0.1:3031/sse`  | Verifier MCP server SSE endpoint  |
| `MCP_API_KEY`            | No       | —                            | MCP server API key (if enabled)   |

### Quantum Credentials

| Variable | Required | Description |
| ---------- | ---------- | ------------- |
| `IBM_QUANTUM_TOKEN` | For IBM tools | IBM Quantum account token |
| `IBM_CHANNEL` | For IBM tools | e.g. `ibm_quantum_platform` |
| `IONQ_API_KEY` | For IonQ tools | IonQ Cloud API key |

### Agent Server

| Variable | Default | Description |
| ---------- | --------- | ------------- |
| `PORT` | `3041` | Dispatcher HTTP server port |
| `QUANTUM_AGENT_URL` | `http://localhost:3041` | Used by `chat.js` to find the dispatcher |
| `MAX_ITERATIONS` | `10` | Maximum ReAct loop steps per request |
| `SUBAGENT_TIMEOUT_MS` | `120000` | Subagent process timeout (ms) |
| `LLM_TIMEOUT_MS` | `60000` | LLM API call timeout (ms) |
| `MCP_TOOL_TIMEOUT_MS` | `60000` | MCP tool call timeout (ms) |
| `LLM_CONCURRENCY` | `4` | Max concurrent LLM calls |
| `MCP_CONCURRENCY` | `8` | Max concurrent MCP tool calls |

---

## Troubleshooting

### MCP Server Connection Refused

```text
MCP connection failed: connect ECONNREFUSED 127.0.0.1:3031
```

The quantum-verifier server is not running in SSE mode or is on a different port.

```bash
# Start it explicitly
python mcp_server.py --transport sse --port 3031
# Then confirm QUANTUM_MCP_SERVER_URI=http://127.0.0.1:3031/sse in your .env
```

### Provider Package Not Found

```text
Provider "gemini" is not available. Please install the required dependency:
npm install @google/generative-ai
```

Install the optional dependency for your chosen provider:

```bash
npm install @google/generative-ai   # Gemini
npm install ollama                  # Ollama
npm install openai                  # OpenAI or vLLM
npm install @anthropic-ai/sdk       # Anthropic
```

### No Tools Available

```text
No tools available for this provider.
```

The MCP server started successfully but returned no tools — usually because the quantum API credentials (`IBM_QUANTUM_TOKEN`, `IONQ_API_KEY`) are missing from the environment the MCP server was started in. These must be set in the MCP server's process environment, not just in the agent's `.env`.

### Ollama Connection Refused

```text
Cannot connect to Ollama at http://localhost:11434.
```

Start Ollama first:

```bash
ollama serve
# Then in another terminal:
ollama pull llama3.1:8b
```

### API Key Issues

Verify credentials are set and active:

```bash
# IBM
python -c "from qiskit_ibm_runtime import QiskitRuntimeService; QiskitRuntimeService(channel='ibm_quantum_platform', token='YOUR_TOKEN')"

# IonQ — check from within the agent
# Ask: "Check my IonQ account status"
```

---

## Concurrency and Timeouts

The dispatcher and subagents are designed for sequential interactive use. Each `/chat` request spawns one subagent process that lives for the duration of that request. Parallel requests are supported up to the limits of your LLM provider's rate limits.

Key timeouts:

- `SUBAGENT_TIMEOUT_MS` (default 120s) — hard kill if a subagent doesn't return an answer
- `MCP_TOOL_TIMEOUT_MS` (default 60s) — individual tool call timeout inside the ReAct loop
- `LLM_TIMEOUT_MS` (default 60s) — each LLM API call timeout

For tools that call real quantum hardware (`submit_job`, `ionq_submit_job`) or run full noisy simulations (`verify_experiment`, `falsify_claim`), increase `SUBAGENT_TIMEOUT_MS` and `MCP_TOOL_TIMEOUT_MS` if you hit timeouts.

---

## Docker

```bash
# Build
docker build -t quantum-verifier-agent .

# Run (pass credentials at runtime, never bake them into the image)
docker run -p 3041:3041 \
  --env-file .env \
  -e QUANTUM_MCP_SERVER_URI=http://host.docker.internal:3031/sse \
  quantum-verifier-agent
```

---

## License

MIT — see [../LICENSE](../LICENSE).
