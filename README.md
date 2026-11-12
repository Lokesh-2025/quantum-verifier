# quantum-verifier

[![Qiskit Ecosystem](https://qisk.it/e-96800568)](https://qisk.it/e)

A safety gate between an AI-generated Qiskit circuit and real quantum hardware. Before you spend QPU time or money, it checks that the circuit is well-formed, that it will survive real routing and native-gate constraints, and whether a claimed result is actually distinguishable from what hardware noise alone would produce, even when you don't know the right answer in advance.

It runs as an [MCP](https://modelcontextprotocol.io) server, so an AI assistant (Claude, etc.) can call it directly.

**Built on Qiskit.** Circuits are Qiskit `QuantumCircuit` objects (or OpenQASM), checked and compiled with the Qiskit SDK (Qiskit 2.0+). Supported targets are IBM Quantum and IonQ hardware, plus free local simulation with PennyLane and NVIDIA CUDA-Q.

| | |
|---|---|
| **Tools** | 53 |
| **Providers** | IBM, IonQ (real hardware) · PennyLane, CUDA-Q (local simulation, no API key) |
| **Status** | Experimental. Interfaces may change. |
| **License** | MIT |

## What it does

**`verify_experiment`** runs a circuit through a pipeline and returns GO or BLOCK with a human-readable reason:

1. **Semantic check:** is it a well-formed circuit?
2. **Topology check:** IBM: will heavy-hex routing blow up the gate count? IonQ: all-to-all, no routing risk.
3. **Ideal simulation:** what should happen with zero noise?
4. **Hardware-aware simulation:** what the real device's noise predicts (IonQ's named noise models, or a local Aer noise model built from IBM's live calibration data).
5. **Gate synthesis check:** did the circuit map cleanly onto the target's native gates, or is the transpiler silently inflating it?
6. **Ground-truth check:** if you supply an expected answer, does it survive predicted hardware noise?

**`falsify_claim`** builds a control circuit (same circuit, entangling gates removed) and reports the real effect size with readout bias cancelled out. It works with no known expected answer, so it suits discovery-mode research.

**`find_robust_circuit`** picks the candidate that holds up under real noise instead of the one that scores best in a noiseless simulation.

**Also included:** cross-provider cost and quality comparison, Qiskit-vs-TKET compiler diffing, exact verification of Clifford circuits at any qubit count, PennyLane gradient / barren-plateau checks, IonQ cost estimation and budget checks, a prediction-vs-reality memory that tunes tolerances from real results, and IBM and IonQ device and job management.

## Bugs it caught in this project's own code

These were real bugs in this project's own transpile, estimate and alert code. The Verifier's checks found them before they cost money or produced a wrong result.

- **Silent `rzz` fallback.** The tool's transpile setup had no direct translation from `rzz` to IonQ's native `zz`, so Qiskit fell back to general synthesis, turning 1 two-qubit gate into 2 native gates plus ~39 single-qubit gates. A reported entangling effect moved from 0.0352 to 0.0735 once fixed. This is now a permanent check (`gate_synthesis_check`).
- **Wrong default gate set.** The bare `"simulator"` target defaulted to an outdated gate family, so predictions did not match modern hardware.
- **Cost estimates about 2x too high** (`estimate_ionq_cost`, `estimate_ionq_gates`), from the same missing decomposition.
- **Drift alerts that never fired.** The calibration-history writer saved only 2 of ~9 columns, so T1/T2 drift detection ran on empty data. Fixed, and the alert gate now reads real data.

More detail is in [POSTMORTEMS.md](POSTMORTEMS.md).

## Calibration data (Turso)

Device calibration history lives in a shared [Turso](https://turso.tech) database, which is the single source of truth. A GitHub Actions job (`.github/workflows/snapshot.yml`) runs every 2 hours and writes IBM and IonQ snapshots to it: device status, per-qubit and per-pair errors, single-qubit gate errors, IonQ extras, a compressed raw IBM archive, and a run-health log. Nothing is collected or stored locally.

Tools such as `get_alerts`, `device_history` and `device_on_date`, and the pre-submission drift gate in `submit_job`, read from it. Set `TURSO_DATABASE_URL` and `TURSO_AUTH_TOKEN` to enable it. Without them, tools that need history say so, and everything else still works. Your own job records stay local and are never uploaded.

## Quick start

```bash
git clone https://github.com/Lokesh-2025/quantum-verifier.git
cd quantum-verifier
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # add IBM_QUANTUM_TOKEN and IONQ_API_KEY (optional for local simulation)
python mcp_server.py
```

Requires Python 3.12+ and Qiskit 2.0+. `cudaq` is optional (`pip install cudaq`).

### Connect to Claude Desktop

Add to `~/Library/Application Support/Claude/claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "quantum-verifier": {
      "command": "/absolute/path/to/.venv/bin/python",
      "args": ["/absolute/path/to/quantum-verifier/mcp_server.py"]
    }
  }
}
```

Restart Claude Desktop. For Claude Code: `claude mcp add quantum-verifier -- /absolute/path/to/.venv/bin/python /absolute/path/to/mcp_server.py`.

## Tools (53)

**Verification**
`verify_experiment` · `falsify_claim` · `find_robust_circuit` · `verify_stabilizer_circuit` · `verify_stabilizer_hardware_result` · `analyze_circuit_gradients` · `diff_compilers` · `find_optimal_backend` · `run_ghz_parity_check` · `run_graph_coloring_search` · `run_hybrid_reduction_loop` · `run_parallel_rail_search` · `correct_for_multiple_comparisons`

**Learning and tracking**
`memory_summary` · `verdict_track_record` · `recommend_tolerance` · `tolerance_v2_disagreement_log` · `shadow_mode_disagreement_log` · `check_taxonomy` · `ibm_sync_memory_for_job` · `ionq_sync_memory_for_job`

**IBM:** devices, calibration, jobs
`ibm_account_check` · `list_devices` · `get_device_details` · `best_qubits` · `best_qubits_for_reproducibility` · `compare_devices` · `queue_status` · `device_history` · `device_profile` · `device_on_date` · `check_chip_identity` · `audit_calibration_telemetry` · `get_alerts` · `submit_job` · `job_status` · `job_results` · `cancel_job` · `list_jobs` · `estimate_runtime` · `route_job` · `start_repro_experiment` · `repro_score` · `job_analytics`

**IonQ:** devices, jobs, cost
`ionq_account_check` · `ionq_compare_devices` · `ionq_preflight` · `ionq_devices` · `ionq_submit_job` · `ionq_job_status` · `ionq_job_results` · `estimate_ionq_gates` · `estimate_ionq_cost`

## Project structure

```
core/         verifier pipeline, control experiments, stabilizer checks, memory, Turso client
providers/    IBM and IonQ integrations
adapters/     one interface over IBM / IonQ / PennyLane / CUDA-Q
mcp_server.py thin MCP wrapper (core/ and providers/ have no MCP dependency)
cloud_snapshot.py   2-hour collector that writes calibration data to Turso
```

## Standalone agent (`agent/`)

A self-contained Node.js agent (contributed by [Jack Woehr](https://github.com/jwoehr)) that uses the MCP server without Claude Desktop or any other MCP host. Questions are routed to Core, IBM or IonQ specialist subagents, each running a reason-and-act loop over the same 53 tools. It works with Gemini, Ollama, OpenAI, Anthropic or vLLM, and offers a terminal chat and a `POST /chat` HTTP endpoint. See [`agent/README.md`](agent/README.md) for setup, provider configuration and Docker.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) and the [Code of Conduct](CODE_OF_CONDUCT.md).

## License

MIT, see [LICENSE](LICENSE).
