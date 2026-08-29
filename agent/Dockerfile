FROM node:22-slim

WORKDIR /app

# Install dependencies first — separate layer for cache efficiency
COPY package.json package-lock.json ./
RUN npm ci --omit=dev

# Copy agent code
COPY . .

# All secrets (GEMINI_API_KEY, IBM_QUANTUM_TOKEN, IONQ_API_KEY, etc.) must be
# passed at runtime via -e or --env-file.
# QUANTUM_MCP_SERVER_URI should point at the quantum-verifier MCP server's
# SSE endpoint (e.g. http://verifier:3031/sse in a docker-compose network).
EXPOSE 3041

CMD ["node", "agent-server.js"]
