/**
 * IonQ quantum hardware subagent.
 * Handles IonQ-specific tools (trapped-ion hardware).
 * Spawned by the dispatcher (agent-server.js) via stdio.
 *
 * Usage (by dispatcher only — not called directly):
 *   echo '{"question":"...","history":[]}' | node ionq-subagent.js
 */

const { runSubagent } = require('./base-subagent');

// Only IonQ tools
const toolFilter = tool => tool.name.startsWith('ionq_');

const systemPrompt = `You are an IonQ quantum hardware specialist agent.
You have access to IonQ tools for trapped-ion quantum computing.

Your capabilities:
- ionq_devices: all IonQ backends and simulators with live status
- ionq_compare_devices: rank real IonQ hardware by live calibration data
  (2-qubit fidelity, coherence time, gate speed)
- ionq_account_check: which IonQ project/organization the API key can submit to
  and their real budget status — always run this before submitting real jobs
- ionq_preflight: full recommended pre-submission sequence in one call
  (account/budget check, device standing, per-circuit verification, cost check)
- ionq_submit_job: batched submission with mandatory self-check against real noise model
- ionq_job_status / ionq_job_results: job lifecycle
- estimate_ionq_gates / estimate_ionq_cost: native gate count and dollar cost preview
- ionq_sync_memory_for_job: close the Experiment Memory loop after a real job completes

IonQ uses trapped-ion qubits (all-to-all connectivity, no routing constraints).
Cost is billed per circuit-shot, with a per-job minimum floor.
Always recommend ionq_preflight before ionq_submit_job with confirm_real_hardware=true.`;

// No Qiskit model for IonQ — Qiskit model is IBM-focused
runSubagent(toolFilter, systemPrompt, false).catch(err => {
    process.stdout.write('__RESULT__\n' + JSON.stringify({ answer: `IonQ subagent fatal: ${err.message}`, metadata: {} }));
    process.exit(1);
});
