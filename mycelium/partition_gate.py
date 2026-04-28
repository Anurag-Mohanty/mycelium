"""MECE Partition Gate — halt when child partitions don't tile the parent scope.

At every parent→child boundary, checks:
  1. Shape: can each child's partition be translated to SQL? (lens vs slice)
  2. Exclusivity: do any two children's record sets overlap?
  3. Completeness: does the union of children cover the parent's scope?

If any check fails and gate=on, the run halts with a diagnostic.
If gate=off, the diagnostic is still written but the run continues.
"""

import json
import sqlite3
import time
from itertools import combinations
from pathlib import Path

from .translator import translate_partition, _build_schema_context


async def check_mece(
    parent_partition: str | None,
    child_partitions: list[dict],
    data_source,
    run_dir: str,
    parent_node_id: str,
    parent_tree_pos: str,
) -> dict:
    """Check whether child partitions satisfy MECE against parent scope.

    Args:
        parent_partition: natural-language partition (None = whole catalog)
        child_partitions: list of {role_name, partition_desc, tree_position}
        data_source: corpus data source with catalog DB
        run_dir: output directory for diagnostics
        parent_node_id: for diagnostic logging
        parent_tree_pos: for diagnostic logging

    Returns:
        dict with verdict (PASS/FAIL), completeness, exclusivity, shape details
    """
    start = time.time()
    total_cost = 0.0

    # Ensure catalog DB is available
    data_source._ensure_catalog_db()
    db = data_source._catalog_db

    # --- Parent scope ---
    if parent_partition:
        parent_translation = await translate_partition(
            partition=parent_partition,
            data_source=data_source,
            max_records=1,  # we only need the SQL, not the records
            run_dir=run_dir,
            hire_id=f"gate_parent_{parent_node_id[:8]}",
        )
        total_cost += parent_translation.cost
        if parent_translation.success:
            parent_sql = _strip_limit(parent_translation.sql)
            parent_rowids = _get_rowids(db, parent_sql)
        else:
            # Can't determine parent scope — can't check MECE
            parent_rowids = _get_all_rowids(db)
    else:
        parent_rowids = _get_all_rowids(db)

    parent_set = set(parent_rowids)
    parent_scope_size = len(parent_set)

    # --- Translate each child partition ---
    children_info = []
    child_sets = []

    for cp in child_partitions:
        desc = cp["partition_desc"]
        role = cp["role_name"]
        pos = cp["tree_position"]

        if not desc or not desc.strip():
            children_info.append({
                "role_name": role,
                "partition_desc": desc or "",
                "tree_position": pos,
                "translation_success": False,
                "sql": None,
                "full_record_count": 0,
                "shape_check": "FAIL_EMPTY",
            })
            child_sets.append(set())
            continue

        translation = await translate_partition(
            partition=desc,
            data_source=data_source,
            max_records=1,  # we only need SQL
            run_dir=run_dir,
            hire_id=f"gate_{pos}_{parent_node_id[:8]}",
        )
        total_cost += translation.cost

        if not translation.success:
            children_info.append({
                "role_name": role,
                "partition_desc": desc,
                "tree_position": pos,
                "translation_success": False,
                "sql": None,
                "full_record_count": 0,
                "shape_check": "FAIL_LENS",
            })
            child_sets.append(set())
            continue

        # Get FULL rowid set (no LIMIT)
        child_sql = _strip_limit(translation.sql)
        try:
            rowids = _get_rowids(db, child_sql)
        except sqlite3.OperationalError:
            children_info.append({
                "role_name": role,
                "partition_desc": desc,
                "tree_position": pos,
                "translation_success": False,
                "sql": translation.sql,
                "full_record_count": 0,
                "shape_check": "FAIL_SQL_ERROR",
            })
            child_sets.append(set())
            continue

        child_set = set(rowids)
        child_sets.append(child_set)
        children_info.append({
            "role_name": role,
            "partition_desc": desc,
            "tree_position": pos,
            "translation_success": True,
            "sql": translation.sql,
            "full_record_count": len(child_set),
            "shape_check": "PASS",
            "drift_check": _check_drift(desc, translation.sql),
        })

    # --- Shape check ---
    shape_failures = [
        {"role_name": c["role_name"], "reason": c["shape_check"]}
        for c in children_info if c["shape_check"] != "PASS"
    ]
    shape_pass = len(shape_failures) == 0

    # --- Exclusivity check ---
    overlapping_pairs = []
    for (i, ci), (j, cj) in combinations(enumerate(children_info), 2):
        overlap = child_sets[i] & child_sets[j]
        if overlap:
            overlapping_pairs.append({
                "child_a": ci["role_name"],
                "child_b": cj["role_name"],
                "overlap_count": len(overlap),
                "overlap_examples": _rowid_examples(db, list(overlap)[:5]),
            })
    exclusivity_pass = len(overlapping_pairs) == 0

    # --- Completeness check ---
    union_set = set()
    for s in child_sets:
        union_set |= s

    uncovered = parent_set - union_set
    coverage_pct = (len(union_set & parent_set) / parent_scope_size * 100) if parent_scope_size > 0 else 0.0
    completeness_pass = len(uncovered) == 0

    uncovered_examples = _rowid_examples(db, list(uncovered)[:10]) if uncovered else []

    # --- Verdict ---
    failure_reasons = []
    if not shape_pass:
        failure_reasons.append("shape")
    if not exclusivity_pass:
        failure_reasons.append("exclusivity")
    if not completeness_pass:
        failure_reasons.append("completeness")

    verdict = "PASS" if not failure_reasons else "FAIL"

    result = {
        "parent_node_id": parent_node_id[:8],
        "parent_tree_position": parent_tree_pos,
        "parent_partition": parent_partition,
        "parent_scope_size": parent_scope_size,
        "children": children_info,
        "completeness": {
            "pass": completeness_pass,
            "union_size": len(union_set & parent_set),
            "parent_scope_size": parent_scope_size,
            "coverage_pct": round(coverage_pct, 2),
            "uncovered_count": len(uncovered),
            "uncovered_examples": uncovered_examples,
        },
        "exclusivity": {
            "pass": exclusivity_pass,
            "overlapping_pairs": overlapping_pairs,
        },
        "shape": {
            "pass": shape_pass,
            "failures": shape_failures,
        },
        "verdict": verdict,
        "failure_reasons": failure_reasons,
        "cost": round(total_cost, 4),
        "elapsed_seconds": round(time.time() - start, 2),
    }

    # Write diagnostic
    if run_dir:
        diag_dir = Path(run_dir) / "diagnostics" / "partition_gate"
        diag_dir.mkdir(parents=True, exist_ok=True)
        diag_path = diag_dir / f"{parent_node_id[:8]}.json"
        with open(diag_path, "w") as f:
            json.dump(result, f, indent=2, default=str)

    return result


def _strip_limit(sql: str) -> str:
    """Remove LIMIT clause from SQL so we get the full result set."""
    import re
    return re.sub(r'\s+LIMIT\s+\d+', '', sql, flags=re.IGNORECASE).rstrip("; \n")


def _get_all_rowids(db) -> list[int]:
    """Get all rowids from the records table."""
    return [r[0] for r in db.execute("SELECT rowid FROM records").fetchall()]


def _get_rowids(db, sql: str) -> list[int]:
    """Get rowids matching a SQL query.

    Replaces SELECT * with SELECT rowid to avoid materializing all columns
    in the subquery (critical for performance on 100K+ row catalogs).
    """
    import re
    # Replace SELECT * or SELECT col1, col2, ... FROM with SELECT rowid FROM
    rowid_sql = re.sub(
        r'^\s*SELECT\s+.+?\s+FROM\s+',
        'SELECT rowid FROM ',
        sql,
        count=1,
        flags=re.IGNORECASE | re.DOTALL,
    )
    return [r[0] for r in db.execute(rowid_sql).fetchall()]


def _rowid_examples(db, rowids: list[int], n: int = 5) -> list[str]:
    """Fetch human-readable identifiers for a sample of rowids."""
    if not rowids:
        return []
    sample = rowids[:n]
    placeholders = ",".join("?" for _ in sample)
    # Try 'name' column first, fall back to first text column
    try:
        rows = db.execute(
            f"SELECT name FROM records WHERE rowid IN ({placeholders})", sample
        ).fetchall()
        return [r[0] for r in rows]
    except sqlite3.OperationalError:
        # No 'name' column — try first column
        try:
            rows = db.execute(
                f"SELECT * FROM records WHERE rowid IN ({placeholders}) LIMIT {n}", sample
            ).fetchall()
            return [str(dict(r).get(list(dict(r).keys())[0], r[0])) for r in rows]
        except Exception:
            return [str(rid) for rid in sample]


def _check_drift(description: str, sql: str) -> dict:
    """Check whether the SQL's numeric values match the description's.

    Extracts numbers from both strings and flags mismatches. Not perfect
    (natural language is ambiguous) but catches obvious value rewrites
    like 'dependency_count >= 5' translating to 'dependency_count >= 3'.
    """
    import re

    # Extract all numbers from both strings
    desc_nums = set(re.findall(r'\b\d+\b', description))
    sql_nums = set(re.findall(r'\b\d+\b', sql))

    # Numbers in the description that don't appear in the SQL
    desc_only = desc_nums - sql_nums
    # Numbers in the SQL that don't appear in the description
    sql_only = sql_nums - desc_only - desc_nums  # truly novel in SQL

    # Filter out trivially different numbers (e.g., LIMIT values)
    sql_only = {n for n in (sql_nums - desc_nums) if n not in ("0", "1")}

    if not desc_only and not sql_only:
        return {"drift": False}

    return {
        "drift": True,
        "description_values": sorted(desc_only),
        "sql_values": sorted(sql_only),
        "note": "Numeric values in description don't match SQL — possible translation drift",
    }
