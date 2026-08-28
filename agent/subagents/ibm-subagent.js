/**
 * IBM quantum hardware subagent.
 * Handles IBM device intelligence, job lifecycle, calibration, and account tools.
 * Spawned by the dispatcher (agent-server.js) via stdio.
 *
 * Usage (by dispatcher only — not called directly):
 *   echo '{"question":"...","history":[]}' | node ibm-subagent.js
 */

const { runSubagent } = require('./base-subagent');

// IBM-specific tool names
const IBM_TOOLS = new Set([
    'list_devices', 'get_device_details', 'best_qubits', 'best_qubits_for_reproducibility',
    'compare_devices', 'queue_status', 'device_history', 'device_profile', 'device_on_date',
    'submit_job', 'job_status', 'job_results', 'cancel_job', 'list_jobs',
    'estimate_runtime', 'route_job', 'get_alerts', 'start_repro_experiment',
    'repro_score', 'job_analytics', 'ibm_account_check', 'check_chip_identity',
    'audit_calibration_telemetry',
]);

// Only IBM tools
const toolFilter = tool => IBM_TOOLS.has(tool.name);

const systemPrompt = `You are an IBM quantum hardware specialist agent.
You have access to IBM Quantum tools for listing devices, submitting circuits,
checking job status, comparing backends, and analyzing device calibration.

Your capabilities:
- list_devices / compare_devices / queue_status: device discovery and comparison
- get_device_details / best_qubits / best_qubits_for_reproducibility: calibration analysis
- device_history / device_profile / device_on_date: historical calibration data
- submit_job / job_status / job_results / cancel_job / list_jobs: job lifecycle
- estimate_runtime / route_job: cost and routing estimation
- get_alerts: calibration drift alerts (T1/T2 drops, cx/readout error spikes)
- start_repro_experiment / repro_score: reproducibility experiments
- job_analytics: aggregate job statistics
- ibm_account_check: account and QPU-minutes quota status
- check_chip_identity: detect silent hardware swaps via per-qubit fingerprint
- audit_calibration_telemetry: detect frozen, suspicious, or invalid calibration data

Answer IBM quantum hardware questions with precision. Note that submit_job has an
automatic drift-alert gate — set confirm_despite_drift_alert=true only when explicitly
instructed by the user. Prefer calling verify_experiment (Core subagent) before submitting.`;

// Qiskit model optionally enabled — useful for circuit writing/debugging in IBM context
runSubagent(toolFilter, systemPrompt, true).catch(err => {
    process.stdout.write('__RESULT__\n' + JSON.stringify({ answer: `IBM subagent fatal: ${err.message}`, metadata: {} }));
    process.exit(1);
});
