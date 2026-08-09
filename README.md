# quantum-verifier

A safety gate between an AI-generated quantum circuit and real hardware.

## Why this exists

Building [quantum-hardware-mcp](https://github.com/Lokesh-2025/quantum-hardware-mcp) surfaced a repeated pattern: circuits that *looked* correct — parsed fine, ran without error, produced plausible-looking numbers — were actually wrong in ways that never crashed anything. Wrong bit ordering. Wrong native gate family. A type hint that silently stopped a real fix from ever reaching anyone. None of these threw an exception. All of them were only caught by deliberately testing against known ground truth.

This project is the generalized version of that lesson: instead of trusting a circuit's own claim about what it does, verify it — simulate it ideally, simulate it against the real target device's actual noise behavior, and check whether the claimed result is genuinely distinguishable from what hardware noise alone would produce.

## What it does

**`verify_experiment`** — the core safety gate. Checks a circuit's semantics (is it well-formed?), its topology risk (will IBM's heavy-hex routing explode the gate count?), an ideal simulation, and a hardware-aware simulation using the real target device's actual noise model (IonQ) or live calibration data (IBM). If you supply a known expected result, it checks whether that claim actually survives contact with real hardware noise — and refuses (`BLOCK`) if it doesn't.

**`falsify_claim`** — the flagship capability. Automatically builds a *control circuit*: the same circuit with its entangling gates removed, everything else identical. Runs both through the same simulation and reports the isolated, confound-free effect size — the real contribution of entanglement, with SPAM/readout bias (which affects both circuits equally) subtracted out. Works even without a known expected answer, which is what makes it usable for genuine discovery-mode research, not just known-answer verification.

Plus the full device-intelligence, job-lifecycle, and pre-flight tooling for both IBM and IonQ, copied and adapted from `quantum-hardware-mcp`.

## Relationship to quantum-hardware-mcp

This is a **separate, from-scratch project** — `quantum-hardware-mcp` was left completely untouched. This project copies over only the genuinely reusable parts (device intelligence, job lifecycle, IonQ's self-check submission pattern) and builds the Verifier and control-experiment generator fresh.

Deliberately left out of this phase: the Pascal's Triangle/Singmaster's-specific tools, the chemistry planner, the Node.js chat dispatcher, and the toy algorithm runners (`run_grover`, `run_vqe`). Only 35 of `quantum-hardware-mcp`'s 50 tools were ever genuinely general-purpose; this project is built from that general-purpose core, not a 1:1 port.

**Also deliberately not built yet**, per an explicit scoping decision: Experiment Memory, Postmortem explanation, and the Intelligence/recommendation layer. Multiple independent design consultations converged on the same ordering — verify first, build the smart advisor layer only once there's real evidence to reason over. This phase is the Verifier and the control-experiment generator only.

## Architecture

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
└── tests/
    ├── test_canaries.py         # endianness + angle-unit regression baseline
    └── test_verifier.py         # injected-bug benchmark, real E1 circuits
```

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # add IBM_QUANTUM_TOKEN and IONQ_API_KEY
pytest tests/
python mcp_server.py
```

## Verified state

15/15 tests passing — endianness/angle-unit canaries, semantic and topology BLOCK cases, IonQ's no-routing-risk case explicitly confirmed (not silently skipped), false-claim BLOCK, true-claim GO, the real E1 entangling circuits (independently verified predictions from `ionq-singmasters`) passing the Verifier's own check, and the control-experiment generator correctly isolating a real entangling effect on genuine hardware-noise-model data. All simulator-only — zero real hardware credits spent building or verifying this.
