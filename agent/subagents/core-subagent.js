/**
 * Core quantum-verifier subagent.
 * Handles the verifier pipeline, falsification, robustness, experiment templates,
 * memory/intelligence, stabilizer, multi-compiler, and optimal-backend tools.
 * Spawned by the dispatcher (agent-server.js) via stdio.
 *
 * Usage (by dispatcher only — not called directly):
 *   echo '{"question":"...","history":[]}' | node core-subagent.js
 */

const { runSubagent } = require('./base-subagent');

// IBM-specific tool names (device/job set) — excluded from Core
const IBM_TOOLS = new Set([
    'list_devices', 'get_device_details', 'best_qubits', 'best_qubits_for_reproducibility',
    'compare_devices', 'queue_status', 'device_history', 'device_profile', 'device_on_date',
    'submit_job', 'job_status', 'job_results', 'cancel_job', 'list_jobs',
    'estimate_runtime', 'route_job', 'get_alerts', 'start_repro_experiment',
    'repro_score', 'job_analytics', 'ibm_account_check', 'check_chip_identity',
    'audit_calibration_telemetry',
]);

// Core tools: everything that is NOT an ionq_* tool AND NOT a dedicated IBM device/job tool
const toolFilter = tool =>
    !tool.name.startsWith('ionq_') && !IBM_TOOLS.has(tool.name);

const systemPrompt = `You are a quantum circuit verification specialist agent.
You have access to the quantum-verifier pipeline — a safety gate between AI-generated
quantum circuits and real quantum hardware.

Your core capabilities:
- verify_experiment: run the full GO/BLOCK safety pipeline (semantic, topology, ideal sim,
  hardware-aware sim, gate-synthesis check, ground-truth check)
- falsify_claim: auto-generate a control circuit and isolate the real confound-free effect size
- find_robust_circuit: select the most real-noise-resistant candidate from a set
- run_ghz_parity_check / run_graph_coloring_search: checkable-structure experiments
- verify_stabilizer_circuit / verify_stabilizer_hardware_result: exact Clifford verification
- diff_compilers: Qiskit vs TKET compilation diff, IBM devices
- find_optimal_backend: cross-provider cost/quality comparison (IBM vs IonQ)
- correct_for_multiple_comparisons: Holm-Bonferroni correction for batched verify results
- memory_summary / verdict_track_record / recommend_tolerance: prediction-accuracy tracking
- shadow_mode_disagreement_log / check_taxonomy: diagnostic and taxonomy tools

Answer questions about circuit verification, safety gating, noise robustness, and
experiment design. Be precise about GO/BLOCK verdicts and their reasons.`;

// Qiskit model disabled for Core — the verifier's job is circuit checking, not code generation
runSubagent(toolFilter, systemPrompt, false).catch(err => {
    process.stdout.write('__RESULT__\n' + JSON.stringify({ answer: `Core subagent fatal: ${err.message}`, metadata: {} }));
    process.exit(1);
});
