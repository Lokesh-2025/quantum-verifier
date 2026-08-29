/**
 * Dispatcher agent — routes user questions to Core, IBM, or IonQ subagents via stdio.
 *
 * Architecture:
 *   chat.js → HTTP → dispatcher (this file)
 *                       ├── child_process.spawn → core-subagent.js  (stdio)
 *                       ├── child_process.spawn → ibm-subagent.js   (stdio)
 *                       └── child_process.spawn → ionq-subagent.js  (stdio)
 *
 * The dispatcher asks the LLM to classify the question (Core / IBM / IonQ),
 * then spawns the appropriate subagent, passes the question via stdin,
 * and returns the subagent's stdout answer back to the user.
 */

const express = require('express');
const cors = require('cors');
const path = require('path');
const { spawn } = require('child_process');
const ProviderFactory = require('./shared/providers/provider-factory');
const ProviderConfig = require('./shared/config/provider-config');
const { requestLoggerMiddleware, createLogger } = require('./lib/request-logger');
const { getLLMLimiter } = require('./shared/concurrency/limiters');
require('dotenv').config();

// Validate config at startup
let providerName, providerConfig;
try {
    const config = ProviderConfig.validate();
    providerName = config.provider;
    providerConfig = config.config;
} catch (error) {
    console.error('Configuration Error:', error.message);
    process.exit(1);
}

const app = express();
const port = process.env.PORT || 3041;

app.use(express.json());
app.use(cors());
app.use(requestLoggerMiddleware);

let llmProvider = null;

// Subagent script paths
const SUBAGENTS = {
    Core: path.join(__dirname, 'subagents/core-subagent.js'),
    IBM:  path.join(__dirname, 'subagents/ibm-subagent.js'),
    IonQ: path.join(__dirname, 'subagents/ionq-subagent.js'),
};

/**
 * Ask the LLM to classify which domain the question targets.
 * Returns 'Core', 'IBM', or 'IonQ'.
 *
 * Core  — verifier pipeline, falsification, robustness, templates, memory,
 *          stabilizer, multi-compiler, optimal-backend, multiple-comparison correction
 * IBM   — IBM device listings, calibration, job submission, queue, alerts, reproducibility
 * IonQ  — IonQ devices, cost estimation, batched submission, budget checks, preflight
 */
async function classifyDomain(question, logger) {
    const chat = await llmProvider.createChat([]);
    const prompt = `You are a quantum verification router. Classify which domain this question targets.

Available domains:
  Core  — circuit verification pipeline (verify_experiment, falsify_claim, find_robust_circuit,
           stabilizer checks, compiler diff, optimal backend, experiment templates,
           experiment memory, tolerance recommendation, multiple-comparison correction)
  IBM   — IBM Quantum device discovery, calibration, job lifecycle (submit, status, results,
           cancel), queue, drift alerts, reproducibility, account/quota, chip identity
  IonQ  — IonQ devices, trapped-ion job submission, cost estimation, preflight, account/budget

Question: "${question}"

Reply with ONLY one of these JSON objects — nothing else:
{ "domain": "Core" }
{ "domain": "IBM" }
{ "domain": "IonQ" }

If the question spans multiple domains, prefer Core. If unsure, default to Core.`;

    const sendResult = await llmProvider.sendMessage(chat, prompt);
    const text = await llmProvider.extractTextResponse(sendResult.response || sendResult);

    try {
        const start = text.indexOf('{');
        const end   = text.lastIndexOf('}');
        const parsed = JSON.parse(text.substring(start, end + 1));
        const domain = ['Core', 'IBM', 'IonQ'].includes(parsed.domain) ? parsed.domain : 'Core';
        logger.log(`[Dispatcher] Routed to: ${domain}`);
        return domain;
    } catch {
        logger.log('[Dispatcher] Classification failed, defaulting to Core');
        return 'Core';
    }
}

/**
 * Spawn a subagent process, send it the question via stdin,
 * and return its answer from stdout.
 *
 * Protocol: subagent writes __RESULT__\n<json> to stdout so we can
 * extract the result even when the LLM response contains curly braces
 * inside code blocks (which broke the old indexOf/lastIndexOf approach).
 */
function callSubagent(domain, question, history, logger, noLocal = false) {
    const timeoutMs = parseInt(process.env.SUBAGENT_TIMEOUT_MS || '120000');

    return new Promise((resolve, reject) => {
        const scriptPath = SUBAGENTS[domain];
        logger.log(`[Dispatcher] Spawning ${domain} subagent: ${scriptPath}${noLocal ? ' (local LLM bypassed)' : ''}`);

        const child = spawn('node', [scriptPath], {
            env: { ...process.env },
            stdio: ['pipe', 'pipe', 'pipe'],
        });

        // Kill the subagent if it doesn't finish within the timeout
        const timer = setTimeout(() => {
            child.kill('SIGTERM');
            reject(new Error(`${domain} subagent timed out after ${timeoutMs}ms`));
        }, timeoutMs);

        // Send question + history (+ noLocal flag) to subagent stdin
        child.stdin.write(JSON.stringify({ question, history: history || [], noLocal }));
        child.stdin.end();

        let stdout = '';
        let stderr = '';

        child.stdout.on('data', chunk => { stdout += chunk; });
        child.stderr.on('data', chunk => { stderr += chunk; });

        child.on('close', code => {
            clearTimeout(timer);
            if (stderr) logger.log(`[${domain} subagent stderr] ${stderr.trim()}`);

            // Primary: look for sentinel line written by subagent
            const sentinelIdx = stdout.indexOf('__RESULT__\n');
            if (sentinelIdx !== -1) {
                try {
                    resolve(JSON.parse(stdout.slice(sentinelIdx + '__RESULT__\n'.length).trim()));
                    return;
                } catch { /* fall through to legacy extraction */ }
            }

            // Fallback: extract first complete JSON object (legacy subagents)
            const start = stdout.indexOf('{');
            const end   = stdout.lastIndexOf('}');
            if (start !== -1 && end > start) {
                try {
                    resolve(JSON.parse(stdout.substring(start, end + 1)));
                    return;
                } catch { /* fall through to error */ }
            }
            reject(new Error(`${domain} subagent returned invalid JSON (exit ${code}): ${stdout.substring(0, 300)}`));
        });

        child.on('error', err => { clearTimeout(timer); reject(new Error(`Failed to spawn ${domain} subagent: ${err.message}`)); });
    });
}

// --- Chat Endpoint ---
app.post('/chat', async (req, res) => {
    // Guard: provider not ready yet (startup race)
    if (!llmProvider) {
        return res.status(503).json({ status: 'error', answer: 'Server is still starting up — try again in a moment.' });
    }

    // Input validation
    const { question, history, noLocal } = req.body;

    // Fast ping — respond immediately without invoking the LLM
    if (question && question.trim().toLowerCase() === 'ping') {
        return res.json({ status: 'complete', answer: 'pong' });
    }

    if (!question || typeof question !== 'string') {
        return res.status(400).json({ status: 'error', answer: 'No question provided.' });
    }
    if (question.length > 32768) {
        return res.status(400).json({ status: 'error', answer: 'Question too long (max 32 KB).' });
    }
    if (history !== undefined && !Array.isArray(history)) {
        return res.status(400).json({ status: 'error', answer: 'history must be an array.' });
    }

    try {
        req.logger.log(`[Chat] Question received (${question.length} chars)`);

        // 1. Classify which domain to route to
        const llmLimiter = await getLLMLimiter(providerName);
        const domain = await llmLimiter(() => classifyDomain(question, req.logger));

        // 2. Spawn the appropriate subagent and get the answer
        const result = await callSubagent(domain, question, history, req.logger, !!noLocal);

        return res.json({
            status: 'complete',
            answer: result.answer,
            metadata: { domain, ...result.metadata }
        });

    } catch (error) {
        req.logger.error('Error in dispatcher:', error);
        res.status(500).json({
            status: 'error',
            answer: 'Sorry, there was an error processing your request.'
        });
    }
});

// Health check — returns tool list so dashboard /health can surface it
app.get('/health', (req, res) => {
    const tools = Object.keys(SUBAGENTS);
    res.json({
        status: 'ok',
        subagents: tools,
        tools,
        toolsLoaded: tools.length,
    });
});

// --- Server Startup ---
app.listen(port, async () => {
    const startupLogger = createLogger('startup');
    startupLogger.log('╔══════════════════════════════════════════════════════════╗');
    startupLogger.log('║   Quantum Verifier MCP Dispatcher Agent                  ║');
    startupLogger.log('╚══════════════════════════════════════════════════════════╝');
    startupLogger.log(`\nServer starting at http://localhost:${port}`);
    startupLogger.log(`LLM Provider: ${providerName}`);
    startupLogger.log(`Subagents: ${Object.keys(SUBAGENTS).join(', ')}`);

    try {
        llmProvider = await ProviderFactory.createProvider(providerName, providerConfig);
        const metadata = llmProvider.getMetadata();
        startupLogger.log(`Model: ${metadata.model || 'unknown'}`);
        startupLogger.log(`\n✓ Dispatcher ready at http://localhost:${port}\n`);
    } catch (error) {
        startupLogger.error('\nFATAL: Failed to start dispatcher:', error.message);
        process.exit(1);
    }
});

process.on('SIGINT',  () => { console.log('\n👋 Dispatcher shutting down.'); process.exit(0); });
process.on('SIGTERM', () => { console.log('\n👋 Dispatcher shutting down.'); process.exit(0); });

// Made with Bob
