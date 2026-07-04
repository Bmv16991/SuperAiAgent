# SuperAiAgent

> A production-grade AI Agent framework with semantic skill routing, self-evolution, and MCP Server support.

[![Python](https://img.shields.io/badge/Python-3.11+-blue?logo=python)](https://python.org)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.110+-green?logo=fastapi)](https://fastapi.tiangolo.com)
[![Qdrant](https://img.shields.io/badge/Qdrant-Vector_DB-red)](https://qdrant.tech)
[![MCP](https://img.shields.io/badge/MCP-Compatible-purple)](https://modelcontextprotocol.io)
[![License](https://img.shields.io/badge/License-MIT-yellow)](LICENSE)

---

## What is SuperAiAgent?

SuperAiAgent is an open-source framework for building AI agents that **route tasks to specialized skills**, **learn from experience**, and **create new skills automatically** when none exist.

Unlike LLM wrappers that rely on a single model call, SuperAiAgent uses a **pipeline of system-2 reasoning**:

```
Task
 │
 ▼
┌─────────────────────────────────────────────────────┐
│  1. Episodic Memory recall (past similar tasks)     │
│  2. Deliberation Engine (which path to take?)       │
│  3. Semantic Skill Router (find best skill)         │
│     ├─ Skill found → Execute                        │
│     └─ No skill    → SkillFactory (create new one)  │
│  4. Execute skill (Python code, real output)        │
│  5. Store result in memory + evolution log          │
└─────────────────────────────────────────────────────┘
 │
 ▼
Result
```

---

## Features

- **Semantic Skill Routing** — multilingual embeddings (50+ languages) match tasks to the right skill, not just keywords
- **Self-Evolution** — Novelty Gate prevents duplicate skills; SkillFactory auto-creates skills for unknown tasks
- **MCP Server** — 5 tools ready for Claude Desktop, VS Code, and any MCP-compatible client
- **Async REST API** — `/run_async` + polling pattern for long-running tasks (no timeout issues)
- **Tiered Memory** — Episodic, Working, and Semantic memory layers
- **Deliberation Engine** — System 2 thinking: DIRECT / RECALL / SEARCH / DECOMPOSE paths
- **Multi-LLM Fallback** — DeepSeek → Qwen → Claude Opus, automatic failover
- **Knowledge Base** — Qdrant vector DB for semantic search over your domain knowledge

---

## Architecture

```
SuperAiAgent/
├── hermes_api.py          ← REST API server (FastAPI)
├── mcp_server.py          ← MCP Server (5 tools)
├── core/
│   ├── orchestrator.py    ← Main pipeline
│   ├── skill_router.py    ← Semantic routing (multilingual)
│   ├── skill_executor.py  ← Runs skill Python code
│   ├── skill_factory.py   ← Auto-creates new skills via LLM
│   ├── deliberation_engine.py  ← System 2 path selection
│   ├── task_decomposer.py ← Multi-step planning
│   ├── episodic_memory.py ← Long-term memory (JSON)
│   ├── working_memory.py  ← Session context
│   ├── hermes_memory.py   ← Tiered memory (Qdrant)
│   ├── evolution.py       ← Evolution log tracker
│   ├── skill_leaderboard.py    ← Skill performance ranking
│   ├── knowledge_gap.py   ← Failure-driven KB gap detection
│   └── llm.py             ← Multi-provider LLM gateway
├── config/
│   └── settings.py        ← Paths, Qdrant URL, collections
└── skills/
    └── examples/          ← Demo skills (see Creating Skills)
```

---

## Quickstart

### 1. Clone & install

```bash
git clone https://github.com/YOUR_USERNAME/SuperAiAgent.git
cd SuperAiAgent
pip install -r requirements.txt
```

### 2. Configure environment

```bash
cp .env.example .env
# Edit .env — add your LLM API keys
```

### 3. Start Qdrant (vector DB)

```bash
docker run -p 6333:6333 qdrant/qdrant
```

### 4. Start the API server

```bash
python hermes_api.py
# → Running on http://0.0.0.0:8765
```

### 5. Run your first task

```bash
curl -X POST http://localhost:8765/run_async \
  -H "X-API-Key: your-key" \
  -H "Content-Type: application/json" \
  -d '{"task": "Find Python rate limiting patterns for FastAPI"}'

# Returns: {"job_id": "abc-123", "status": "running"}

# Poll for result:
curl http://localhost:8765/result/abc-123 \
  -H "X-API-Key: your-key"
```

---

## MCP Server Setup

Add to your Claude Desktop / VS Code config:

```json
{
  "mcpServers": {
    "superai": {
      "command": "python",
      "args": ["F:/SuperAiAgent/mcp_server.py"],
      "env": {
        "HERMES_API_KEY": "your-key",
        "HERMES_API_URL": "http://localhost:8765"
      }
    }
  }
}
```

### Available MCP Tools

| Tool | Description |
|------|-------------|
| `hermes_run` | Run any task through the full agent pipeline |
| `hermes_search` | Semantic search across all available skills |
| `hermes_kb_query` | Search your knowledge base (Qdrant) |
| `hermes_memory` | Store / retrieve / stats on tiered memory |
| `hermes_stats` | System status: skill count, KB size, memory |

---

## REST API Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| `POST` | `/run` | Synchronous task execution |
| `POST` | `/run_async` | Async task → returns `job_id` |
| `GET` | `/result/{job_id}` | Poll async job status |
| `POST` | `/search` | Semantic skill search |
| `POST` | `/kb_query` | Knowledge base search |
| `POST` | `/memory` | Memory store/retrieve/stats |
| `GET` | `/stats` | System statistics |
| `GET` | `/health` | Health check (no auth) |

---

## Creating Skills

Each skill is a folder with a `SKILL.md` file:

```
skills/
└── my-skill/
    ├── SKILL.md     ← metadata + Python code
    └── (optional supporting files)
```

**SKILL.md format:**

```markdown
---
name: my-skill
description: "One-line description of what this skill does"
tags: [python, api, data]
---

## Purpose
What this skill does and when to use it.

## Code
```python
def run(task: str, context: str = "") -> str:
    # Your skill logic here
    return f"Result for: {task}"
```
```

Skills are auto-discovered at startup — no registration needed. The semantic router uses the description + Purpose section to match incoming tasks.

---

## LLM Providers

SuperAiAgent supports multi-tier LLM routing with automatic fallback:

| Tier | Primary | Fallback | Use Case |
|------|---------|----------|----------|
| `flash` | DeepSeek V4-Flash | Cloudflare → Groq | Routine tasks, skill creation |
| `plus` | Qwen3.7-Plus | DeepSeek → Groq | KB synthesis, upgrades |
| `max` | Qwen3.7-Max | Qwen-Plus → DeepSeek | Complex reasoning |
| `opus` | Claude Opus | Qwen-Max → DeepSeek | Critical one-time tasks |

Configure in `.env`:
```
DEEPSEEK_API_KEY=...
OPENROUTER_API_KEY=...
CF_API_KEY=...
GROQ_API_KEY=...
```

---

## Skill Routing

Tasks are matched to skills using **semantic similarity** (not keywords):

1. **Semantic Router** — cosine similarity vs skill embeddings (threshold 0.55)
2. **Keyword Table** — domain-specific fallback rules
3. **LLM Classify** — last resort classification
4. **SkillFactory** — auto-create if no match found

The router uses `paraphrase-multilingual-MiniLM-L12-v2` — works in **50+ languages** including Thai, Japanese, Arabic, etc.

---

## Self-Evolution

SuperAiAgent improves itself over time:

- **Novelty Gate** — rejects duplicate skills (cosine > 0.92 with existing)
- **Evolution Log** — tracks every task: skill, success, confidence
- **Skill Leaderboard** — reranks skills by historical performance
- **Knowledge Gap** — detects failure patterns → triggers KB search

---

## Open Source Philosophy

> **Moat = trained data, not code**

The framework (this repo) is open source. The intelligence comes from:
- Skills trained on your domain data
- Knowledge base built from your repos/documents
- Episodic memory accumulated from real usage

Clone this repo + any LLM key = empty shell.  
Run it on your data for 6 months = powerful agent.

---

## Contributing

1. Fork the repo
2. Create a skill in `skills/examples/`
3. Test: `python -c "from core.orchestrator import Orchestrator; o=Orchestrator(); print(o.execute('your task'))"`
4. Open a PR with your skill + test result

Bug reports and feature requests welcome via Issues.

---

## License

MIT — see [LICENSE](LICENSE)

---

*Built with FastAPI · Qdrant · SentenceTransformers · Model Context Protocol*
