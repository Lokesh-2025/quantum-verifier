# Quantum Verifier Agent — Implementation Plan

## Top-Level Overview

Create a ReAct (Reason-Act) agent for the `quantum-verifier` MCP server, mirroring the architecture
of the existing `agent/` project in `quantum-hardware-mcp`. The new agent lives at
`quantum-verifier/agent/` inside the verifier repo and provides:

- An **HTTP dispatcher** (`agent-server.js`) that classifies incoming questions and routes them to
  one of three subagents.
- Three **stdio subagents** — Core, IBM, and IonQ — each operating a ReAct loop over a filtered
  slice of the verifier's tools.
- A **REPL chat client** (`chat.js`) identical in UX to the existing one, re-titled for the verifier.
- All **shared LLM provider infrastructure** (Gemini, Ollama, OpenAI, Anthropic, vLLM) copied
  verbatim from `agent/shared/` — no functional changes, no new dependencies invented.
- A `.env.example` covering LLM providers, MCP server URI, quantum API keys, and timeouts.
- A `README.md` explaining setup, transport modes (stdio vs SSE), and all three subagents.

**Transport note:** `mcp_server.py` defaults to stdio (for Claude Desktop). For the agent to
connect, the verifier must be started with SSE transport:
```
python mcp_server.py --transport sse --port 3031
```
The README documents both modes; the `.env.example` points to the SSE URI.

**Dispatcher routing:** Classifies questions into `Core`, `IBM`, or `IonQ`. Core handles everything
not clearly IBM- or IonQ-specific (verifier pipeline, falsify, robustness, templates, memory,
stabilizer, multi-compiler, optimal backend).

---

## Sub-Tasks

---

### Sub-Task 1 — Scaffold the project skeleton

**Intent:** Create the directory layout and the Node.js project config so all later sub-tasks have
a place to write into.

**Expected Outcomes:**
- `/home/jwoehr/work/AI/MCP/quantum-verifier/agent/` directory exists with the correct structure.
- `package.json` is present and correct (same deps as `quantum-hardware-mcp/agent/package.json`,
  name changed to `quantum-verifier-agent`).
- `.gitignore` and `.dockerignore` are present.

**Todo List:**
1. Create `quantum-verifier/agent/package.json` — name `quantum-verifier-agent`, same deps as
   `agent/package.json` in quantum-hardware-mcp.
2. Create `quantum-verifier/agent/.gitignore` — same content as `agent/.gitignore`.
3. Create `quantum-verifier/agent/.dockerignore` — same content as `agent/.dockerignore`.

**Relevant Context:**
- [`agent/package.json`](agent/package.json) — source to mirror.
- [`agent/.gitignore`](agent/.gitignore), [`agent/.dockerignore`](agent/.dockerignore).

**Status:** [ ] pending

---

### Sub-Task 2 — Copy shared LLM provider infrastructure

**Intent:** The `shared/` tree (LLM providers, config, concurrency) is not quantum-verifier-specific
and should be copied verbatim so the new agent gets multi-provider LLM support for free.

**Expected Outcomes:**
- `quantum-verifier/agent/shared/` mirrors `agent/shared/` exactly.
- `quantum-verifier/agent/lib/` contains `request-logger.js`.

**Todo List:**
1. Copy all files under `agent/shared/` into `quantum-verifier/agent/shared/` preserving
   subdirectory structure (`providers/`, `config/`, `concurrency/`).
2. Copy `agent/lib/request-logger.js` → `quantum-verifier/agent/lib/request-logger.js`.

**Relevant Context:**
- [`agent/shared/`](agent/shared/) — source tree.
- [`agent/lib/request-logger.js`](agent/lib/request-logger.js).
- No content changes; all require paths inside these files use relative paths that still resolve
  correctly after the copy.

**Status:** [ ] pending

---

### Sub-Task 3 — Create the base subagent

**Intent:** The ReAct loop, MCP connection, and stdio protocol live in `base-subagent.js`. This is
largely the same as the existing base, with one change: the Qiskit specialist model is not relevant
to verifier-specific work (no provider-specific code model exists for quantum verification), so the
`callQiskitModel` function and `QISKIT_CODE_MODEL` references are kept for compatibility with the
shared `runSubagent` interface but the Core and IonQ subagents will set `qiskitEnabled = false`.
The IBM subagent may optionally enable it for circuit-writing questions.

**Expected Outcomes:**
- `quantum-verifier/agent/subagents/base-subagent.js` is present and runs the ReAct loop.
- Path to load `.env` is `../agent/.env` (one level up from `subagents/`, inside `agent/`).

**Todo List:**
1. Copy `agent/subagents/base-subagent.js` → `quantum-verifier/agent/subagents/base-subagent.js`.
2. Adjust the `dotenv.config` path comment if needed (path is already relative, no code change
   needed — `path.join(__dirname, '../.env')` resolves correctly from
   `quantum-verifier/agent/subagents/`).
3. Update the `require` paths for `provider-factory` and `provider-config` — both already use
   `../shared/...` which resolves correctly.

**Relevant Context:**
- [`agent/subagents/base-subagent.js`](agent/subagents/base-subagent.js).
- MCP server env var is `QUANTUM_MCP_SERVER_URI` — same variable name reused; the `.env` will
  point it at the verifier's SSE endpoint (default `http://127.0.0.1:3031/mcp`).

**Status:** [ ] pending

---

### Sub-Task 4 — Create the three subagents

**Intent:** Define the three specialist subagents (Core, IBM, IonQ) with their tool filters and
system prompts tailored to the quantum-verifier's domain.

**Expected Outcomes:**
- `quantum-verifier/agent/subagents/core-subagent.js` — handles the verifier pipeline, falsify,
  robustness, templates, memory/intelligence, stabilizer, multi-compiler, optimal backend.
- `quantum-verifier/agent/subagents/ibm-subagent.js` — handles IBM device/job tools; Qiskit model
  optionally enabled.
- `quantum-verifier/agent/subagents/ionq-subagent.js` — handles IonQ tools; no Qiskit model.

**Todo List:**
1. Create `core-subagent.js` with a tool filter that **excludes** tools starting with `ibm_`
   equivalent names and `ionq_` names. Concretely: include tools that do NOT start with `ionq_`
   AND whose names are NOT in the IBM device/job set. The safest filter: include all tools EXCEPT
   those starting with `ionq_` AND EXCEPT the IBM-specific tools (`list_devices`, `get_device_details`,
   `best_qubits`, `best_qubits_for_reproducibility`, `compare_devices`, `queue_status`,
   `device_history`, `device_profile`, `device_on_date`, `submit_job`, `job_status`, `job_results`,
   `cancel_job`, `list_jobs`, `estimate_runtime`, `route_job`, `get_alerts`, `start_repro_experiment`,
   `repro_score`, `job_analytics`, `ibm_account_check`, `check_chip_identity`, `audit_calibration_telemetry`).
2. Create `ibm-subagent.js` with filter `tool => !tool.name.startsWith('ionq_')` (same as existing
   agent — all non-ionq tools, which includes the IBM set AND core tools, matching the dispatcher's
   intent that IBM questions may need core tools too for context).
   Actually: restrict to IBM-specific tools only — filter to tools that are in the IBM set defined
   above plus `ibm_account_check`, `check_chip_identity`, `audit_calibration_telemetry`. This keeps
   each subagent focused.
3. Create `ionq-subagent.js` with filter `tool => tool.name.startsWith('ionq_')`.
4. Write context-aware system prompts for each subagent that reference quantum verification
   concepts (not generic hardware descriptions).

**Relevant Context:**
- [`/home/jwoehr/work/AI/MCP/quantum-verifier/mcp_server.py`](../quantum-verifier/mcp_server.py) —
  full tool list is the ground truth for filter design.
- Tool name groupings from the verifier README:
  - Core: `verify_experiment`, `correct_for_multiple_comparisons`, `check_taxonomy`,
    `shadow_mode_disagreement_log`, `falsify_claim`, `run_ghz_parity_check`,
    `run_graph_coloring_search`, `find_optimal_backend`, `diff_compilers`,
    `verify_stabilizer_circuit`, `verify_stabilizer_hardware_result`, `find_robust_circuit`,
    `ionq_sync_memory_for_job`, `memory_summary`, `verdict_track_record`, `recommend_tolerance`.
  - IBM: `list_devices`, `get_device_details`, `best_qubits`, `best_qubits_for_reproducibility`,
    `compare_devices`, `queue_status`, `device_history`, `device_profile`, `device_on_date`,
    `submit_job`, `job_status`, `job_results`, `cancel_job`, `list_jobs`, `estimate_runtime`,
    `route_job`, `get_alerts`, `start_repro_experiment`, `repro_score`, `job_analytics`,
    `ibm_account_check`, `check_chip_identity`, `audit_calibration_telemetry`.
  - IonQ: `ionq_devices`, `ionq_submit_job`, `ionq_job_status`, `ionq_job_results`,
    `estimate_ionq_gates`, `estimate_ionq_cost`, `ionq_preflight`, `ionq_account_check`,
    `ionq_compare_devices`.

**Status:** [ ] pending

---

### Sub-Task 5 — Create the dispatcher agent server

**Intent:** Build `agent-server.js` that classifies incoming questions into `Core`, `IBM`, or
`IonQ` and routes to the matching subagent.

**Expected Outcomes:**
- `quantum-verifier/agent/agent-server.js` starts on port 3041 (default, configurable via `PORT`).
- `/chat` endpoint accepts `{ question, history, noLocal }` and returns `{ status, answer, metadata }`.
- Provider classification prompt correctly identifies the three domains.
- Server banner names "Quantum Verifier MCP Agent".

**Todo List:**
1. Copy `agent/agent-server.js` as the basis.
2. Change the three-way subagent map: `SUBAGENTS = { Core, IBM, IonQ }` pointing to the new
   subagent scripts.
3. Update `classifyProvider` to classify into `Core`, `IBM`, or `IonQ` with default `Core`.
4. Change `port` default to `3041`.
5. Update banner text to "Quantum Verifier MCP Dispatcher Agent".
6. Update `require` paths to use the new `shared/` and `lib/` locations.

**Relevant Context:**
- [`agent/agent-server.js`](agent/agent-server.js) — source to adapt.
- Default port `3041` avoids collision with `quantum-hardware-mcp` agent on `3021`.

**Status:** [ ] pending

---

### Sub-Task 6 — Create the REPL chat client

**Intent:** Build `chat.js` — the interactive terminal interface that connects to the dispatcher.
The logic is identical to the existing chat; only branding, default URL, and welcome text differ.

**Expected Outcomes:**
- `quantum-verifier/agent/chat.js` starts a REPL, sends questions to the dispatcher, displays
  answers, supports `/poll`, `/save`, `/clear`, `/nolocal`, `/help`.
- Default `QUANTUM_AGENT_URL` points to `http://localhost:3041`.
- Welcome banner says "Quantum Verifier Agent Chat Interface".
- Poll command mentions Core/IBM/IonQ providers.

**Todo List:**
1. Copy `agent/chat.js` as the basis.
2. Change `AGENT_URL` default to `http://localhost:3041`.
3. Update the welcome banner and example queries to reflect verifier use cases
   (e.g., "Verify this circuit", "Falsify a claim", "Check IBM calibration").
4. Update `/poll` provider hints in help text to say `Core`, `IBM`, or `IonQ`.
5. Update prompt string to `⚛️  Verifier Query> `.

**Relevant Context:**
- [`agent/chat.js`](agent/chat.js) — source to adapt.

**Status:** [ ] pending

---

### Sub-Task 7 — Create .env.example

**Intent:** Provide a complete environment template covering both the LLM providers (from the
existing agent) and the quantum-verifier's own credentials.

**Expected Outcomes:**
- `quantum-verifier/agent/.env.example` is present and documents all required/optional vars.
- Quantum credentials section covers `IBM_QUANTUM_TOKEN`, `IBM_CHANNEL`, and `IONQ_API_KEY`
  (carried over from quantum-verifier's own `.env.example`).
- MCP server URI points to `http://127.0.0.1:3031/mcp` (verifier's SSE port).
- Agent server port default is `3041`.

**Todo List:**
1. Create the file, combining the LLM sections from `agent/.env.example` with a new
   "Quantum Verifier MCP Server Configuration" section.
2. Add an `IBM_QUANTUM_TOKEN`, `IBM_CHANNEL`, and `IONQ_API_KEY` section (per
   `quantum-verifier/.env.example`).
3. Change `QUANTUM_MCP_SERVER_URI` default to `http://127.0.0.1:3031/mcp`.
4. Change `PORT` default comment to `3041`.
5. Remove the `QUANTUM_AGENT_URL` default or update it to `http://localhost:3041`.
6. Remove Qiskit specialist model section (not applicable to the verifier agent's core use case;
   optionally keep as commented-out for the IBM subagent).

**Relevant Context:**
- [`agent/.env.example`](agent/.env.example) — LLM sections source.
- [`/home/jwoehr/work/AI/MCP/quantum-verifier/.env.example`](../quantum-verifier/.env.example) —
  quantum credential vars.

**Status:** [ ] pending

---

### Sub-Task 8 — Create the Dockerfile

**Intent:** Allow the agent to be containerized consistently with the existing agent's Docker setup.

**Expected Outcomes:**
- `quantum-verifier/agent/Dockerfile` builds a working image that starts the dispatcher.
- Exposes port `3041`.

**Todo List:**
1. Copy `agent/Dockerfile`.
2. Change `EXPOSE 3021` → `EXPOSE 3041`.
3. No other changes needed (same Node base, same entry point pattern).

**Relevant Context:**
- [`agent/Dockerfile`](agent/Dockerfile).

**Status:** [ ] pending

---

### Sub-Task 9 — Create the README

**Intent:** Document the agent project so a new user can install, configure, and run it in under
10 minutes. Reference the verifier's own README for context.

**Expected Outcomes:**
- `quantum-verifier/agent/README.md` covers: purpose, prerequisites, installation, configuration
  (all LLM providers), how to start the verifier in SSE mode, running the dispatcher and chat,
  the three subagent domains, environment variables reference, and troubleshooting.

**Todo List:**
1. Write `README.md` modeled on `agent/README.md` but scoped to the verifier:
   - Title: "Quantum Verifier MCP Agent"
   - Describe the three subagents (Core/IBM/IonQ) and their tool domains.
   - Add a "Starting the MCP Server in SSE mode" section showing:
     `python mcp_server.py --transport sse --port 3031` (FastMCP SSE flag).
   - Cover all LLM providers with the same detail as the existing agent README.
   - Environment variables table distinguishing verifier-specific vars from LLM vars.
   - Architecture section with a text diagram of:
     `chat.js → HTTP:3041 → dispatcher → Core/IBM/IonQ subagent → SSE → quantum-verifier MCP`.
   - Example queries relevant to circuit verification, falsification, calibration checks.

**Relevant Context:**
- [`agent/README.md`](agent/README.md) — structural template.
- [`/home/jwoehr/work/AI/MCP/quantum-verifier/README.md`](../quantum-verifier/README.md) —
  verifier domain context.

**Status:** [ ] pending

---

## File Layout After Completion

```
quantum-verifier/
└── agent/
    ├── .dockerignore
    ├── .env.example
    ├── .gitignore
    ├── Dockerfile
    ├── README.md
    ├── agent-server.js
    ├── chat.js
    ├── lib/
    │   └── request-logger.js
    ├── package.json
    ├── shared/
    │   ├── README.md
    │   ├── concurrency/
    │   │   └── limiters.js
    │   ├── config/
    │   │   └── provider-config.js
    │   └── providers/
    │       ├── anthropic-provider.js
    │       ├── base-provider.js
    │       ├── gemini-provider.js
    │       ├── ollama-provider.js
    │       ├── openai-provider.js
    │       ├── provider-factory.js
    │       └── vllm-provider.js
    └── subagents/
        ├── base-subagent.js
        ├── core-subagent.js
        ├── ibm-subagent.js
        └── ionq-subagent.js
```

## Port Assignments

| Service | Port |
|---------|------|
| quantum-hardware-mcp server | 3020 |
| quantum-hardware-mcp agent dispatcher | 3021 |
| quantum-verifier MCP server (SSE) | 3031 |
| quantum-verifier agent dispatcher | 3041 |
