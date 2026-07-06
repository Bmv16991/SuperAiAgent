# SuperAiAgent MCP HTTP API

**Base URL:** `http://localhost:8766` (self-hosted) | Future: `https://api.superaiagent.dev`  
**Auth:** `X-API-Key: superai-xxxx` header required on all endpoints (except `/health`)  
**Protocol:** JSON-RPC 2.0 over HTTP POST `/mcp`

---

## Authentication

### API Key Tiers

| Tier | Spawns/Month | Queries/Day | Access |
|------|-------------|-------------|--------|
| `free` | 5 | 100 | Testing / evaluation |
| `pro` | Unlimited | Unlimited | Contact for key |

### Create an API Key (pro only)
```bash
POST /keys/create
X-API-Key: superai-pro-key

{"name": "my-project", "tier": "free"}
# Response: {"api_key": "superai-xxxx", "name": "my-project", "tier": "free"}
```

---

## MCP Tools

All tools are called via `POST /mcp` with JSON-RPC 2.0 format:

```json
{
  "jsonrpc": "2.0",
  "id": "req-001",
  "method": "tools/call",
  "params": {
    "name": "<tool_name>",
    "arguments": { ... }
  }
}
```

---

### `spawn_agent`

Spawn a new domain-specific AI agent from the SuperAiAgent foundation.

**Arguments:**
| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `domain` | string | yes | Domain to spawn: `forex-trading`, `crypto-trading`, `fivem-scripting`, `ui-design` |
| `output_dir` | string | no | Custom output directory (default: system-assigned) |

**Example:**
```bash
curl -X POST http://localhost:8766/mcp \
  -H "X-API-Key: superai-dev-key" \
  -H "Content-Type: application/json" \
  -d '{
    "jsonrpc": "2.0",
    "id": "1",
    "method": "tools/call",
    "params": {
      "name": "spawn_agent",
      "arguments": {"domain": "crypto-trading"}
    }
  }'
```

**Response:**
```json
{
  "jsonrpc": "2.0",
  "id": "1",
  "result": {
    "domain": "crypto-trading",
    "status": "spawned_now",
    "output_dir": "F:\\CryptoAgent",
    "manifest_excerpt": "# CryptoAgent — SPAWN MANIFEST\n...",
    "spawns_used": 1,
    "cost": {
      "model": "deepseek-chat",
      "input_tokens": 12,
      "output_tokens": 50,
      "raw_cost_usd": 0.000058,
      "margin_usd": 0.0000174,
      "total_usd": 0.0000754
    }
  }
}
```

**Status values:**
- `spawned_now` — newly created, KB built, agent directory ready
- `already_spawned` — agent was spawned in a previous call, returns existing manifest
- `spawn_failed` — script error, check `manifest_excerpt` for stderr
- `spawn_timeout` — timed out after 180s (large KB builds can take 50-60s)
- `no_script` — domain not yet implemented

---

### `query_foundation`

Semantic search over the 24,850-chunk GitHub knowledge base.

**Arguments:**
| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `query` | string | yes | Natural language or code query |
| `top_k` | integer | no | Number of results (default: 5) |

**Example:**
```bash
curl -X POST http://localhost:8766/mcp \
  -H "X-API-Key: superai-dev-key" \
  -H "Content-Type: application/json" \
  -d '{
    "jsonrpc": "2.0",
    "id": "2",
    "method": "tools/call",
    "params": {
      "name": "query_foundation",
      "arguments": {"query": "React component design system Tailwind", "top_k": 3}
    }
  }'
```

**Response:**
```json
{
  "jsonrpc": "2.0",
  "id": "2",
  "result": {
    "results": [
      {
        "score": 0.89,
        "repo_name": "shadcn-ui/ui",
        "file_path": "components/button.tsx",
        "content": "...",
        "domain": "frontend"
      }
    ]
  }
}
```

---

### `list_capabilities`

Returns current KB sizes, spawned agents, and available domains.

**No arguments required.**

**Example:**
```bash
curl -X POST http://localhost:8766/mcp \
  -H "X-API-Key: superai-dev-key" \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":"3","method":"tools/call","params":{"name":"list_capabilities","arguments":{}}}'
```

**Response:**
```json
{
  "result": {
    "domains_available": ["forex-trading", "crypto-trading", "fivem-scripting", "ui-design"],
    "already_spawned": ["crypto-trading", "fivem-scripting"],
    "kb": {
      "gh_knowledge": 24850,
      "gh_patterns": 868,
      "gh_synthesis": 305
    },
    "domain_kbs": {
      "trading_kb": 294,
      "crypto_kb": 303,
      "fivem_kb": 404
    },
    "procedural_memory": 57,
    "skills_total": 1052
  }
}
```

---

### `get_blueprint`

Get the SuperAiAgent Blueprint: current readiness score, architecture overview, and next steps.

**No arguments required.**

**Example:**
```bash
curl -X POST http://localhost:8766/mcp \
  -H "X-API-Key: superai-dev-key" \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":"4","method":"tools/call","params":{"name":"get_blueprint","arguments":{}}}'
```

---

## REST Endpoints

### `GET /health`
No auth. Returns `{"status": "ok", "version": "1.0.0"}`.

### `GET /usage/{api_key}`
Returns token usage stats for the given key. Requires auth with any valid key.

```json
{
  "api_key": "superai-d...",
  "name": "dev",
  "tier": "pro",
  "usage": {
    "spawns_this_month": 2,
    "queries_today": 14,
    "tokens_consumed": 5820
  }
}
```

### `GET /billing/{api_key}`
Returns billing log (last 50 calls) and cost totals.

```json
{
  "summary": {
    "total_calls": 12,
    "total_raw_cost_usd": 0.000142,
    "total_margin_usd":   0.0000426,
    "total_charged_usd":  0.0001846
  },
  "recent": [...]
}
```

### `GET /procedural_memory/stats`
Returns Hermes skill execution statistics.

### `GET /procedural_memory/query?q=...&domain=...&top_k=5`
Semantic search over procedural memory (skill execution traces).

### `GET /.well-known/mcp`
MCP agent card — machine-readable capability descriptor.

---

## Error Codes

| HTTP Status | Meaning |
|------------|---------|
| `401` | Invalid or missing API key |
| `403` | Tier restriction (e.g., creating keys requires pro tier) |
| `429` | Quota exceeded — spawns/month or queries/day limit hit |
| `400` | Bad request — unknown tool or method |
| `500` | Internal error — check Hermes logs |

---

## Rate Limits

- `free` tier: 5 spawn_agent calls/month, 100 query_foundation calls/day
- `pro` tier: no limits (fair use)
- All tiers: billing tracked per call with 30% margin on LLM token costs

---

*SuperAiAgent API v1.0 — 2026-07-06*
