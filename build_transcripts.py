"""Build reasoning transcripts and dashboards for all runs.

Reads existing output/ data. Does NOT run any exploration or make API calls.
Produces:
  - output/{run_id}/transcripts/{node_id}.md  (per-node transcript)
  - output/{run_id}/dashboard.md              (run-level dashboard)
  - output/INDEX.md                           (cross-run index)

Usage:
  python3 build_transcripts.py              # all runs
  python3 build_transcripts.py <run_id>     # single run
"""

import json
import glob
import os
import sys
from pathlib import Path
from collections import Counter
from datetime import datetime


def load_json(path):
    try:
        with open(path) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def load_jsonl(path):
    try:
        with open(path) as f:
            return [json.loads(line) for line in f if line.strip()]
    except FileNotFoundError:
        return []


# ─── Per-node transcript ────────────────────────────────────

def build_node_transcript(run_id, node_data, diag_data, events, tree_data):
    """Build a markdown transcript for one node."""
    lines = []
    node_id = node_data.get("node_id", diag_data.get("node_id", "unknown"))

    # 1. Identity
    lines.append(f"# Node Transcript: {node_id[:8]}")
    lines.append("")
    lines.append(f"**Run:** {run_id}")
    lines.append(f"**Node ID:** {node_id}")
    pos = diag_data.get("tree_position", "unknown")
    lines.append(f"**Tree Position:** {pos}")
    lines.append(f"**Parent:** {node_data.get('parent_id', 'none') or 'root'}")
    lines.append(f"**Depth:** {pos.count('.') if pos != 'ROOT' and pos != 'unknown' else 0}")

    budget = diag_data.get("budget", {})
    lines.append(f"**Budget:** ${budget.get('allocated', '?'):.3f} allocated, "
                 f"${budget.get('spent', node_data.get('cost', 0)):.3f} spent, "
                 f"${budget.get('returned', '?'):.3f} returned"
                 if isinstance(budget.get('allocated'), (int, float))
                 else f"**Cost:** ${node_data.get('cost', 0):.3f}")
    lines.append("")

    # 2. Inputs
    lines.append("## Inputs")
    lines.append("")

    # Scope
    scope = diag_data.get("scope", node_data.get("scope_description", ""))
    lines.append("### Scope")
    lines.append(scope or "[not recorded]")
    lines.append("")

    # Purpose
    purpose = diag_data.get("purpose", "")
    lines.append("### Purpose")
    lines.append(purpose or "[not recorded in this run's diagnostics]")
    lines.append("")

    # Data received
    data_info = diag_data.get("data_received", {})
    lines.append("### Data Received")
    if data_info:
        lines.append(f"- Records: {data_info.get('record_count', '?')}")
        fields = data_info.get("fields_present", [])
        if fields:
            lines.append(f"- Fields: {', '.join(fields[:20])}")
        lines.append(f"- Avg text length: {data_info.get('avg_text_length', '?')}")
        sample = data_info.get("sample_record_summary", "")
        if sample:
            lines.append(f"- Sample: `{sample[:200]}`")
    else:
        lines.append("[not recorded in this run's diagnostics]")
    lines.append("")

    # Anomaly targets
    targets = diag_data.get("anomaly_targets_received", {})
    lines.append("### Anomaly Targets")
    tc = targets.get("count", 0)
    if tc > 0:
        te = sum(1 for t in targets.get("targets", []) if t.get("has_evidence"))
        lines.append(f"**{tc} targets received ({te} with evidence)**")
        lines.append("")
        for i, t in enumerate(targets.get("targets", []), 1):
            ev_marker = " **[+evidence]**" if t.get("has_evidence") else ""
            lines.append(f"{i}. [{t.get('type', '?')}] {t.get('description', '')[:200]}{ev_marker}")
            if t.get("evidence_keys"):
                lines.append(f"   Evidence keys: {', '.join(t['evidence_keys'])}")
    elif tc == 0 and diag_data:
        lines.append("No anomaly targets received.")
    else:
        lines.append("[not recorded in this run's diagnostics]")
    lines.append("")

    # Filter schema
    lines.append("### Filter Schema")
    lines.append("[filter schema shown in prompt — not separately recorded per node]")
    lines.append("")

    # Parent context
    lines.append("### Parent Context")
    lines.append("[parent context embedded in prompt — see thinking below for full context]")
    lines.append("")

    # 3. Reasoning
    lines.append("## Reasoning")
    lines.append("")
    thinking = node_data.get("thinking", "")
    if thinking:
        lines.append("### Extended Thinking")
        lines.append("")
        # Preserve full thinking — only truncate if enormous
        if len(thinking) > 50000:
            lines.append(thinking[:50000])
            lines.append(f"\n[TRUNCATED — {len(thinking)} chars total, showing first 50000]")
        else:
            lines.append(thinking)
    else:
        thinking_summary = diag_data.get("thinking_summary", "")
        if thinking_summary:
            lines.append("### Thinking Summary (full thinking not recorded)")
            lines.append("")
            lines.append(thinking_summary)
        else:
            lines.append("[thinking not recorded in this run]")
    lines.append("")

    # 4. Output
    lines.append("## Output")
    lines.append("")

    decision = diag_data.get("decision", "unknown")
    children_count = diag_data.get("output", {}).get("children_spawned",
                                                      node_data.get("child_directives_count", 0))
    lines.append(f"**Decision:** {decision}")
    lines.append(f"**Children spawned:** {children_count}")
    lines.append("")

    # Observations
    observations = node_data.get("observations", [])
    lines.append(f"### Observations ({len(observations)})")
    lines.append("")
    if observations:
        for i, obs in enumerate(observations, 1):
            # Handle both old (what_i_saw) and new (raw_evidence) formats
            evidence = obs.get("raw_evidence", obs.get("what_i_saw", ""))
            grounding = obs.get("statistical_grounding", "")
            hypothesis = obs.get("local_hypothesis", obs.get("reasoning", ""))
            surprising = obs.get("surprising_because", "")
            obs_type = obs.get("observation_type", "?")
            confidence = obs.get("confidence", "")
            src = obs.get("source", {})
            if isinstance(src, dict):
                src_id = src.get("doc_id", src.get("title", "?"))
                src_date = src.get("date", "")
            else:
                src_id = str(src)[:50]
                src_date = ""

            lines.append(f"#### Observation {i}: [{obs_type}]")
            lines.append(f"**Source:** {src_id}" + (f" ({src_date})" if src_date else ""))
            if confidence:
                lines.append(f"**Confidence:** {confidence}")
            lines.append("")
            lines.append(f"**Evidence:** {evidence}")
            if grounding:
                lines.append(f"\n**Statistical Grounding:** {grounding}")
            if hypothesis:
                lines.append(f"\n**Hypothesis:** {hypothesis}")
            if surprising:
                lines.append(f"\n**Surprising because:** {surprising}")
            lines.append("")
    else:
        lines.append("No observations produced.")
        fetch_result = diag_data.get("fetch_result", diag_data.get("data_received", {}))
        if fetch_result and fetch_result.get("record_count", -1) == 0:
            lines.append(f"\nFetch returned 0 records. Filter: `{fetch_result.get('filter_used', '?')}`")
    lines.append("")

    # Self-evaluation
    se = diag_data.get("self_evaluation", {})
    lines.append("### Self-Evaluation")
    if se and any(v is not None for v in se.values()):
        lines.append(f"- Purpose addressed: {se.get('purpose_addressed', '?')}")
        lines.append(f"- Evidence quality: {se.get('evidence_quality', '?')}")
        gap = se.get("purpose_gap", "")
        if gap:
            lines.append(f"- Gap: {gap}")
    else:
        lines.append("[not recorded in this run's diagnostics]")
    lines.append("")

    # Unresolved
    unresolved = node_data.get("unresolved", [])
    if unresolved:
        lines.append("### Unresolved Threads")
        for u in unresolved:
            lines.append(f"- {u}")
        lines.append("")

    # 5. Downstream effects
    lines.append("## Downstream Effects")
    lines.append("")

    # Find children in events
    children_spawned = [e for e in events
                        if e.get("type") == "node_spawned" and e.get("parent_id") == node_id]
    if children_spawned:
        lines.append("### Children Spawned")
        for c in children_spawned:
            lines.append(f"- [{c.get('tree_position', '?')}] {c.get('node_id', '?')[:8]} — "
                         f"{c.get('scope_summary', '')[:80]}")
        lines.append("")

    # Check if observations survived synthesis/validation (from tree data)
    lines.append("[downstream citation tracking not yet implemented]")
    lines.append("")

    return "\n".join(lines)


# ─── Run-level dashboard ────────────────────────────────────

def build_dashboard(run_id, run_dir):
    """Build dashboard.md for a run."""
    tree = load_json(run_dir / "tree.json")
    metrics = load_json(run_dir / "metrics.json")
    report_exists = (run_dir / "report.md").exists()
    events = load_jsonl(run_dir / "events.jsonl")
    node_files = sorted(glob.glob(str(run_dir / "nodes" / "*.json")))
    diag_files = sorted(glob.glob(str(run_dir / "diagnostics" / "*.json")))

    lines = []
    lines.append(f"# Dashboard: Run {run_id}")
    lines.append("")

    # Metadata
    lines.append("## Run Metadata")
    lines.append("")
    if metrics:
        lines.append(f"- **Source:** {metrics.get('source', '?')}")
        lines.append(f"- **Timestamp:** {metrics.get('timestamp', '?')}")
        lines.append(f"- **Budget:** ${metrics.get('cost', {}).get('budget_authorized', '?')}")
        lines.append(f"- **Total Cost:** ${metrics.get('cost', {}).get('total', '?')}")
    elif tree:
        stats = tree.get("stats", {})
        lines.append(f"- **Budget:** ${stats.get('budget', '?')}")
        lines.append(f"- **Total Cost:** ${stats.get('total_cost', '?')}")
        lines.append(f"- **Elapsed:** {stats.get('elapsed_seconds', '?')}s")

    source_events = [e for e in events if e.get("type") == "source_info"]
    if source_events:
        lines.append(f"- **Data Source:** {source_events[0].get('source_name', '?')}")
    lines.append(f"- **Report:** {'[report.md](report.md)' if report_exists else 'not generated'}")
    lines.append("")

    # Tree topology
    lines.append("## Tree Topology")
    lines.append("")
    stats = (tree or {}).get("stats", {})
    total_nodes = len(node_files) or stats.get("nodes_spawned", 0)
    max_depth = stats.get("max_depth_reached", 0)
    nodes_resolved = stats.get("nodes_resolved", 0)
    obs_collected = stats.get("observations_collected", 0)

    lines.append(f"- **Nodes:** {total_nodes}")
    lines.append(f"- **Max Depth:** {max_depth}")
    lines.append(f"- **Resolved:** {nodes_resolved}")
    lines.append(f"- **Observations:** {obs_collected}")
    lines.append(f"- **Avg Branching Factor:** {stats.get('avg_branching_factor', '?')}")
    lines.append("")

    # Diagnostic aggregates (if available)
    if diag_files:
        diags = [load_json(f) for f in diag_files]
        diags = [d for d in diags if d]
        zero_obs = sum(1 for d in diags if d.get("output", {}).get("observations_count", 0) == 0)
        with_targets = sum(1 for d in diags if d.get("anomaly_targets_received", {}).get("count", 0) > 0)
        with_evidence = sum(1 for d in diags if any(
            t.get("has_evidence") for t in d.get("anomaly_targets_received", {}).get("targets", [])))
        decomposed = sum(1 for d in diags if d.get("decision") == "decomposed")
        gaps = sum(1 for d in diags if not d.get("self_evaluation", {}).get("purpose_addressed", True))

        lines.append("## Node Diagnostics")
        lines.append("")
        lines.append(f"- **Zero-observation nodes:** {zero_obs}/{len(diags)} ({zero_obs*100//max(1,len(diags))}%)")
        lines.append(f"- **Received anomaly targets:** {with_targets}/{len(diags)}")
        lines.append(f"- **Targets with evidence:** {with_evidence}/{len(diags)}")
        lines.append(f"- **Decomposed:** {decomposed}/{len(diags)}")
        lines.append(f"- **Self-eval gaps:** {gaps}/{len(diags)}")
        lines.append("")

    # Cost breakdown
    if metrics:
        lines.append("## Cost Breakdown")
        lines.append("")
        cost = metrics.get("cost", {})
        by_phase = cost.get("by_phase", {})
        for phase, amount in sorted(by_phase.items(), key=lambda x: -x[1]):
            if amount > 0:
                lines.append(f"- {phase}: ${amount:.3f}")
        lines.append(f"- **Per observation:** ${cost.get('per_observation', '?')}")
        lines.append(f"- **Per validated finding:** {cost.get('per_validated_finding', 'N/A')}")
        lines.append("")
    elif stats.get("phase_costs"):
        lines.append("## Cost Breakdown")
        lines.append("")
        for phase, amount in stats["phase_costs"].items():
            if amount > 0:
                lines.append(f"- {phase}: ${amount:.3f}")
        lines.append("")

    # Validation
    if tree:
        validations = tree.get("validations", [])
        if validations:
            lines.append("## Validation Outcomes")
            lines.append("")
            for v in validations:
                verdict = v.get("verdict", "?")
                finding = str(v.get("original_finding", "?"))[:100]
                lines.append(f"- **{verdict.upper()}**: {finding}")
            lines.append("")

    # Transcript index
    transcript_dir = run_dir / "transcripts"
    if transcript_dir.exists():
        transcript_files = sorted(transcript_dir.glob("*.md"))
        if transcript_files:
            lines.append("## Transcript Index")
            lines.append("")
            lines.append("| Position | Node | Obs | Decision | Scope |")
            lines.append("|----------|------|-----|----------|-------|")

            # Build index from diagnostics if available, else from node files
            if diag_files:
                diags_sorted = sorted(
                    [load_json(f) for f in diag_files],
                    key=lambda d: d.get("tree_position", "") if d else ""
                )
                for d in diags_sorted:
                    if not d:
                        continue
                    nid = d.get("node_id", "?")[:8]
                    pos = d.get("tree_position", "?")
                    obs_count = d.get("output", {}).get("observations_count", 0)
                    decision = d.get("decision", "?")
                    scope = d.get("scope", "")[:50]
                    lines.append(f"| {pos} | [{nid}](transcripts/{nid}.md) | {obs_count} | {decision} | {scope} |")
            else:
                for tf in transcript_files:
                    nid = tf.stem
                    lines.append(f"| ? | [{nid}](transcripts/{nid}.md) | ? | ? | ? |")
            lines.append("")

    # Notable nodes
    if diag_files:
        diags = [load_json(f) for f in diag_files]
        diags = [d for d in diags if d]

        lines.append("## Notable Nodes")
        lines.append("")

        # Highest obs
        by_obs = sorted(diags, key=lambda d: d.get("output", {}).get("observations_count", 0), reverse=True)
        lines.append("### Highest Observation Count")
        for d in by_obs[:3]:
            nid = d.get("node_id", "?")[:8]
            obs = d.get("output", {}).get("observations_count", 0)
            lines.append(f"- [{nid}](transcripts/{nid}.md) — {obs} observations at position {d.get('tree_position', '?')}")
        lines.append("")

        # Zero obs
        zeros = [d for d in diags if d.get("output", {}).get("observations_count", 0) == 0]
        if zeros:
            lines.append("### Zero-Observation Nodes")
            for d in zeros[:3]:
                nid = d.get("node_id", "?")[:8]
                rec = d.get("data_received", {}).get("record_count", "?")
                lines.append(f"- [{nid}](transcripts/{nid}.md) — {rec} records received, position {d.get('tree_position', '?')}")
            if len(zeros) > 3:
                lines.append(f"- ... and {len(zeros) - 3} more")
            lines.append("")

        # Biggest gaps
        gapped = [d for d in diags if d.get("self_evaluation", {}).get("purpose_gap")]
        if gapped:
            lines.append("### Largest Self-Eval Gaps")
            for d in gapped[:3]:
                nid = d.get("node_id", "?")[:8]
                gap = d.get("self_evaluation", {}).get("purpose_gap", "")[:100]
                lines.append(f"- [{nid}](transcripts/{nid}.md) — {gap}")
            lines.append("")

    # Data coverage
    lines.append("## Data Coverage")
    lines.append("")
    catalog_events = [e for e in events if e.get("type") == "catalog_complete"]
    if catalog_events:
        ce = catalog_events[0]
        lines.append(f"- Records cataloged: {ce.get('total_records', '?')}")
        lines.append(f"- Anomaly clusters: {ce.get('anomaly_clusters', '?')}")
        lines.append(f"- Outliers: {ce.get('outliers', '?')}")
    elif metrics:
        dc = metrics.get("data_coverage", {})
        lines.append(f"- Records enriched: {dc.get('records_enriched', '?')}")
    else:
        lines.append("[catalog data not available for this run]")
    lines.append("")

    return "\n".join(lines)


# ─── Process one run ────────────────────────────────────────

def process_run(run_id, output_dir="output"):
    run_dir = Path(output_dir) / run_id
    if not run_dir.is_dir():
        return None

    node_files = sorted(glob.glob(str(run_dir / "nodes" / "*.json")))
    diag_files = sorted(glob.glob(str(run_dir / "diagnostics" / "*.json")))
    events = load_jsonl(run_dir / "events.jsonl")
    tree = load_json(run_dir / "tree.json")

    if not node_files and not diag_files:
        # No node data — can't produce transcripts
        return {"run_id": run_id, "transcripts": 0, "coverage": "no node data"}

    # Build node lookup
    nodes = {}
    for nf in node_files:
        data = load_json(nf)
        if data:
            nodes[data.get("node_id", "")] = data

    diags = {}
    for df in diag_files:
        data = load_json(df)
        if data:
            diags[data.get("node_id", "")] = data

    # Also pull nodes from tree.json if node files are sparse
    if tree:
        for nr in tree.get("node_results", []):
            nid = nr.get("node_id", "")
            if nid and nid not in nodes:
                nodes[nid] = nr

    # Create transcript directory
    transcript_dir = run_dir / "transcripts"
    transcript_dir.mkdir(exist_ok=True)

    transcript_count = 0
    all_node_ids = set(nodes.keys()) | set(diags.keys())

    for node_id in all_node_ids:
        node_data = nodes.get(node_id, {})
        diag_data = diags.get(node_id, {})

        if not node_data and not diag_data:
            continue

        # Merge: use node_data as base, fill from diag_data
        if not node_data.get("node_id"):
            node_data["node_id"] = node_id
        if not diag_data.get("node_id"):
            diag_data["node_id"] = node_id

        transcript = build_node_transcript(run_id, node_data, diag_data, events, tree)

        short_id = node_id[:8]
        with open(transcript_dir / f"{short_id}.md", "w") as f:
            f.write(transcript)
        transcript_count += 1

    # Build dashboard
    dashboard = build_dashboard(run_id, run_dir)
    with open(run_dir / "dashboard.md", "w") as f:
        f.write(dashboard)

    # Determine coverage
    has_diags = len(diag_files) > 0
    has_thinking = any(nodes[n].get("thinking") for n in nodes if nodes[n].get("thinking"))
    coverage_parts = []
    if has_diags:
        coverage_parts.append("diagnostics")
    if has_thinking:
        coverage_parts.append("thinking")
    coverage_parts.append("observations")
    coverage = ", ".join(coverage_parts)

    return {
        "run_id": run_id,
        "transcripts": transcript_count,
        "coverage": coverage,
    }


# ─── Cross-run index ────────────────────────────────────────

def build_index(output_dir="output"):
    runs = sorted([d for d in os.listdir(output_dir)
                   if os.path.isdir(os.path.join(output_dir, d)) and d != "transcripts"],
                  reverse=True)

    lines = []
    lines.append("# Mycelium Run Index")
    lines.append("")
    lines.append(f"*Generated {datetime.now().strftime('%Y-%m-%d %H:%M')}*")
    lines.append("")
    lines.append("| Run ID | Date | Source | Budget | Cost | Nodes | Obs | Validated | Dashboard | Coverage |")
    lines.append("|--------|------|--------|--------|------|-------|-----|-----------|-----------|----------|")

    for run_id in runs:
        run_dir = Path(output_dir) / run_id
        metrics = load_json(run_dir / "metrics.json")
        tree = load_json(run_dir / "tree.json")
        has_dashboard = (run_dir / "dashboard.md").exists()
        has_transcripts = (run_dir / "transcripts").exists()

        # Extract metadata
        date = "?"
        source = "?"
        budget = "?"
        cost = "?"
        nodes = "?"
        obs = "?"
        validated = "?"

        if metrics:
            date = metrics.get("timestamp", "?")[:10]
            source = metrics.get("source", "?")
            budget = f"${metrics.get('cost', {}).get('budget_authorized', '?')}"
            cost = f"${metrics.get('cost', {}).get('total', '?')}"
            nodes = metrics.get("efficiency", {}).get("nodes_spawned", "?")
            obs = metrics.get("quality", {}).get("total_observations", "?")
            confirmed = metrics.get("quality", {}).get("findings_confirmed", 0)
            total_val = metrics.get("quality", {}).get("findings_submitted", 0)
            validated = f"{confirmed}/{total_val}"
        elif tree:
            stats = tree.get("stats", {})
            budget = f"${stats.get('budget', '?')}"
            cost = f"${stats.get('total_cost', '?'):.2f}" if isinstance(stats.get('total_cost'), float) else "?"
            nodes = stats.get("nodes_spawned", "?")
            obs = stats.get("observations_collected", "?")
            validated = f"{stats.get('findings_confirmed', '?')}/{stats.get('findings_validated', '?')}"

            # Try to get date from events
            events = load_jsonl(run_dir / "events.jsonl")
            if events:
                ts = events[0].get("timestamp", 0)
                if ts:
                    date = datetime.fromtimestamp(ts).strftime("%Y-%m-%d")

            source_events = [e for e in events if e.get("type") == "source_info"]
            if source_events:
                source = source_events[0].get("source_name", "?")

        dashboard_link = f"[dashboard]({run_id}/dashboard.md)" if has_dashboard else "—"
        coverage = "full" if has_transcripts else "none"

        lines.append(f"| {run_id[:8]} | {date} | {source} | {budget} | {cost} | "
                     f"{nodes} | {obs} | {validated} | {dashboard_link} | {coverage} |")

    lines.append("")
    return "\n".join(lines)


# ─── Main ────────────────────────────────────────────────────

def main():
    output_dir = "output"
    target_run = sys.argv[1] if len(sys.argv) > 1 else None

    if target_run:
        runs = [target_run]
    else:
        runs = sorted([d for d in os.listdir(output_dir)
                       if os.path.isdir(os.path.join(output_dir, d))])

    print(f"Processing {len(runs)} runs...")
    results = []
    for run_id in runs:
        result = process_run(run_id, output_dir)
        if result:
            results.append(result)
            print(f"  {run_id}: {result['transcripts']} transcripts ({result['coverage']})")
        else:
            print(f"  {run_id}: skipped (not a valid run)")

    # Build cross-run index
    index = build_index(output_dir)
    with open(os.path.join(output_dir, "INDEX.md"), "w") as f:
        f.write(index)

    total_transcripts = sum(r["transcripts"] for r in results)
    runs_with_data = sum(1 for r in results if r["transcripts"] > 0)
    print(f"\nDone: {total_transcripts} transcripts across {runs_with_data} runs")
    print(f"Index: output/INDEX.md")


if __name__ == "__main__":
    main()
