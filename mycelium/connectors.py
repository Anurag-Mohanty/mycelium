"""Deliverable connectors — pluggable delivery of engagement results.

Registration pattern for delivering deliverable.db to different destinations.
Default: filesystem (already in run output dir).
MCP: generates a standalone MCP server stub alongside deliverable.db.
"""

import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)


class DeliverableConnector:
    """Base class for deliverable delivery."""

    def deliver(self, deliverable_db_path: str, destination_config: dict) -> dict:
        """Deliver the engagement results. Returns status dict."""
        raise NotImplementedError


class FilesystemConnector(DeliverableConnector):
    """Default — deliverable.db already in run output dir."""

    def deliver(self, deliverable_db_path: str, destination_config: dict) -> dict:
        path = Path(deliverable_db_path)
        if not path.exists():
            return {"status": "error", "message": f"deliverable.db not found at {path}"}
        size_kb = path.stat().st_size / 1024
        return {
            "status": "ok",
            "path": str(path),
            "size_kb": round(size_kb, 1),
        }


class MCPConnector(DeliverableConnector):
    """Generate MCP server stub alongside deliverable.db."""

    def deliver(self, deliverable_db_path: str, destination_config: dict) -> dict:
        db_path = Path(deliverable_db_path)
        if not db_path.exists():
            return {"status": "error", "message": f"deliverable.db not found at {db_path}"}

        mcp_path = db_path.parent / "mcp_server.py"
        mcp_path.write_text(_MCP_SERVER_TEMPLATE)
        logger.info(f"MCP server stub written to {mcp_path}")

        return {
            "status": "ok",
            "deliverable_path": str(db_path),
            "mcp_server_path": str(mcp_path),
            "usage": f"python {mcp_path}",
        }


# Registry
CONNECTORS = {
    "filesystem": FilesystemConnector,
    "mcp": MCPConnector,
}


def get_connector(name: str) -> DeliverableConnector:
    cls = CONNECTORS.get(name)
    if not cls:
        raise ValueError(f"Unknown connector: {name}. Available: {list(CONNECTORS.keys())}")
    return cls()


_MCP_SERVER_TEMPLATE = '''\
#!/usr/bin/env python3
"""MCP server for querying a Mycelium deliverable.db.

Standalone — works without the mycelium package installed.
Requires: mcp, sqlite3, json. Optionally: openai (for semantic queries).

Usage:
    python mcp_server.py

Connect to Claude Desktop/Code as an MCP server.
"""

import json
import os
import sqlite3
import struct
from pathlib import Path

from mcp.server.fastmcp import FastMCP

DB_PATH = str(Path(__file__).parent / "deliverable.db")

mcp = FastMCP("mycelium-deliverable")


@mcp.tool()
def query_structural(sql_query: str) -> str:
    """Run a SQL query against the deliverable database.

    Available tables: entities, observations, relationships, findings, engagement_metadata, vectors.
    Returns rows as JSON array.
    """
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(sql_query).fetchall()
        result = [dict(r) for r in rows]
        return json.dumps(result, indent=2, default=str)
    except Exception as e:
        return json.dumps({"error": str(e)})
    finally:
        conn.close()


@mcp.tool()
def query_semantic(query: str, k: int = 10) -> str:
    """Semantic search — embed query and return k nearest entities/observations.

    Requires OPENAI_API_KEY environment variable.
    Returns top-k results ranked by cosine similarity.
    """
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        return json.dumps({"error": "OPENAI_API_KEY not set — semantic search unavailable"})

    try:
        import openai
    except ImportError:
        return json.dumps({"error": "openai package not installed"})

    client = openai.OpenAI(api_key=api_key)
    try:
        response = client.embeddings.create(
            model="text-embedding-3-small",
            input=[query],
        )
    except Exception as e:
        return json.dumps({"error": f"Embedding failed: {e}"})

    query_vec = response.data[0].embedding

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    rows = conn.execute("SELECT id, source_table, source_id, text, embedding FROM vectors").fetchall()

    results = []
    for row in rows:
        blob = row["embedding"]
        if not blob:
            continue
        dim = len(blob) // 4
        stored_vec = struct.unpack(f"{dim}f", blob)
        # Cosine similarity
        dot = sum(a * b for a, b in zip(query_vec, stored_vec))
        norm_q = sum(a * a for a in query_vec) ** 0.5
        norm_s = sum(a * a for a in stored_vec) ** 0.5
        sim = dot / (norm_q * norm_s) if norm_q and norm_s else 0.0
        results.append({
            "source_table": row["source_table"],
            "source_id": row["source_id"],
            "text": row["text"][:500],
            "similarity": round(sim, 4),
        })

    conn.close()
    results.sort(key=lambda x: x["similarity"], reverse=True)
    return json.dumps(results[:k], indent=2)


if __name__ == "__main__":
    mcp.run()
'''
