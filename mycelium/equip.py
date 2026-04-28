"""EQUIP — workspace prep node for exploration engagements.

EQUIP forms first, prepares the data layer for the team, writes a
SKILL.md workspace artifact, and exits. Every downstream node reads
the artifact at formation. Workers describe slices in natural language;
EQUIP's preparation handles how the data is reached.

Phase 1: exploration profile only. Other engagement types refuse cleanly.
"""

import json
import time
from pathlib import Path

import anthropic

from . import events


SUPPORTED_PROFILES = {"exploration"}


async def run_equip(data_source, charter: str, catalog_stats: dict | None,
                    bulletin_board, budget: float = 0.50) -> dict:
    """Run EQUIP workspace prep. Returns status dict.

    Args:
        data_source: the corpus data source instance
        charter: genesis charter text
        catalog_stats: analytical survey results (or None)
        bulletin_board: the engagement's bulletin board
        budget: EQUIP budget allocation (default $0.50)

    Returns:
        {"status": "READY" | "CANNOT_PREP", "reason": str, "cost": float}
    """
    corpus_name = data_source.__class__.__name__
    equip_id = "equip"
    cost = 0.0

    print(f"\n  [EQUIP] Workspace prep starting for {corpus_name}")
    events.emit("node_spawned", {
        "node_id": equip_id,
        "parent_id": "__genesis__",
        "tree_position": "EQUIP",
        "scope_summary": f"Workspace prep for {corpus_name}",
        "role_name": "workspace_prep",
    })

    # --- Step 1: Coverage discovery ---
    catalog_path = data_source.catalog_path() if hasattr(data_source, 'catalog_path') else None
    if catalog_path is None or not catalog_path.exists():
        reason = (f"No enriched catalog found for {corpus_name}. "
                  f"Run catalog enrichment first (fetch_bulk_metadata).")
        print(f"  [EQUIP] CANNOT_PREP: {reason}")
        events.emit("node_resolved", {
            "node_id": equip_id, "tree_position": "EQUIP",
            "observations_count": 0, "cost_spent": 0,
            "top_observation": f"CANNOT_PREP: {reason}",
        })
        return {"status": "CANNOT_PREP", "reason": reason, "cost": 0}

    # Count records
    record_count = 0
    with open(catalog_path) as f:
        for line in f:
            if line.strip():
                record_count += 1

    # Read sample records for schema description
    sample_records = []
    with open(catalog_path) as f:
        for line in f:
            if line.strip():
                sample_records.append(json.loads(line))
            if len(sample_records) >= 5:
                break

    if not sample_records:
        reason = f"Catalog at {catalog_path} is empty."
        print(f"  [EQUIP] CANNOT_PREP: {reason}")
        return {"status": "CANNOT_PREP", "reason": reason, "cost": 0}

    catalog_modified = catalog_path.stat().st_mtime
    coverage_timestamp = time.strftime("%Y-%m-%d %H:%M", time.localtime(catalog_modified))

    print(f"  [EQUIP] Coverage: {record_count:,} records in {catalog_path.name} "
          f"(as of {coverage_timestamp})")

    # --- Step 2: Analytical survey ---
    if catalog_stats:
        n_clusters = len(catalog_stats.get("anomaly_clusters", []))
        n_outliers = len(catalog_stats.get("outliers", []))
        n_concentrations = len(catalog_stats.get("concentrations", []))
        print(f"  [EQUIP] Survey: {n_clusters} anomaly clusters, "
              f"{n_outliers} outliers, {n_concentrations} concentrations")
    else:
        print(f"  [EQUIP] No analytical survey available — proceeding without shape view")

    # --- Step 3: Author SKILL.md via LLM ---

    # Build catalog metadata for the prompt
    cat_meta = data_source.catalog_metadata() if hasattr(data_source, 'catalog_metadata') else {}
    fields_desc = ""
    for f in cat_meta.get("fields", []):
        fname = f["name"]
        ftype = f.get("type", "TEXT")
        range_str = ""
        if f.get("min") is not None:
            range_str = f", range {f['min']} to {f['max']}"
        fields_desc += f"  - {fname} ({ftype}{range_str})\n"

    # Build distribution summaries for numeric fields (partition-ready)
    distributions_text = _build_distributions(data_source)

    # Build survey summary
    survey_summary = _format_survey_summary(catalog_stats) if catalog_stats else "(no survey available)"

    # Sample records formatted
    samples_text = ""
    for i, rec in enumerate(sample_records[:3], 1):
        samples_text += f"Sample record {i}:\n"
        for k, v in rec.items():
            samples_text += f"  {k}: {str(v)[:100]}\n"
        samples_text += "\n"

    prompt = f"""You are EQUIP, the workspace preparation node for a Mycelium exploration engagement.

Your job: write a SKILL.md that prepares the team to investigate this corpus. The team will read what you write. They consume; you prepare.

ENGAGEMENT DIRECTIVE:
{charter[:2000]}

CORPUS: {corpus_name}
CATALOG: {record_count:,} enriched records at {catalog_path.name}
CATALOG TIMESTAMP: {coverage_timestamp}

SCHEMA (fields in each catalog record):
{fields_desc}

SAMPLE RECORDS:
{samples_text}

ANALYTICAL SURVEY RESULTS:
{survey_summary}

FIELD DISTRIBUTIONS (for partitioning):
{distributions_text}

---

Write a SKILL.md with these seven sections. Do not tell the team what to look for or what's interesting. Describe what's available and how to reach it.

1. **Corpus Orientation** — one or two paragraphs describing what kind of data this is, what the records represent, what vocabulary the team should use.

2. **Schema** — for each field in the catalog, a one-line description with type, range or distribution, and a sample value. Format: `field_name (type, range): description. Example: <sample>`. List every field. Do not truncate.

3. **Shape View** — the analytical survey results as the team will read them. For each anomaly cluster: cluster name, count, description, 3-5 example records. Same for outliers and concentrations. If no survey, say so.

4. **Partitioning Guide** — this is the MOST IMPORTANT section. The engagement lead's job is to partition the corpus into non-overlapping slices that together cover every record. This section gives the engagement lead the data it needs to do that job.

   For each numeric field that would make a good partition dimension, provide:
   - The field name and what it measures
   - The distribution: min, p25, median, p75, max
   - Natural break points where a cut produces meaningfully different groups
   - How many records fall in each segment if you cut there
   - A ready-to-use example partition using this field

   Suggest 3-5 concrete partition schemes. Each scheme is a set of non-overlapping filter conditions that together cover 100% of the corpus. For example:
   - Scheme A (by maintainer_count): "maintainer_count = 1" (N records) + "maintainer_count 2-5" (N records) + "maintainer_count > 5" (N records)
   - Scheme B (by monthly_downloads): "downloads < 5000" (N records) + "5000-100000" (N records) + "> 100000" (N records)

   The engagement lead will pick one of these schemes (or combine dimensions). Make the schemes concrete with actual record counts so the engagement lead can make an informed choice.

5. **Coverage Report** — how many records are cataloged, what percentage of the full corpus this represents (estimate if needed), when the catalog was built, what coverage tier this is (scout / partial / full).

6. **Tools** — what's connected and accessible. For this engagement: the enriched catalog ({record_count:,} records), the analytical survey results, and a search API as a fallback path.

7. **Partition Rules** — include this section verbatim:
   The engagement lead MUST author data partitions, not analytical lenses.
   - A partition is a filter condition over record fields: "maintainer_count = 1", "created before 2020"
   - Partitions tile the corpus: every record is in exactly one partition
   - A lens is an analytical question: "coordination patterns", "temporal anomalies"
   - Lenses CANNOT be partitions because they don't map to record filters
   - The partition gate will HALT the run if partitions overlap or don't cover the corpus

Return ONLY the SKILL.md content as markdown. No preamble, no JSON wrapper."""

    # LLM call
    client = anthropic.AsyncAnthropic()
    try:
        response = await client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=4000,
            messages=[{"role": "user", "content": prompt}],
        )
        skill_md = response.content[0].text
        cost = (response.usage.input_tokens * 3 / 1_000_000 +
                response.usage.output_tokens * 15 / 1_000_000)
    except Exception as e:
        reason = f"LLM call failed: {e}"
        print(f"  [EQUIP] CANNOT_PREP: {reason}")
        events.emit("node_resolved", {
            "node_id": equip_id, "tree_position": "EQUIP",
            "observations_count": 0, "cost_spent": cost,
            "top_observation": f"CANNOT_PREP: {reason}",
        })
        return {"status": "CANNOT_PREP", "reason": reason, "cost": cost}

    print(f"  [EQUIP] SKILL.md authored ({len(skill_md)} chars, ${cost:.3f})")

    # --- Step 4: Post to bulletin board and sign off ---
    bulletin_board.post(
        author_node_id=equip_id,
        author_role_name="workspace_prep",
        post_type="EQUIP_BRIEFING",
        content=skill_md,
    )

    events.emit("node_resolved", {
        "node_id": equip_id, "tree_position": "EQUIP",
        "observations_count": 0, "cost_spent": cost,
        "top_observation": f"SKILL.md authored ({len(skill_md)} chars)",
    })
    events.emit("bb_post", {
        "node_id": equip_id,
        "post_count": 1,
        "post_type": "EQUIP_BRIEFING",
    })

    print(f"  [EQUIP] READY — workspace artifact posted to bulletin board")
    return {"status": "READY", "reason": "SKILL.md authored and posted", "cost": cost}


def _format_survey_summary(catalog_stats: dict) -> str:
    """Format analytical survey results for the EQUIP prompt."""
    parts = []

    clusters = catalog_stats.get("anomaly_clusters", [])
    if clusters:
        parts.append(f"Anomaly clusters ({len(clusters)}):")
        for c in clusters[:10]:
            if isinstance(c, dict):
                parts.append(
                    f"  - {c.get('description', c.get('name', '?'))}: "
                    f"{c.get('count', '?')} records"
                )

    outliers = catalog_stats.get("outliers", [])
    if outliers:
        parts.append(f"\nOutliers ({len(outliers)}):")
        for o in outliers[:10]:
            if isinstance(o, dict):
                parts.append(
                    f"  - {o.get('record', o.get('entity', '?'))}: "
                    f"{o.get('description', o.get('field', '?'))} "
                    f"(z={o.get('z_score', '?')})"
                )

    concentrations = catalog_stats.get("concentrations", [])
    if concentrations:
        parts.append(f"\nConcentrations ({len(concentrations)}):")
        for co in concentrations[:5]:
            if isinstance(co, dict):
                parts.append(f"  - {co.get('description', str(co)[:200])}")

    return "\n".join(parts) if parts else "(no survey results)"


def _build_distributions(data_source) -> str:
    """Build distribution summaries for numeric fields from the catalog DB.

    Returns a formatted string with percentiles and segment counts for each
    numeric field, ready for the engagement lead to use for partitioning.
    """
    if not hasattr(data_source, '_ensure_catalog_db'):
        return "(no catalog DB available for distributions)"

    import sqlite3
    data_source._ensure_catalog_db()
    db = data_source._catalog_db

    # Discover numeric columns
    try:
        cols = db.execute("PRAGMA table_info(records)").fetchall()
    except sqlite3.OperationalError:
        return "(cannot read schema)"

    numeric_cols = [c[1] for c in cols if c[2].upper() in ("INTEGER", "REAL", "NUMERIC")]
    if not numeric_cols:
        return "(no numeric fields found)"

    parts = []
    for col in numeric_cols:
        try:
            rows = db.execute(
                f"SELECT {col} FROM records WHERE {col} IS NOT NULL ORDER BY {col}"
            ).fetchall()
            vals = [r[0] for r in rows if r[0] is not None]
        except sqlite3.OperationalError:
            continue

        if len(vals) < 10:
            continue

        n = len(vals)
        p25, med, p75 = vals[n // 4], vals[n // 2], vals[3 * n // 4]

        header = f"{col} (n={n:,}): min={vals[0]}, p25={p25}, median={med}, p75={p75}, max={vals[-1]}"
        parts.append(header)

        # Segment counts at natural breakpoints
        if col == "maintainer_count" or (vals[-1] <= 500 and med <= 5):
            # Low-range field: count at specific values
            for bp in sorted(set([0, 1, 2, 5, 10, med, p75])):
                bp = int(bp)
                eq = sum(1 for v in vals if v == bp)
                if eq > 0:
                    parts.append(f"  ={bp}: {eq:,} ({eq / n * 100:.1f}%)")
            gt10 = sum(1 for v in vals if v > 10)
            if gt10 > 0:
                parts.append(f"  >10: {gt10:,} ({gt10 / n * 100:.1f}%)")
        else:
            # High-range field: count at percentile boundaries
            for label, lo, hi in [
                (f"<= {p25}", None, p25),
                (f"{p25+1} to {med}", p25 + 1, med),
                (f"{med+1} to {p75}", med + 1, p75),
                (f"> {p75}", p75 + 1, None),
            ]:
                count = sum(1 for v in vals
                            if (lo is None or v >= lo) and (hi is None or v <= hi))
                if count > 0:
                    parts.append(f"  {label}: {count:,} ({count / n * 100:.1f}%)")

        parts.append("")  # blank line between fields

    return "\n".join(parts) if parts else "(no distributions computed)"
