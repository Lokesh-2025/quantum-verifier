# quantum-verifier

A safety gate between an AI-generated quantum circuit and real quantum hardware. It checks whether a circuit is well-formed, whether it will survive real routing constraints, and — the flagship capability — whether a claimed result is actually distinguishable from what hardware noise alone would produce. It can do this even when you don't know the right answer in advance.

Companion project to [quantum-hardware-mcp](https://github.com/Lokesh-2025/quantum-hardware-mcp) — an open-source MCP server giving AI assistants live access to real quantum backends across IBM, IonQ, and AWS Braket.

---

## Why this exists

Circuits that *look* correct — parse fine, run without error, produce plausible numbers — can still be wrong in ways that never throw an exception. Wrong bit ordering. Wrong native gate family. A silently inflated gate count that changes what actually runs on the chip. None of these crash. All of them are only caught by deliberately checking against ground truth.

This project generalizes that lesson into a standalone check: instead of trusting a circuit's own claim about what it does, verify it — simulate it ideally, simulate it against the real target device's actual noise behavior, and check whether the claimed result genuinely survives contact with that noise.

---

## What we found using it on a real experiment

This isn't a demo built to look good — it caught a real bug in its own already-shipped results the first time it was used for real.

Before ever submitting a pre-registered experiment (characterizing angle-dependent error on IonQ's Forte-class hardware) to real hardware, its own rule requires a free-simulator dry run first. That dry run came back statistically shaky — not broken, just not clean. Chasing it down surfaced a real bug: Qiskit's transpiler had no direct translation between the standard `rzz` gate and IonQ's native `zz` gate, so it was silently falling back to expensive, noisy general synthesis — turning **1 logical two-qubit gate into 2 native gates plus ~39 extra single-qubit gates**.

That bug wasn't limited to the new experiment. It had already been quietly affecting results shipped in this repo's own test suite — the "isolated entangling effect" reported for a real research circuit moved from **0.0352 to 0.0735** once fixed, meaning the original number had been masking roughly half the real signal.

Fixing it surfaced two more real issues in the same code path:
- The bare `"simulator"` target silently defaults to IonQ's retired Aria-era gate family instead of the modern one used by Forte-class hardware.
- IonQ's native two-qubit gate is only valid for rotations up to a quarter turn — larger logical rotations need an exact multi-gate decomposition, not a naive 1:1 conversion.

All three are now fixed, and the fix itself became a permanent, general check (`gate_synthesis_check`) rather than a one-off patch — so the next circuit that hits this class of bug gets caught automatically. The angle-error experiment's own safety check still isn't fully clean after the fix (a smaller, reproducible discrepancy remains), so — correctly — that experiment stays blocked from real hardware until it is. That refusal is the tool doing exactly what it's for.

---

## What it does

**`verify_experiment`** — the core safety gate.

```
circuit + target device + (optional) expected result
        │
        ▼
  1. Semantic check         — is this a well-formed circuit at all?
        ▼
  2. Topology check          — IBM: will heavy-hex routing explode the gate count?
        ▼                       IonQ: no-op — all-to-all, no routing risk
  3. Ideal simulation        — what SHOULD happen with zero noise?
        ▼
  4. Hardware-aware sim      — what does the real target device's noise predict?
        ▼                       (full noisy simulation on IonQ; live-calibration
        ▼                        fidelity estimate on IBM — that asymmetry is
        ▼                        real and stated explicitly, not hidden)
  5. Gate synthesis check    — did the circuit map cleanly onto the target's
        ▼                       native gateset, or is this a hidden gate-family
        ▼                       mismatch inflating the "real" simulation?
  6. Ground-truth check      — if you supplied an expected answer, does the
        ▼                       claim survive contact with predicted hardware
        ▼                       behavior?
  GO / BLOCK, with a structured, human-readable reason
```

**`falsify_claim`** — the flagship capability. Automatically builds a *control circuit*: the same circuit with its entangling gates removed, everything else identical. Runs both through the same hardware-aware simulation and reports the real, confound-isolated effect size — SPAM/readout bias, which affects both circuits equally, cancels out. Works even with no known expected answer, which is what makes it usable for genuine discovery-mode research, not just checking known claims.

Plus the general-purpose device-intelligence, job-lifecycle, and IonQ pre-flight tooling, carried over from `quantum-hardware-mcp`.

---

## How it works

**Step 1 — Parse and validate.** `verify_experiment` rejects malformed circuits (empty, no measurements, mismatched classical registers) before anything else runs.

**Step 2 — Routing risk.** On IBM, any qubit needing more than 3 direct connections triggers SWAP injection that can inflate gate count 3-5x — caught here, before submission. On IonQ, this risk doesn't exist (all-to-all connectivity) and is reported as an explicit no-op, not silently skipped.

**Step 3 — Simulate twice.** Once with zero noise (the ideal case), once against the real target device's actual behavior — IonQ's own named noise models on the free simulator, or live calibration data on IBM.

**Step 4 — Catch gate-family mismatches.** If the circuit's gates don't map cleanly onto the target's native gateset, the transpiler can silently fall back to expensive re-synthesis that doesn't represent what the hardware actually runs. `gate_synthesis_check` flags that before it corrupts the result.

**Step 5 — Check the claim.** If you know the expected answer, `verify_experiment` checks whether it's actually distinguishable from predicted hardware noise. If you don't, `falsify_claim` builds a control circuit and reports the real effect size directly — no known answer required.

---

## Relationship to quantum-hardware-mcp

This is a separate, focused project — `quantum-hardware-mcp` is untouched. This repo carries over only the genuinely general-purpose device and job tools (device intelligence, job lifecycle, IonQ's self-checked submission pattern) and builds the Verifier and control-experiment generator fresh.

Deliberately left out: the Pascal's Triangle/Singmaster's-specific tools, the chemistry planner, and the toy algorithm runners (`run_grover`, `run_vqe`) — those stay in the main repo. Also deliberately not built yet: an Experiment Memory / Postmortem / recommendation layer on top of the Verifier — that comes once this phase has produced enough real data to learn from.

**Known gap:** right now these are two separate tool sets. Nothing forces `quantum-hardware-mcp`'s job-submission tools to route through this Verifier first — an assistant using the main tool today could still submit straight to hardware without it. Wiring the Verifier in as a mandatory, automatic gate in front of the main tool's submission path is the next real step, not yet done.

---

## Tools (27 total)

### Core — the Verifier and control-experiment generator

| Tool | What it does |
|------|-------------|
| `verify_experiment` | The safety gate — semantic, routing, gate-synthesis, and ground-truth checks, ending in a GO/BLOCK verdict |
| `falsify_claim` | Auto-generates a control circuit (entangling gates removed) and reports the real, confound-isolated effect size — no known answer required |

### IBM — device intelligence and job lifecycle

| Tool | What it does |
|------|-------------|
| `list_devices` | All accessible IBM backends with live operational status |
| `get_device_details` | Per-qubit T1/T2, readout error, gate error, queue depth |
| `best_qubits` | Score and rank qubits by calibration quality |
| `compare_devices` | Rank by CX error, queue depth, qubit count, or combined score |
| `queue_status` | Current queue snapshot across all backends |
| `device_history` | Calibration snapshots over the last N days |
| `device_profile` | Full calibration profile for one device |
| `device_on_date` | Exact calibration state on any past date — for reproducibility |
| `submit_job` | Transpile and submit OpenQASM 2.0/3.0 — returns `job_id` |
| `job_status` | QUEUED / RUNNING / DONE / ERROR |
| `job_results` | Bit-string measurement counts from a completed job |
| `cancel_job` | Cancel a queued or running job |
| `list_jobs` | Recent jobs with status, backend, and timestamps |
| `estimate_runtime` | QPU minutes + queue wait estimate before submitting |
| `route_job` | Credit-aware routing — cheapest backend that meets your error threshold |
| `get_alerts` | Calibration drift alerts — spikes in CX error, readout error, T1, T2 |
| `start_repro_experiment` | Run the same circuit N times, record variance |
| `repro_score` | KL-divergence reproducibility score |
| `job_analytics` | Aggregate stats across logged jobs |

### IonQ — devices, batched submission, cost estimation

| Tool | What it does |
|------|-------------|
| `ionq_devices` | All IonQ backends and simulators with live status |
| `ionq_submit_job` | Batched submission with a pre-flight self-check on the free simulator (real target device's noise model applied) before anything real is billed — one bad circuit refuses the whole batch |
| `ionq_job_status` | Job status, with `is_real_hardware` always reported explicitly |
| `ionq_job_results` | Measurement counts, single or batched |
| `estimate_ionq_gates` | Native gate count before submitting, transpiled against a real device's actual native target |
| `estimate_ionq_cost` | Dollar cost preview using IonQ's real per-job pricing floor |

---

## Project structure

```
quantum-verifier/
├── core/
│   ├── verifier.py            # the safety-gate pipeline
│   └── control_experiment.py  # auto-generated control circuits
├── providers/
│   ├── ibm.py                 # device intelligence + job lifecycle
│   └── ionq.py                # device listing, batched self-checked submission
├── mcp_server.py               # thin MCP wrapper — core/ and providers/ have
│                                # zero MCP dependency and can be imported and
│                                # used directly, without ever touching this file
├── tests/
│   ├── test_canaries.py             # endianness + angle-unit regression baseline
│   ├── test_verifier.py             # injected-bug benchmark, real research circuits
│   └── test_side_by_side_old_repo.py  # confirms copied tools match the originals
└── requirements.txt
```

---

## Test suite

```bash
pytest tests/
```

24 tests — endianness/angle-unit canaries, semantic and topology BLOCK cases, gate-synthesis inflation detection (the real bug class described above), wrong-measurement-basis detection, false-claim BLOCK / true-claim GO, real research circuits passing their independently-verified predictions, the control-experiment generator correctly isolating a real entangling effect, and a side-by-side diff against the original tool proving the copied device/job functions didn't silently drift. All simulator-only — zero real hardware credits spent building or verifying this.

`test_side_by_side_old_repo.py` needs `quantum-hardware-mcp` cloned as a sibling directory (`~/quantum-hardware-mcp`) with its own dependencies installed, since it imports that repo directly to diff against it.

---

## Quick start

```bash
git clone https://github.com/Lokesh-2025/quantum-verifier.git
cd quantum-verifier
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # add IBM_QUANTUM_TOKEN and IONQ_API_KEY
pytest tests/
python mcp_server.py
```

---

## Connect to Claude Desktop

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

Restart Claude Desktop. Both tools appear under the hammer icon.

---

## Roadmap

**Completed**
- [x] `verify_experiment` — semantic, topology, ideal + hardware-aware simulation, ground-truth check
- [x] `falsify_claim` — auto-generated control circuits, confound-isolated effect size
- [x] `gate_synthesis_check` — catches gate-family mismatches before they corrupt a simulation
- [x] IBM device intelligence + job lifecycle, carried over and diff-verified against the original
- [x] IonQ devices, batched self-checked submission, cost/gate estimation
- [x] Found and fixed a real transpile bug affecting already-shipped results (see above)

**Next**
- [ ] Wire this Verifier in as a mandatory, automatic gate in front of `quantum-hardware-mcp`'s job-submission tools, rather than two separate tool sets
- [ ] Resolve the residual discrepancy still blocking the angle-error experiment from real hardware
- [ ] Experiment Memory / Postmortem layer, built on real prediction-vs-reality data once available
- [ ] IBM hardware-aware simulation as a full noisy simulation, not just a fidelity estimate, if IBM's public API ever exposes an equivalent to IonQ's named noise models

---

## License

MIT — see [LICENSE](LICENSE).
