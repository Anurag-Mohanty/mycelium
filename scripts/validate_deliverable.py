#!/usr/bin/env python3
"""Validate deliverable generation against completed runs.

Usage:
    python3 scripts/validate_deliverable.py
"""

import ast
import json
import os
import sqlite3
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from mycelium.deliverable import generate_deliverable, query_semantic
from mycelium.connectors import MCPConnector


RUNS = ["7e4f0a81", "75e170e3", "c8bd3b12", "bebc7c8e"]
OUTPUT_DIR = Path(__file__).parent.parent / "output"


def validate_run(run_id: str) -> bool:
    run_dir = OUTPUT_DIR / run_id
    if not run_dir.exists():
        print(f"  SKIP {run_id} — directory not found")
        return True

    print(f"\n{'='*60}")
    print(f"  Run: {run_id}")
    print(f"{'='*60}")

    # 1. Generate deliverable
    db_path = generate_deliverable(str(run_dir), run_id)
    assert Path(db_path).exists(), "deliverable.db not created"
    print(f"  Generated: {db_path}")

    # 2. Check counts
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    counts = {}
    for table in ["entities", "observations", "relationships", "findings", "engagement_metadata"]:
        count = conn.execute(f"SELECT COUNT(*) as c FROM {table}").fetchone()["c"]
        counts[table] = count
    print(f"  Entities: {counts['entities']}")
    print(f"  Observations: {counts['observations']}")
    print(f"  Relationships: {counts['relationships']}")
    print(f"  Findings: {counts['findings']}")
    print(f"  Metadata rows: {counts['engagement_metadata']}")

    assert counts["entities"] > 0, "No entities"
    assert counts["observations"] > 0, "No observations"
    assert counts["engagement_metadata"] == 1, "Metadata should have exactly 1 row"

    # Verify metadata fields
    meta = dict(conn.execute("SELECT * FROM engagement_metadata").fetchone())
    print(f"  Corpus: {meta['corpus']}")
    print(f"  Budget: ${meta['total_budget']} spent: ${meta['budget_spent']}")
    print(f"  Nodes: {meta['node_count']}, Observations: {meta['observation_count']}")

    # 3. Check vector table exists (even if empty without embeddings)
    vec_count = conn.execute("SELECT COUNT(*) as c FROM vectors").fetchone()["c"]
    print(f"  Vectors: {vec_count}")

    conn.close()

    # 4. Test semantic query if OPENAI_API_KEY available
    if os.environ.get("OPENAI_API_KEY"):
        print("  Testing semantic query...")
        results = query_semantic(db_path, "security vulnerability", k=5)
        print(f"  Semantic results: {len(results)}")
        if results:
            print(f"    Top match: {results[0]['text'][:80]}... (sim={results[0]['similarity']:.3f})")
    else:
        print("  Semantic query: skipped (no OPENAI_API_KEY)")

    # 5. Generate MCP server stub and verify syntax
    mcp_connector = MCPConnector()
    result = mcp_connector.deliver(db_path, {})
    assert result["status"] == "ok", f"MCP connector failed: {result}"
    mcp_path = result["mcp_server_path"]
    assert Path(mcp_path).exists(), "MCP server stub not created"

    # Verify it's valid Python
    with open(mcp_path) as f:
        source = f.read()
    try:
        ast.parse(source)
        print(f"  MCP server: valid Python ({mcp_path})")
    except SyntaxError as e:
        print(f"  MCP server: SYNTAX ERROR — {e}")
        return False

    print(f"  PASS")
    return True


def main():
    print("Validating deliverable generation")
    all_passed = True
    for run_id in RUNS:
        try:
            if not validate_run(run_id):
                all_passed = False
        except Exception as e:
            print(f"  FAIL {run_id}: {e}")
            all_passed = False

    print(f"\n{'='*60}")
    if all_passed:
        print("  All validations passed")
    else:
        print("  Some validations FAILED")
        sys.exit(1)


if __name__ == "__main__":
    main()
