#!/usr/bin/env python3
"""Validate knowledge graph LLM-planned queries.

Runs 4 demonstration queries through plan -> execute -> synthesize
and saves results to output/kg_validation/.

Usage:
    python3 scripts/validate_knowledge_graph.py [--db knowledge.db]
"""

import asyncio
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from mycelium.knowledge_graph import KnowledgeGraph

QUESTIONS = [
    "What packages have single-maintainer concentration risk in npm?",
    "Which maintainers control the most downloads across npm?",
    "What entities have been observed in the most runs?",
    "What findings reference the lodash package?",
]


async def run_validation(db_path: str):
    kg = KnowledgeGraph(db_path)
    out_dir = Path("output/kg_validation")
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"Knowledge graph: {kg.stats()['entities']} entities, "
          f"{kg.stats()['observations']} observations, "
          f"{kg.stats()['relationships']} relationships\n")

    for i, question in enumerate(QUESTIONS, 1):
        print(f"--- Query {i}: {question}")
        result = await kg.query_with_llm(question)

        print(f"  SQL: {result['sql']}")
        print(f"  Results: {len(result['results'])} rows")
        print(f"  Answer: {result['answer'][:200]}...")
        print()

        # Save to file
        out_file = out_dir / f"query_{i}.json"
        with open(out_file, "w") as f:
            json.dump(result, f, indent=2, default=str)
        print(f"  Saved to {out_file}")
        print()

    kg.close()
    print(f"\nAll results saved to {out_dir}/")


def main():
    parser = argparse.ArgumentParser(description="Validate KG LLM queries")
    parser.add_argument("--db", default="knowledge.db", help="Path to knowledge.db")
    args = parser.parse_args()
    asyncio.run(run_validation(args.db))


if __name__ == "__main__":
    main()
