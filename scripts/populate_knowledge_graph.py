#!/usr/bin/env python3
"""Populate the shared knowledge graph from existing run output directories.

Walks output/*/ and extracts entities, relationships, role records,
decision records, and outcome records from nodes/*.json, diagnostics/*.json,
and metrics.json.

Usage:
    python3 scripts/populate_knowledge_graph.py [--db knowledge.db] [--output-dir output]
"""

import argparse
import json
import os
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from mycelium.knowledge_graph import KnowledgeGraph


def find_run_dirs(output_dir: str) -> list[Path]:
    """Find all run directories that contain nodes/."""
    runs = []
    output_path = Path(output_dir)
    if not output_path.exists():
        return runs
    for entry in sorted(output_path.iterdir()):
        if entry.is_dir() and (entry / "nodes").is_dir():
            runs.append(entry)
    return runs


def load_node_files(run_dir: Path) -> list[dict]:
    """Load all node JSON files from a run directory."""
    nodes_dir = run_dir / "nodes"
    nodes = []
    if not nodes_dir.exists():
        return nodes
    for f in sorted(nodes_dir.iterdir()):
        if f.suffix == ".json":
            try:
                with open(f) as fh:
                    nodes.append(json.load(fh))
            except (json.JSONDecodeError, IOError):
                pass
    return nodes


def load_diagnostic_files(run_dir: Path) -> dict:
    """Load all diagnostic JSON files, keyed by node_id prefix."""
    diag_dir = run_dir / "diagnostics"
    diags = {}
    if not diag_dir.exists():
        return diags
    for f in sorted(diag_dir.iterdir()):
        if f.suffix == ".json":
            try:
                with open(f) as fh:
                    d = json.load(fh)
                    diags[d.get("node_id", f.stem)] = d
            except (json.JSONDecodeError, IOError):
                pass
    return diags


def load_metrics(run_dir: Path) -> dict:
    """Load metrics.json from a run directory."""
    metrics_path = run_dir / "metrics.json"
    if metrics_path.exists():
        try:
            with open(metrics_path) as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            pass
    return {}


def corpus_from_metrics(metrics: dict) -> str:
    """Extract corpus name from metrics.json source field."""
    return metrics.get("source", "") or ""


def populate_entities_from_node(kg: KnowledgeGraph, run_id: str, node: dict,
                                corpus: str = None):
    """Extract entities and relationships from a node's observations."""
    for obs in node.get("observations", []):
        if not isinstance(obs, dict):
            continue
        # Get entity name from source
        source = obs.get("source", {})
        if isinstance(source, dict):
            entity_name = source.get("title") or source.get("doc_id")
            agency = source.get("agency", "")
        else:
            entity_name = None
            agency = ""

        if not entity_name:
            continue

        # Determine entity type
        obs_type = obs.get("observation_type", "pattern")
        if obs_type in ("dependency_risk", "single_point_of_failure"):
            entity_type = "risk_entity"
        else:
            entity_type = "item"

        # Build claim from available fields
        claim = obs.get("raw_evidence", "") or obs.get("what_i_saw", "")
        if not claim:
            continue

        confidence = obs.get("confidence", 0.5)
        if isinstance(confidence, dict):
            confidence = max(confidence.values()) if confidence else 0.5
        elif not isinstance(confidence, (int, float)):
            confidence = 0.5

        node_id = node.get("node_id", "")

        kg.add_observation(
            entity_name=entity_name,
            claim=claim[:2000],
            source_node_id=node_id,
            source_run_id=run_id,
            confidence=confidence,
            observation_type=obs_type,
            entity_type=entity_type,
            corpus=corpus,
        )

        # Add agency relationship
        if agency and agency != "unknown":
            kg.add_relationship(
                from_name=entity_name,
                to_name=agency,
                relationship_type="maintained_by",
                confidence=0.8,
                evidence=claim[:200],
                from_type=entity_type,
                to_type="person_or_org",
                corpus=corpus,
            )

        # Add potential connections
        for conn in obs.get("potential_connections", []):
            if isinstance(conn, str) and conn:
                kg.add_relationship(
                    from_name=entity_name,
                    to_name=conn,
                    relationship_type="related_to",
                    confidence=0.3,
                    evidence=claim[:200],
                    corpus=corpus,
                )


def populate_reasoning_from_node(kg: KnowledgeGraph, run_id: str, node: dict,
                                  diag: dict, metrics: dict):
    """Extract role, decision, and outcome records from node and diagnostic data."""
    node_id = node.get("node_id", "")
    if not node_id:
        return

    # Use diagnostic data if available (richer), fall back to node data
    role_name = diag.get("role") or node.get("role")
    role_bar = diag.get("role_bar") or node.get("role_bar", "")
    scope = diag.get("scope") or node.get("scope_description", "")
    purpose = diag.get("purpose", "")
    tree_position = diag.get("tree_position") or node.get("tree_position", "")
    depth = tree_position.count(".") if tree_position else 0
    budget_info = diag.get("budget", {})
    corpus = metrics.get("source", "")

    # Role record
    if role_name:
        kg.add_role_record(
            run_id=run_id,
            node_id=node_id,
            parent_id=node.get("parent_id"),
            role_name=role_name,
            mission=purpose,
            bar=role_bar,
            scope_description=scope,
            budget=budget_info.get("envelope") if isinstance(budget_info, dict) else node.get("cost"),
            corpus=corpus,
            tree_position=tree_position,
            depth=depth,
        )

    # Decision record: formation
    decision = diag.get("decision", "")
    children_count = node.get("child_directives_count", 0)
    if not decision:
        decision = "hired" if children_count > 0 else "investigated"
    kg.add_decision_record(
        run_id=run_id,
        node_id=node_id,
        decision_type="formation",
        outcome=decision,
        reasoning_summary=diag.get("decision_reasoning", "")[:500],
    )

    # Decision record: turn2 if present
    turn2 = diag.get("turn2_result") or node.get("turn2_result")
    if turn2 and isinstance(turn2, dict):
        kg.add_decision_record(
            run_id=run_id,
            node_id=node_id,
            decision_type="turn2",
            outcome=turn2.get("option_chosen", ""),
            reasoning_summary=str(turn2.get("option_reasoning", ""))[:500],
        )

    # Outcome record
    node_metrics = node.get("metrics", {})
    if not isinstance(node_metrics, dict):
        node_metrics = {}
    self_eval = diag.get("self_evaluation", {})
    if not isinstance(self_eval, dict):
        self_eval = {}

    obs_count = len(node.get("observations", []))
    budget_allocated = budget_info.get("envelope") if isinstance(budget_info, dict) else None
    budget_spent = budget_info.get("spent") if isinstance(budget_info, dict) else node.get("cost")

    # Try to extract turn2 classification from various sources
    turn2_class = (node_metrics.get("turn2_classification", "")
                   or self_eval.get("turn2_classification", ""))

    kg.add_outcome_record(
        run_id=run_id,
        node_id=node_id,
        observation_count=obs_count,
        budget_allocated=budget_allocated,
        budget_spent=budget_spent,
        turn2_classification=turn2_class,
        reader_test_scores=node_metrics.get("reader_test_scores", {}),
        validation_outcomes=node_metrics.get("validation_outcomes", {}),
    )


def backfill_corpus(kg: KnowledgeGraph, output_dir: str):
    """Backfill corpus column for existing data using metrics.json from each run."""
    run_dirs = find_run_dirs(output_dir)
    updated = 0
    for rd in run_dirs:
        run_id = rd.name
        metrics = load_metrics(rd)
        corpus = corpus_from_metrics(metrics)
        if not corpus:
            continue
        # Update observations by source_run_id
        cur = kg.conn.execute(
            "UPDATE observations SET corpus = ? WHERE source_run_id = ? AND (corpus IS NULL OR corpus = '')",
            (corpus, run_id))
        updated += cur.rowcount
        # Update entities by first_observed_run or last_observed_run
        kg.conn.execute(
            "UPDATE entities SET corpus = ? WHERE (first_observed_run = ? OR last_observed_run = ?) AND (corpus IS NULL OR corpus = '')",
            (corpus, run_id, run_id))
        # Update relationships via their entities: relationships whose from_entity
        # belongs to an entity with this corpus
        kg.conn.execute(
            """UPDATE relationships SET corpus = ? WHERE (corpus IS NULL OR corpus = '') AND (
               from_entity IN (SELECT id FROM entities WHERE (first_observed_run = ? OR last_observed_run = ?))
               OR to_entity IN (SELECT id FROM entities WHERE (first_observed_run = ? OR last_observed_run = ?))
            )""",
            (corpus, run_id, run_id, run_id, run_id))
    kg.conn.commit()
    print(f"  Backfill: updated {updated} observations with corpus tags")


def main():
    parser = argparse.ArgumentParser(description="Populate knowledge graph from existing runs")
    parser.add_argument("--db", default="knowledge.db", help="Path to knowledge.db")
    parser.add_argument("--output-dir", default="output", help="Output directory with run dirs")
    parser.add_argument("--dry-run", action="store_true", help="Count records without writing")
    args = parser.parse_args()

    run_dirs = find_run_dirs(args.output_dir)
    if not run_dirs:
        print(f"No run directories found in {args.output_dir}/")
        return

    print(f"Found {len(run_dirs)} run directories")

    if args.dry_run:
        total_nodes = 0
        total_diags = 0
        for rd in run_dirs:
            nodes = load_node_files(rd)
            diags = load_diagnostic_files(rd)
            total_nodes += len(nodes)
            total_diags += len(diags)
        print(f"Total nodes: {total_nodes}, diagnostics: {total_diags}")
        print("(dry run, no changes made)")
        return

    kg = KnowledgeGraph(args.db)
    existing = kg.stats()
    print(f"Existing graph: {existing['entities']} entities, "
          f"{existing['role_records']} role records, "
          f"{existing['outcome_records']} outcome records")

    total_nodes = 0
    total_roles = 0
    total_outcomes = 0

    for rd in run_dirs:
        run_id = rd.name
        nodes = load_node_files(rd)
        diags = load_diagnostic_files(rd)
        metrics = load_metrics(rd)

        if not nodes:
            continue

        corpus = corpus_from_metrics(metrics)
        for node in nodes:
            node_id = node.get("node_id", "")
            # Match diagnostic by node_id prefix (first 8 chars)
            diag = diags.get(node_id[:8], {})
            if not diag:
                # Try full node_id
                diag = diags.get(node_id, {})

            populate_entities_from_node(kg, run_id, node, corpus=corpus)
            populate_reasoning_from_node(kg, run_id, node, diag, metrics)
            total_nodes += 1

        node_count = len(nodes)
        diag_count = len(diags)
        print(f"  {run_id}: {node_count} nodes, {diag_count} diagnostics")

    # Backfill corpus for all existing data
    print("\nBackfilling corpus tags...")
    backfill_corpus(kg, args.output_dir)

    # Report corpus distribution
    for table in ("entities", "observations", "relationships"):
        rows = kg.conn.execute(
            f"SELECT corpus, COUNT(*) as cnt FROM {table} GROUP BY corpus ORDER BY cnt DESC"
        ).fetchall()
        print(f"  {table}: {', '.join(f'{r[0] or 'NULL'}={r[1]}' for r in rows)}")

    final = kg.stats()
    print(f"\nFinal graph: {final['entities']} entities, "
          f"{final['observations']} observations, "
          f"{final['relationships']} relationships")
    print(f"  {final['role_records']} role records, "
          f"{final['decision_records']} decision records, "
          f"{final['outcome_records']} outcome records")
    print(f"  Runs: {final['runs_represented']}")

    kg.close()
    print(f"\nKnowledge graph saved to {args.db}")


if __name__ == "__main__":
    main()
