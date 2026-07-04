"""
mcp_server.py — SuperAiAgent MCP Server

Exposes 5 tools for Claude Desktop, VS Code, and any MCP-compatible client.

Tools:
  superai_run      — execute any task (full orchestrator pipeline)
  superai_search   — find skills by semantic similarity
  superai_kb_query — query knowledge base (Qdrant)
  superai_memory   — store/retrieve tiered memory
  superai_stats    — system status snapshot

Setup in Claude Desktop / VS Code:
  "mcpServers": {
    "superai": {
      "command": "python",
      "args": ["path/to/mcp_server.py"],
      "env": {
        "HERMES_API_KEY": "your-key",
        "QDRANT_URL": "http://localhost:6333"
      }
    }
  }
"""
import sys
import os
import asyncio
from pathlib import Path

ROOT_DIR = Path(__file__).parent.resolve()
sys.path.insert(0, str(ROOT_DIR))
os.chdir(ROOT_DIR)

from dotenv import load_dotenv
load_dotenv()

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp import types
from loguru import logger

logger.remove()
logger.add(sys.stderr, level="INFO", format="{time:HH:mm:ss} | {level} | {message}")

from config.settings import QDRANT_URL, ST_CACHE_PATH, EMBED_MODEL, SKILL_VECTORS_PATH

app = Server("superai-agent")

_orchestrator  = None
_qdrant_client = None
_encoder       = None
_skill_cache: dict = {}


def _get_orchestrator():
    global _orchestrator
    if _orchestrator is None:
        from core.orchestrator import Orchestrator
        _orchestrator = Orchestrator()
    return _orchestrator


def _get_encoder():
    global _encoder
    if _encoder is None:
        os.environ["TOKENIZERS_PARALLELISM"] = "false"
        os.environ["HF_HOME"] = ST_CACHE_PATH
        from sentence_transformers import SentenceTransformer
        _encoder = SentenceTransformer(EMBED_MODEL, device="cpu")
    return _encoder


def _get_kb_client():
    global _qdrant_client
    if _qdrant_client is None:
        from qdrant_client import QdrantClient
        _qdrant_client = QdrantClient(url=QDRANT_URL)
    return _qdrant_client, _get_encoder()


def _build_skill_cache():
    import numpy as np
    if "vecs" in _skill_cache:
        return _skill_cache
    if SKILL_VECTORS_PATH.exists():
        data = np.load(str(SKILL_VECTORS_PATH), allow_pickle=True)
        _skill_cache["names"] = data["names"].tolist()
        _skill_cache["descs"] = data["descs"].tolist()
        _skill_cache["vecs"]  = data["vecs"]
        logger.info(f"Loaded {len(_skill_cache['names'])} skill vectors")
    return _skill_cache


@app.list_tools()
async def list_tools() -> list[types.Tool]:
    return [
        types.Tool(
            name="superai_run",
            description="Run any task through SuperAiAgent (semantic routing → skill execution → self-learning). Use for: code generation, analysis, research, or any multi-step task.",
            inputSchema={
                "type": "object",
                "properties": {
                    "task": {"type": "string", "description": "Task in natural language"},
                    "context": {"type": "string", "description": "Optional extra context", "default": ""}
                },
                "required": ["task"]
            }
        ),
        types.Tool(
            name="superai_search",
            description="Search available skills by semantic similarity. Discover what SuperAiAgent can do before running superai_run.",
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "What you're looking for"},
                    "top_k": {"type": "integer", "description": "Number of results (default 5)", "default": 5}
                },
                "required": ["query"]
            }
        ),
        types.Tool(
            name="superai_kb_query",
            description="Query the knowledge base (Qdrant). Returns relevant code patterns and insights. Use for: finding implementation examples, researching best practices.",
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "What to search for"},
                    "top_k": {"type": "integer", "description": "Number of results (default 5, max 10)", "default": 5}
                },
                "required": ["query"]
            }
        ),
        types.Tool(
            name="superai_memory",
            description="Store or retrieve from tiered memory (episodic/semantic/procedural). Use store to save findings. Use retrieve to recall past work.",
            inputSchema={
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": ["store", "retrieve", "stats"]},
                    "tier":   {"type": "string", "enum": ["episodic", "semantic", "procedural"], "default": "episodic"},
                    "content":    {"type": "string"},
                    "query":      {"type": "string"},
                    "importance": {"type": "number", "default": 0.5}
                },
                "required": ["action"]
            }
        ),
        types.Tool(
            name="superai_stats",
            description="Get SuperAiAgent system status: skill count, KB size, memory tiers, Qdrant health.",
            inputSchema={"type": "object", "properties": {}, "required": []}
        ),
    ]


@app.call_tool()
async def call_tool(name: str, arguments: dict) -> list[types.TextContent]:
    try:
        if name == "superai_run":
            return await _handle_run(arguments)
        elif name == "superai_search":
            return await _handle_search(arguments)
        elif name == "superai_kb_query":
            return await _handle_kb_query(arguments)
        elif name == "superai_memory":
            return await _handle_memory(arguments)
        elif name == "superai_stats":
            return await _handle_stats()
        else:
            return [types.TextContent(type="text", text=f"Unknown tool: {name}")]
    except Exception as e:
        logger.error(f"Tool '{name}' error: {e}")
        return [types.TextContent(type="text", text=f"Error: {e}")]


async def _handle_run(args: dict) -> list[types.TextContent]:
    task    = args["task"]
    context = args.get("context", "")
    loop    = asyncio.get_event_loop()
    orch    = _get_orchestrator()
    result  = await loop.run_in_executor(None, lambda: orch.execute(task, context))
    text    = str(result.result) if hasattr(result, "result") else str(result)
    prefix  = "[OK]" if getattr(result, "success", True) else "[FAIL]"
    return [types.TextContent(type="text", text=f"{prefix} {text}")]


async def _handle_search(args: dict) -> list[types.TextContent]:
    query = args["query"]
    top_k = int(args.get("top_k", 5))
    loop  = asyncio.get_event_loop()

    def _search():
        cache = _build_skill_cache()
        if not cache or "vecs" not in cache:
            return "No skill vectors found. Run hermes_api.py first to build skill cache."
        enc   = _get_encoder()
        q_vec = enc.encode(query[:500], normalize_embeddings=True, show_progress_bar=False)
        sims  = (q_vec @ cache["vecs"].T).tolist()
        ranked = sorted(zip(sims, cache["names"], cache["descs"]), key=lambda x: x[0], reverse=True)[:top_k]
        lines  = [f"Top {top_k} skills for: '{query}'\n"]
        for score, name, desc in ranked:
            lines.append(f"  [{score:.2f}] {name}")
            if desc:
                lines.append(f"         {desc}")
        return "\n".join(lines)

    return [types.TextContent(type="text", text=await loop.run_in_executor(None, _search))]


async def _handle_kb_query(args: dict) -> list[types.TextContent]:
    query = args["query"]
    top_k = min(int(args.get("top_k", 5)), 10)
    loop  = asyncio.get_event_loop()

    def _query():
        client, encoder = _get_kb_client()
        q_vec   = encoder.encode(query[:500], normalize_embeddings=True, show_progress_bar=False).tolist()
        results = client.query_points(
            collection_name="gh_knowledge", query=q_vec, limit=top_k, with_payload=True
        ).points
        if not results:
            return "No KB results found."
        lines = [f"KB results for: '{query}'\n"]
        for i, r in enumerate(results, 1):
            p     = r.payload
            chunk = p.get("content", p.get("text", ""))[:400]
            lines.append(f"[{i}] score={r.score:.3f} | {p.get('repo_name','?')} | {p.get('file_path','?')}")
            if chunk:
                lines.append(f"    {chunk}\n")
        return "\n".join(lines)

    return [types.TextContent(type="text", text=await loop.run_in_executor(None, _query))]


async def _handle_memory(args: dict) -> list[types.TextContent]:
    action = args["action"]
    loop   = asyncio.get_event_loop()

    def _mem():
        from core.hermes_memory import store, retrieve, stats
        if action == "stats":
            s = stats()
            return f"Memory: episodic={s['episodic']} | semantic={s['semantic']} | procedural={s['procedural']}"
        elif action == "store":
            tier    = args.get("tier", "episodic")
            content = args.get("content", "")
            if not content:
                return "Error: content required"
            pid = store(tier, content, {"source": "mcp"}, importance=float(args.get("importance", 0.5)))
            return f"Stored to {tier} tier. id={pid}"
        elif action == "retrieve":
            tier  = args.get("tier", "episodic")
            query = args.get("query", "")
            if not query:
                return "Error: query required"
            results = retrieve(tier, query, k=5)
            if not results:
                return f"No results in {tier} for: '{query}'"
            lines = [f"Retrieved from {tier} ({len(results)} results):\n"]
            for r in results:
                lines.append(f"  [score={r['score']:.3f}] {r['content'][:200]}")
            return "\n".join(lines)
        return f"Unknown action: {action}"

    return [types.TextContent(type="text", text=await loop.run_in_executor(None, _mem))]


async def _handle_stats() -> list[types.TextContent]:
    loop = asyncio.get_event_loop()

    def _stats():
        lines = ["=== SuperAiAgent Stats ===\n"]
        from config.settings import SKILLS_DIR
        total = sum(1 for _ in SKILLS_DIR.rglob("SKILL.md")) if SKILLS_DIR.exists() else 0
        lines.append(f"Skills: {total} total")
        try:
            from qdrant_client import QdrantClient
            c = QdrantClient(url=QDRANT_URL)
            for col in c.get_collections().collections:
                info = c.get_collection(col.name)
                lines.append(f"  {col.name}: {info.points_count} points")
        except Exception as e:
            lines.append(f"Qdrant: unavailable ({e})")
        try:
            from core.hermes_memory import stats as mem_stats
            s = mem_stats()
            lines.append(f"Memory: episodic={s['episodic']} | semantic={s['semantic']} | procedural={s['procedural']}")
        except Exception as e:
            lines.append(f"Memory: unavailable ({e})")
        return "\n".join(lines)

    return [types.TextContent(type="text", text=await loop.run_in_executor(None, _stats))]


def _warmup():
    logger.info("Loading encoder...")
    _get_encoder()
    if SKILL_VECTORS_PATH.exists():
        _build_skill_cache()
        logger.info(f"Skill cache: {len(_skill_cache.get('names', []))} skills")
    try:
        client, _ = _get_kb_client()
        client.get_collections()
        logger.info("Qdrant ready.")
    except Exception as e:
        logger.warning(f"Qdrant warmup failed: {e}")


async def main():
    logger.info("SuperAiAgent MCP Server starting...")
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, _warmup)
    logger.info("Ready.")
    async with stdio_server() as (read_stream, write_stream):
        await app.run(read_stream, write_stream, app.create_initialization_options())


if __name__ == "__main__":
    asyncio.run(main())
