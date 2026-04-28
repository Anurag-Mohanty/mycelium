"""Obsidian vault export — generates a folder of interlinked markdown files.

After a run completes, produces wiki-linked markdown that can be opened
directly in Obsidian for graph navigation of discovered knowledge.
"""

import json
import re
import sqlite3
from collections import defaultdict
from datetime import datetime
from pathlib import Path


# Characters unsafe for filenames on any major OS
_UNSAFE_CHARS = re.compile(r'[/\\\"\'<>|?*:]')


def _sanitize_filename(name: str) -> str:
    """Replace filesystem-unsafe characters with hyphens, truncate to 200 chars."""
    sanitized = _UNSAFE_CHARS.sub("-", name).strip("-").strip()
    if len(sanitized) > 200:
        sanitized = sanitized[:200].rstrip("-").rstrip()
    return sanitized


def _generate_entity_summary(entity: dict, observations: list) -> str:
    """Build a 1-2 paragraph summary from entity attributes and observations."""
    name = entity.get("canonical_name") or entity.get("name", "Unknown")
    etype = entity.get("entity_type", "entity")
    corpus = entity.get("corpus", "")
    obs_count = entity.get("observation_count", 0)
    attrs = entity.get("attributes", {})
    if isinstance(attrs, str):
        try:
            attrs = json.loads(attrs)
        except (json.JSONDecodeError, TypeError):
            attrs = {}

    parts = [f"{name} is a {etype}"]
    if corpus:
        parts[0] += f" in the {corpus} corpus"
    parts[0] += "."

    if attrs:
        attr_strs = [f"{k}: {v}" for k, v in list(attrs.items())[:10]]
        parts.append("Attributes: " + ", ".join(attr_strs) + ".")

    if obs_count:
        parts.append(f"It has been referenced in {obs_count} observations.")

    # Include top observations as key findings
    top_obs = [o for o in observations[:5] if o.get("claim")]
    if top_obs:
        parts.append("Key observations include:")
        for o in top_obs:
            claim = o["claim"]
            if len(claim) > 200:
                claim = claim[:200] + "..."
            parts.append(f"- {claim}")

    return "\n\n".join(parts[:2]) + ("\n\n" + "\n".join(parts[2:]) if len(parts) > 2 else "")


def _build_relationship_section(relationships: list, entity_id_to_filename: dict) -> str:
    """Format relationships grouped by type with wiki-links."""
    if not relationships:
        return ""

    by_type = defaultdict(list)
    for rel in relationships:
        by_type[rel["relationship_type"]].append(rel)

    lines = ["## Relationships", ""]
    for rtype, rels in sorted(by_type.items()):
        lines.append(f"### {rtype}")
        for rel in rels:
            # Determine which end is the "other" entity
            target_id = rel.get("_target_id", "")
            target_name = rel.get("_target_name", "unknown")
            filename = entity_id_to_filename.get(target_id, _sanitize_filename(target_name))
            confidence = rel.get("confidence", 0)
            provenance = rel.get("provenance", "")
            detail = f"confidence: {confidence:.2f}" if confidence else ""
            if provenance:
                detail += f", {provenance}" if detail else provenance
            lines.append(f"- [[{filename}]] — {rtype}" + (f" ({detail})" if detail else ""))
        lines.append("")

    return "\n".join(lines)


def _load_from_deliverable_db(db_path: str) -> dict:
    """Load entities, observations, relationships, findings from deliverable.db."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    entities = [dict(r) for r in conn.execute("SELECT * FROM entities").fetchall()]
    observations = [dict(r) for r in conn.execute("SELECT * FROM observations").fetchall()]
    relationships = [dict(r) for r in conn.execute("SELECT * FROM relationships").fetchall()]

    findings = []
    try:
        findings = [dict(r) for r in conn.execute("SELECT * FROM findings").fetchall()]
    except sqlite3.OperationalError:
        pass  # table may not exist

    metadata = {}
    try:
        row = conn.execute("SELECT * FROM engagement_metadata LIMIT 1").fetchone()
        if row:
            metadata = dict(row)
    except sqlite3.OperationalError:
        pass

    conn.close()
    return {
        "entities": entities,
        "observations": observations,
        "relationships": relationships,
        "findings": findings,
        "metadata": metadata,
    }


def _load_from_knowledge_graph_json(kg_path: str) -> dict:
    """Load from knowledge_graph.json fallback."""
    with open(kg_path) as f:
        data = json.load(f)

    return {
        "entities": data.get("entities", []),
        "observations": data.get("observations", []),
        "relationships": data.get("relationships", []),
        "findings": [],
        "metadata": {},
    }


def _load_data(run_dir: str) -> dict:
    """Load from deliverable.db if available, else knowledge_graph.json."""
    run_path = Path(run_dir)
    db_path = run_path / "deliverable.db"
    kg_path = run_path / "knowledge_graph.json"

    if db_path.exists():
        return _load_from_deliverable_db(str(db_path))
    elif kg_path.exists():
        return _load_from_knowledge_graph_json(str(kg_path))
    else:
        raise FileNotFoundError(
            f"No deliverable.db or knowledge_graph.json in {run_dir}")


def _resolve_filenames(entities: list) -> dict:
    """Build entity_id -> filename mapping, handling name collisions."""
    # Group by sanitized name to detect collisions
    name_groups = defaultdict(list)
    for e in entities:
        name = e.get("canonical_name") or e.get("name", "unknown")
        sanitized = _sanitize_filename(name)
        name_groups[sanitized].append(e)

    id_to_filename = {}
    for sanitized, group in name_groups.items():
        if len(group) == 1:
            id_to_filename[group[0]["id"]] = sanitized
        else:
            # Collision: append entity type
            for e in group:
                etype = e.get("entity_type", "entity")
                id_to_filename[e["id"]] = f"{sanitized} ({etype})"

    return id_to_filename


def generate_vault(run_dir: str, run_id: str,
                   min_confidence: float = 0.3,
                   min_observations: int = 1) -> str:
    """Generate an Obsidian vault from a completed run.

    Returns the vault directory path.
    """
    data = _load_data(run_dir)
    entities = data["entities"]
    observations = data["observations"]
    relationships = data["relationships"]
    findings = data["findings"]
    metadata = data["metadata"]

    # Filter entities
    filtered_entities = []
    for e in entities:
        obs_count = e.get("observation_count", 0) or 0
        if obs_count < min_observations:
            continue
        filtered_entities.append(e)

    # Build lookups
    entity_by_id = {e["id"]: e for e in filtered_entities}
    id_to_filename = _resolve_filenames(filtered_entities)
    filtered_ids = set(entity_by_id.keys())

    # Group observations by entity
    obs_by_entity = defaultdict(list)
    for o in observations:
        eid = o.get("entity_id", "")
        if eid in filtered_ids:
            obs_by_entity[eid].append(o)

    # Group relationships by entity (both directions)
    rels_by_entity = defaultdict(list)
    for r in relationships:
        from_id = r.get("from_entity", "")
        to_id = r.get("to_entity", "")
        if from_id in filtered_ids and to_id in filtered_ids:
            # For the "from" entity, the target is "to"
            r_copy = dict(r)
            r_copy["_target_id"] = to_id
            r_copy["_target_name"] = entity_by_id.get(to_id, {}).get(
                "canonical_name", entity_by_id.get(to_id, {}).get("name", "unknown"))
            rels_by_entity[from_id].append(r_copy)
            # For the "to" entity, the target is "from"
            r_copy2 = dict(r)
            r_copy2["_target_id"] = from_id
            r_copy2["_target_name"] = entity_by_id.get(from_id, {}).get(
                "canonical_name", entity_by_id.get(from_id, {}).get("name", "unknown"))
            rels_by_entity[to_id].append(r_copy2)

    # Create vault directory
    vault_dir = Path(run_dir) / "obsidian_vault"
    vault_dir.mkdir(parents=True, exist_ok=True)

    # Build finding filenames
    finding_filenames = {}
    for f in findings:
        summary = f.get("summary", "Untitled")
        finding_filenames[f["id"]] = f"Finding - {_sanitize_filename(summary)}"

    # Generate entity files
    entity_count = 0
    for e in filtered_entities:
        eid = e["id"]
        filename = id_to_filename.get(eid, _sanitize_filename(e.get("name", "unknown")))
        name = e.get("canonical_name") or e.get("name", "Unknown")
        etype = e.get("entity_type", "")
        corpus = e.get("corpus") or metadata.get("corpus", "")
        first_run = e.get("first_observed_run") or run_id
        last_run = e.get("last_observed_run") or run_id
        obs_count = e.get("observation_count", 0) or 0

        entity_obs = obs_by_entity.get(eid, [])
        entity_rels = rels_by_entity.get(eid, [])

        # Frontmatter
        lines = [
            "---",
            f"entity_type: {etype}",
            f"corpus: {corpus}",
            f"first_observed_run: {first_run or run_id}",
            f"last_observed_run: {last_run or run_id}",
            f"observation_count: {obs_count}",
            "---",
            "",
            f"# {name}",
            "",
        ]

        # Summary
        summary_text = _generate_entity_summary(e, entity_obs)
        lines.append(summary_text)
        lines.append("")

        # Relationships
        rel_section = _build_relationship_section(entity_rels, id_to_filename)
        if rel_section:
            lines.append(rel_section)

        # Findings that reference this entity (search by name in finding text)
        matching_findings = []
        entity_name_lower = name.lower()
        for f in findings:
            text = (f.get("summary", "") + " " + f.get("impact_summary", "")).lower()
            if entity_name_lower in text and len(entity_name_lower) > 2:
                matching_findings.append(f)

        if matching_findings:
            lines.append("## Findings")
            lines.append("")
            for f in matching_findings:
                fname = finding_filenames.get(f["id"], "Unknown finding")
                lines.append(f"- [[{fname}]] — references this entity")
            lines.append("")

        # Observations
        if entity_obs:
            lines.append("## Observations")
            lines.append("")
            for o in entity_obs[:20]:  # cap at 20 to keep files reasonable
                claim = o.get("claim", "")
                if len(claim) > 300:
                    claim = claim[:300] + "..."
                conf = o.get("confidence", 0)
                node_id = o.get("source_node_id", "")
                run = o.get("source_run_id", run_id)
                detail_parts = []
                if run:
                    detail_parts.append(f"run {run}")
                if node_id:
                    detail_parts.append(f"node {node_id}")
                if conf:
                    detail_parts.append(f"confidence: {conf:.2f}")
                detail = ", ".join(detail_parts)
                lines.append(f"- \"{claim}\"" + (f" ({detail})" if detail else ""))
            if len(entity_obs) > 20:
                lines.append(f"- ... and {len(entity_obs) - 20} more observations")
            lines.append("")

        filepath = vault_dir / f"{filename}.md"
        filepath.write_text("\n".join(lines))
        entity_count += 1

    # Generate finding files
    for f in findings:
        fid = f["id"]
        filename = finding_filenames.get(fid, f"Finding - {fid}")
        summary = f.get("summary", "Untitled")
        ftype = f.get("type", "")
        validation = f.get("validation_status", "")
        significance = f.get("significance_level", "")
        impact = f.get("impact_summary", "")

        lines = [
            "---",
            f"run_id: {run_id}",
            f"validation_status: {validation}",
            f"significance_tier: {significance}",
            f"finding_type: {ftype}",
            "---",
            "",
            f"# Finding: {summary}",
            "",
        ]

        if impact:
            lines.append(impact)
            lines.append("")

        # Sources: entities whose names appear in the finding text
        text_lower = (summary + " " + impact).lower()
        source_entities = []
        for e in filtered_entities:
            ename = (e.get("canonical_name") or e.get("name", "")).lower()
            if ename and len(ename) > 2 and ename in text_lower:
                source_entities.append(e)

        if source_entities:
            lines.append("## Sources")
            lines.append("")
            for e in source_entities[:20]:
                eid = e["id"]
                efname = id_to_filename.get(eid, _sanitize_filename(e.get("name", "")))
                lines.append(f"- [[{efname}]]")
            lines.append("")

        # Related findings: those sharing source entities
        related = []
        source_ids = {e["id"] for e in source_entities}
        for other_f in findings:
            if other_f["id"] == fid:
                continue
            other_text = (other_f.get("summary", "") + " " + other_f.get("impact_summary", "")).lower()
            for e in source_entities:
                ename = (e.get("canonical_name") or e.get("name", "")).lower()
                if ename and len(ename) > 2 and ename in other_text:
                    related.append(other_f)
                    break

        if related:
            lines.append("## Related Findings")
            lines.append("")
            for rf in related:
                rfname = finding_filenames.get(rf["id"], "Unknown")
                lines.append(f"- [[{rfname}]] — shares entities")
            lines.append("")

        filepath = vault_dir / f"{filename}.md"
        filepath.write_text("\n".join(lines))

    # Generate index file
    corpus = metadata.get("corpus", "")
    timestamp = metadata.get("run_timestamp", datetime.now().isoformat())
    finding_count = len(findings)

    # Top entities by observation count
    sorted_entities = sorted(
        filtered_entities,
        key=lambda e: e.get("observation_count", 0) or 0,
        reverse=True,
    )

    index_lines = [
        "# Mycelium Knowledge Vault",
        "",
        f"Run: {run_id}",
        f"Corpus: {corpus}",
        f"Date: {timestamp}",
        f"Entities: {entity_count}",
        f"Findings: {finding_count}",
        "",
        "## Top Entities (by observation count)",
        "",
    ]

    for i, e in enumerate(sorted_entities[:30], 1):
        eid = e["id"]
        fname = id_to_filename.get(eid, _sanitize_filename(e.get("name", "")))
        obs_count = e.get("observation_count", 0) or 0
        index_lines.append(f"{i}. [[{fname}]] — {obs_count} observations")

    index_lines.append("")

    if findings:
        index_lines.append("## Findings")
        index_lines.append("")
        for f in findings:
            fname = finding_filenames.get(f["id"], "Unknown")
            status = f.get("validation_status", "")
            index_lines.append(f"- [[{fname}]]" + (f" ({status})" if status else ""))
        index_lines.append("")

    index_lines.extend([
        "## How to Navigate",
        "",
        "- Click any [[wiki-link]] to open that entity's page",
        "- Use Obsidian's graph view to see connections",
        "- Use the backlinks panel to see what references each entity",
        "",
    ])

    (vault_dir / "_index.md").write_text("\n".join(index_lines))

    return str(vault_dir)


def update_persistent_vault(run_dir: str, run_id: str, corpus: str) -> str:
    """Update a cumulative vault at catalog/obsidian_vault/{corpus_short}/.

    - Existing entities: update observation_count, last_observed_run, add new observations
    - New entities: create new files
    - Entities not in this run: leave untouched
    """
    # Derive corpus_short
    corpus_lower = corpus.lower()
    if "npm" in corpus_lower:
        corpus_short = "npm"
    elif "sec" in corpus_lower or "edgar" in corpus_lower:
        corpus_short = "sec"
    elif "federal" in corpus_lower:
        corpus_short = "federal_register"
    else:
        corpus_short = _sanitize_filename(corpus).lower().replace(" ", "_")

    vault_dir = Path("catalog") / "obsidian_vault" / corpus_short
    vault_dir.mkdir(parents=True, exist_ok=True)

    # Load this run's data
    data = _load_data(run_dir)
    entities = data["entities"]
    observations = data["observations"]
    metadata = data["metadata"]

    # Build lookups
    obs_by_entity = defaultdict(list)
    for o in observations:
        obs_by_entity[o.get("entity_id", "")].append(o)

    id_to_filename = _resolve_filenames(entities)

    for e in entities:
        eid = e["id"]
        obs_count = e.get("observation_count", 0) or 0
        if obs_count < 1:
            continue

        filename = id_to_filename.get(eid, _sanitize_filename(e.get("name", "unknown")))
        filepath = vault_dir / f"{filename}.md"
        entity_obs = obs_by_entity.get(eid, [])
        name = e.get("canonical_name") or e.get("name", "Unknown")
        etype = e.get("entity_type", "")
        e_corpus = e.get("corpus", corpus)

        if filepath.exists():
            # Update existing file: rewrite frontmatter and append new observations
            content = filepath.read_text()

            # Parse existing frontmatter
            if content.startswith("---"):
                end_idx = content.index("---", 3)
                fm_text = content[3:end_idx].strip()
                body = content[end_idx + 3:].lstrip("\n")

                # Update frontmatter fields
                fm_lines = fm_text.split("\n")
                fm_dict = {}
                for line in fm_lines:
                    if ": " in line:
                        k, v = line.split(": ", 1)
                        fm_dict[k.strip()] = v.strip()

                fm_dict["last_observed_run"] = run_id
                # Accumulate observation count
                old_count = int(fm_dict.get("observation_count", "0"))
                fm_dict["observation_count"] = str(old_count + obs_count)

                new_fm = "---\n"
                for k, v in fm_dict.items():
                    new_fm += f"{k}: {v}\n"
                new_fm += "---\n"

                # Append new observations to the body
                new_obs_lines = []
                for o in entity_obs[:10]:
                    claim = o.get("claim", "")
                    if len(claim) > 300:
                        claim = claim[:300] + "..."
                    conf = o.get("confidence", 0)
                    new_obs_lines.append(
                        f"- \"{claim}\" (run {run_id}"
                        + (f", confidence: {conf:.2f}" if conf else "")
                        + ")"
                    )

                if new_obs_lines:
                    body = body.rstrip("\n")
                    body += f"\n\n### Run {run_id}\n\n" + "\n".join(new_obs_lines) + "\n"

                filepath.write_text(new_fm + "\n" + body)
            else:
                # No frontmatter — just append
                content += f"\n\n### Run {run_id}\n\n"
                for o in entity_obs[:10]:
                    claim = o.get("claim", "")[:300]
                    content += f"- \"{claim}\"\n"
                filepath.write_text(content)
        else:
            # New entity — create fresh file
            lines = [
                "---",
                f"entity_type: {etype}",
                f"corpus: {e_corpus}",
                f"first_observed_run: {run_id}",
                f"last_observed_run: {run_id}",
                f"observation_count: {obs_count}",
                "---",
                "",
                f"# {name}",
                "",
            ]
            summary_text = _generate_entity_summary(e, entity_obs)
            lines.append(summary_text)
            lines.append("")

            if entity_obs:
                lines.append("## Observations")
                lines.append("")
                for o in entity_obs[:20]:
                    claim = o.get("claim", "")
                    if len(claim) > 300:
                        claim = claim[:300] + "..."
                    conf = o.get("confidence", 0)
                    lines.append(
                        f"- \"{claim}\" (run {run_id}"
                        + (f", confidence: {conf:.2f}" if conf else "")
                        + ")"
                    )
                lines.append("")

            filepath.write_text("\n".join(lines))

    return str(vault_dir)
