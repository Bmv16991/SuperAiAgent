"""
SuperAiAgent MCP Server — Open Source Interface Layer
This is the PUBLIC skeleton. The private Foundation (Hermes KB + Skills) runs separately.

Connect Claude Code to SuperAiAgent:
  Add to .claude.json mcpServers:
  {
    "superaiagent": {
      "command": "python",
      "args": ["path/to/server.py"],
      "env": { "SUPERAIAGENT_URL": "http://your-host:8766", "SUPERAIAGENT_KEY": "your-key" }
    }
  }
"""
import os, sys, json, asyncio
import httpx
import mcp.types as types
from mcp.server import Server
from mcp.server.stdio import stdio_server

BASE_URL = os.environ.get("SUPERAIAGENT_URL", "http://localhost:8766")
API_KEY  = os.environ.get("SUPERAIAGENT_KEY",  "superai-dev-key")
HEADERS  = {"X-API-Key": API_KEY}

server = Server("superaiagent")


@server.list_tools()
async def list_tools():
    return [
        types.Tool(name="get_blueprint",     description="Get SuperAiAgent Blueprint and current build status",                  inputSchema={"type": "object", "properties": {}}),
        types.Tool(name="query_foundation",  description="Query the KB (24,850 chunks from 1,766 GitHub repos)",                 inputSchema={"type": "object", "properties": {"query": {"type": "string"}, "top_k": {"type": "integer", "default": 5}}, "required": ["query"]}),
        types.Tool(name="list_capabilities", description="List available domains and already spawned agents",                     inputSchema={"type": "object", "properties": {}}),
        types.Tool(name="spawn_agent",       description="Spawn a new domain agent (forex-trading, crypto-trading, fivem-scripting, ui-design)", inputSchema={"type": "object", "properties": {"domain": {"type": "string"}, "output_dir": {"type": "string"}}, "required": ["domain"]}),
    ]


@server.call_tool()
async def call_tool(name: str, arguments: dict):
    async with httpx.AsyncClient(timeout=60) as c:
        r = await c.post(
            f"{BASE_URL}/mcp",
            headers=HEADERS,
            json={"jsonrpc": "2.0", "id": "1", "method": "tools/call",
                  "params": {"name": name, "arguments": arguments}},
        )
        r.raise_for_status()
        data = r.json()
        result = data.get("result", data)
    return [types.TextContent(type="text", text=json.dumps(result, ensure_ascii=False, indent=2))]


async def main():
    async with stdio_server() as (r, w):
        await server.run(r, w, server.create_initialization_options())


if __name__ == "__main__":
    asyncio.run(main())
