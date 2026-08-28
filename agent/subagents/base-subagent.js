// Redirect ALL console output to stderr immediately.
// Subagents communicate with the dispatcher via stdout/stdin JSON.
// Any stray console.log (from dotenv, provider factories, etc.) would
// corrupt the JSON stream and break JSON.parse() in the dispatcher.
console.log   = (...a) => process.stderr.write(a.map(String).join(' ') + '\n');
console.warn  = (...a) => process.stderr.write(a.map(String).join(' ') + '\n');
console.error = (...a) => process.stderr.write(a.map(String).join(' ') + '\n');

/**
 * Base subagent — shared ReAct logic for Core, IBM, and IonQ subagents.
 *
 * Each subagent is a standalone Node.js process that:
 *   1. Reads a JSON question from stdin  { question, history }
 *   2. Connects to the quantum-verifier MCP server via SSE
 *   3. Runs a ReAct loop using only its filtered set of tools
 *   4. Writes a JSON answer to stdout    { answer, metadata }
 *   5. Exits
 *
 * The dispatcher (agent-server.js) spawns subagents via child_process.spawn
 * and communicates with them over stdio — no HTTP, no sockets.
 */

const path = require('path');
const { Client } = require('@modelcontextprotocol/sdk/client/index.js');
const { SSEClientTransport } = require('@modelcontextprotocol/sdk/client/sse.js');
const ProviderFactory = require('../shared/providers/provider-factory');
const ProviderConfig = require('../shared/config/provider-config');

// Load .env from the agent directory (one level up from subagents/)
require('dotenv').config({ path: path.join(__dirname, '../.env') });

const QISKIT_CODE_MODEL     = process.env.QISKIT_CODE_MODEL;
const QISKIT_CODE_MODEL_URL = process.env.QISKIT_CODE_MODEL_URL || 'http://localhost:11434';
const QISKIT_MODEL_TIMEOUT_MS = parseInt(process.env.QISKIT_MODEL_TIMEOUT_MS || '600000');

// Call the local Qiskit specialist model via Ollama
async function callQiskitModel(prompt) {
    const { Ollama } = require('ollama');
    const client = new Ollama({ host: QISKIT_CODE_MODEL_URL });
    const signal = AbortSignal.timeout(QISKIT_MODEL_TIMEOUT_MS);
    const response = await client.chat({
        model: QISKIT_CODE_MODEL,
        messages: [{ role: 'user', content: prompt }],
        stream: false,
        signal,
    });
    return response.message.content;
}

const MCP_TOOL_TIMEOUT_MS = parseInt(process.env.MCP_TOOL_TIMEOUT_MS || '60000');

// Call an MCP tool and return parsed result, with timeout
async function callTool(mcpClient, toolName, toolArguments) {
    const callPromise = mcpClient.callTool({ name: toolName, arguments: toolArguments });
    const timeoutPromise = new Promise((_, reject) =>
        setTimeout(() => reject(new Error(`Tool "${toolName}" timed out after ${MCP_TOOL_TIMEOUT_MS}ms`)), MCP_TOOL_TIMEOUT_MS)
    );
    const result = await Promise.race([callPromise, timeoutPromise]);
    if (result.content && Array.isArray(result.content)) {
        const text = result.content.filter(i => i.type === 'text').map(i => i.text).join('\n');
        try { return JSON.parse(text); } catch { return { text }; }
    }
    return result;
}

/**
 * Run a ReAct loop for a subagent.
 *
 * @param {object}   llmProvider    - LLM provider instance
 * @param {object}   chat           - LLM chat session
 * @param {string}   question       - User question
 * @param {string}   formattedTools - JSON string of available tools
 * @param {object}   mcpClient      - Connected MCP client
 * @param {string}   systemPrompt   - Provider-specific system context
 * @param {boolean}  qiskitEnabled  - Whether Qiskit model routing is on
 * @returns {Promise<string>}        Final answer text
 */
async function runReAct(llmProvider, chat, question, formattedTools, mcpClient, systemPrompt, qiskitEnabled) {
    const maxIterations = parseInt(process.env.MAX_ITERATIONS) || 10;
    let iterations = 0;
    let finalAnswer = 'Unable to complete the task within the maximum number of steps.';

    const qiskitLine = qiskitEnabled && QISKIT_CODE_MODEL
        ? `You have a Qiskit specialist model (${QISKIT_CODE_MODEL}) for quantum code questions. Use "model_call" action for any code request.`
        : '';

    // Only offer model_call as an option when the local model is actually available
    const modelCallLine = qiskitEnabled && QISKIT_CODE_MODEL
        ? '- Call Qiskit model:   { "action": "model_call", "prompt": "<question>" }'
        : '';

    let currentPrompt = `${systemPrompt}

Available tools:
${formattedTools}

${qiskitLine}

User request: "${question}"

Respond with ONLY one valid JSON object:
- Use a tool:          { "action": "tool", "toolName": "<name>", "toolArguments": {<args>} }
${modelCallLine}
- Give final answer:   { "action": "answer", "finalAnswer": "<text>" }`;

    while (iterations < maxIterations) {
        iterations++;

        const sendResult = await llmProvider.sendMessage(chat, currentPrompt);
        chat = sendResult.chat;
        const textResponse = await llmProvider.extractTextResponse(sendResult.response || sendResult);

        // Parse JSON action
        let action;
        try {
            action = JSON.parse(textResponse);
        } catch {
            const start = textResponse.indexOf('{');
            const end   = textResponse.lastIndexOf('}');
            if (start !== -1 && end > start) {
                try { action = JSON.parse(textResponse.substring(start, end + 1)); }
                catch { finalAnswer = textResponse; break; }
            } else {
                finalAnswer = textResponse;
                break;
            }
        }

        if (action.action === 'answer' || action.finalAnswer) {
            finalAnswer = action.finalAnswer || action.answer || textResponse;
            break;

        } else if (action.action === 'model_call') {
            if (qiskitEnabled && QISKIT_CODE_MODEL) {
                try {
                    const modelResponse = await callQiskitModel(action.prompt);
                    currentPrompt = `Qiskit model responded:\n${modelResponse}\n\nDecide next — respond with ONLY valid JSON.`;
                } catch (err) {
                    currentPrompt = `Qiskit model failed: ${err.message}\n\nFall back to tools or answer directly. Respond with ONLY valid JSON.`;
                }
            } else {
                // Local model bypassed — answer the code question directly
                currentPrompt = `Local Qiskit model is disabled. Answer this code question yourself using your own knowledge: ${action.prompt}\n\nRespond with ONLY valid JSON {"action":"answer","finalAnswer":"..."}`;
            }

        } else if (action.action === 'tool' || action.toolName) {
            const toolArgs = action.toolArguments || action.arguments || {};
            try {
                const result = await callTool(mcpClient, action.toolName, toolArgs);
                currentPrompt = `Tool "${action.toolName}" returned:\n${JSON.stringify(result, null, 2)}\n\nDecide next — respond with ONLY valid JSON.`;
            } catch (err) {
                currentPrompt = `Tool "${action.toolName}" failed: ${err.message}\n\nDecide next — respond with ONLY valid JSON.`;
            }

        } else {
            // Unrecognised action — do NOT treat as a final answer.
            // Feed an error back so the LLM corrects itself.
            const badAction = action.action || JSON.stringify(action).substring(0, 80);
            currentPrompt = `ERROR: "${badAction}" is not a valid action. You must respond with ONLY one of these JSON formats:\n- { "action": "tool", "toolName": "<name>", "toolArguments": {<args>} }\n${modelCallLine ? modelCallLine + '\n' : ''}- { "action": "answer", "finalAnswer": "<text>" }\n\nDo NOT write code. Call the appropriate tool directly.`;
        }
    }

    return finalAnswer;
}

/**
 * Entry point called by each subagent.
 *
 * @param {function(object): boolean} toolFilter   - Keeps only this subagent's tools
 * @param {string}                    systemPrompt - Provider-specific context for the LLM
 * @param {boolean}                   qiskitEnabled - Enable Qiskit model routing
 */
async function runSubagent(toolFilter, systemPrompt, qiskitEnabled = false) {
    // 1. Read question + history from dispatcher via stdin
    let raw = '';
    process.stdin.setEncoding('utf8');
    await new Promise(resolve => {
        process.stdin.on('data', chunk => { raw += chunk; });
        process.stdin.on('end', resolve);
    });

    let question, history, noLocal;
    try {
        ({ question, history, noLocal } = JSON.parse(raw));
    } catch (err) {
        process.stdout.write('__RESULT__\n' + JSON.stringify({ answer: `Subagent parse error: ${err.message}`, metadata: {} }));
        process.exit(1);
    }

    // 2. Connect to MCP server via SSE
    const mcpServerUri = process.env.QUANTUM_MCP_SERVER_URI;
    const mcpApiKey    = process.env.MCP_API_KEY;

    if (!mcpServerUri) {
        process.stdout.write('__RESULT__\n' + JSON.stringify({ answer: 'QUANTUM_MCP_SERVER_URI not set', metadata: {} }));
        process.exit(1);
    }

    const transportOptions = mcpApiKey
        ? { requestInit: { headers: { 'X-API-Key': mcpApiKey } } }
        : {};

    const transport = new SSEClientTransport(new URL(mcpServerUri), transportOptions);
    const mcpClient = new Client({ name: 'quantum-verifier-subagent', version: '1.0.0' }, { capabilities: {} });

    try {
        await mcpClient.connect(transport);
        await new Promise(resolve => setTimeout(resolve, 100)); // handshake settle
    } catch (err) {
        process.stdout.write('__RESULT__\n' + JSON.stringify({ answer: `MCP connection failed: ${err.message}`, metadata: {} }));
        process.exit(1);
    }

    // 3. Filter tools to only this subagent's domain
    let toolsResponse;
    try {
        const listPromise = mcpClient.listTools();
        const listTimeout = new Promise((_, reject) =>
            setTimeout(() => reject(new Error('listTools timed out after 30s')), 30000)
        );
        toolsResponse = await Promise.race([listPromise, listTimeout]);
    } catch (err) {
        process.stdout.write('__RESULT__\n' + JSON.stringify({ answer: `MCP listTools failed: ${err.message}`, metadata: {} }));
        await mcpClient.close();
        process.exit(1);
    }
    const myTools = toolsResponse.tools
        .map(t => ({ name: t.name, description: t.description, inputSchema: t.inputSchema }))
        .filter(toolFilter);

    if (myTools.length === 0) {
        process.stdout.write('__RESULT__\n' + JSON.stringify({
            answer: 'No tools available for this provider. Check that the MCP server has the required tools loaded and any necessary API keys are set in the root .env file.',
            metadata: { toolsAvailable: [] }
        }));
        await mcpClient.close();
        process.exit(0);
    }

    const formattedTools = JSON.stringify(myTools, null, 2);

    // 4. Set up LLM provider
    let llmProvider, chat;
    try {
        const config = ProviderConfig.validate();
        llmProvider = await ProviderFactory.createProvider(config.provider, config.config);
        const stdHistory = llmProvider.standardizeHistory(history || []);
        chat = await llmProvider.createChat(stdHistory);
    } catch (err) {
        process.stdout.write('__RESULT__\n' + JSON.stringify({ answer: `LLM setup failed: ${err.message}`, metadata: {} }));
        await mcpClient.close();
        process.exit(1);
    }

    // 5. Run ReAct loop
    let answer;
    try {
        // noLocal flag from dispatcher overrides qiskitEnabled for this request
        answer = await runReAct(llmProvider, chat, question, formattedTools, mcpClient, systemPrompt, qiskitEnabled && !noLocal);
    } catch (err) {
        answer = `Subagent error: ${err.message}`;
    }

    // 6. Send result back to dispatcher via stdout using sentinel protocol
    // Sentinel ensures the dispatcher can extract the JSON even when the
    // LLM response contains curly braces inside code blocks.
    process.stdout.write('__RESULT__\n' + JSON.stringify({
        answer,
        metadata: { toolsAvailable: myTools.map(t => t.name) }
    }));

    await mcpClient.close();
    process.exit(0);
}

module.exports = { runSubagent };
