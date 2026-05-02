"""Synthesizer — the attention mechanism of Mycelium.

When a parent node receives observations from all its children, synthesis
runs the equivalent of cross-attention: each observation is checked against
every other observation from sibling nodes to find reinforcements,
contradictions, and cross-cutting patterns.

This is where discoveries emerge that no single node could have found alone.
"""

import json
import anthropic
from .schemas import NodeResult, SynthesisResult
from . import prompts as _prompts


async def synthesize(parent_result: NodeResult, children_results: list[NodeResult],
                     lenses: list[str], light: bool = False,
                     synthesis_role: dict = None,
                     workspace_context: str = "",
                     data_source=None) -> SynthesisResult:
    """Cross-reference observations from sibling nodes to find emergent patterns.

    Args:
        parent_result: The parent node's own result (for context)
        children_results: Results from all child nodes
        lenses: Attention lenses to score against

    Returns:
        SynthesisResult with reinforced patterns, contradictions, cross-cutting
        patterns, and discovered questions
    """
    node_id = parent_result.node_id

    # Skip synthesis if there's nothing to cross-reference
    all_observations = []
    for child in children_results:
        all_observations.extend(child.observations)

    if len(all_observations) < 2:
        return _empty_synthesis(node_id)

    # Format investigator reports for the synthesis prompt
    reports = _format_investigator_reports(children_results)
    lenses_str = ", ".join(lenses)

    # Use role-anchored synthesis if a synthesis role was authored
    if synthesis_role and synthesis_role.get("success_bar"):
        prompt = _prompts.SYNTHESIS_PROMPT_V2.format(
            role_name=synthesis_role.get("name", "synthesis"),
            role_bar=synthesis_role.get("success_bar", ""),
            role_heuristic=synthesis_role.get("heuristic", ""),
            workspace_context=workspace_context,
            investigator_reports=reports,
        )
    else:
        template = _prompts.SYNTHESIS_LIGHT_PROMPT if light else _prompts.SYNTHESIS_PROMPT
        prompt = template.format(
            investigator_reports=reports,
            lenses=lenses_str,
        )

    # Run synthesis — light uses fewer tokens
    client = anthropic.Anthropic()
    response = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=1500 if light else 4096,
        messages=[{"role": "user", "content": prompt}],
    )

    raw_text = response.content[0].text
    usage = {
        "input_tokens": response.usage.input_tokens,
        "output_tokens": response.usage.output_tokens,
    }
    cost = (usage["input_tokens"] * 3 + usage["output_tokens"] * 15) / 1_000_000

    # Parse the synthesis output
    try:
        result = _parse_json(raw_text)
    except (json.JSONDecodeError, ValueError) as e:
        print(f"  WARNING: Synthesis parse error: {e}")
        return SynthesisResult(
            node_id=node_id,
            reinforced=[],
            contradictions=[],
            cross_cutting=[],
            rescored_observations=all_observations,
            discovered_questions=[],
            unresolved_threads=[f"Synthesis parse error: {str(e)[:100]}"],
            raw_reasoning=raw_text,
            token_usage=usage,
            cost=cost,
        )

    # Verify cross-cutting patterns against corpus if data source available
    cross_cutting = result.get("cross_cutting_patterns", [])
    if data_source and cross_cutting and hasattr(data_source, '_ensure_catalog_db'):
        verified = _verify_cross_cutting(data_source, cross_cutting)
        cross_cutting = verified["findings"]
        verify_cost = verified["cost"]
        cost += verify_cost
        usage["input_tokens"] += verified.get("input_tokens", 0)
        usage["output_tokens"] += verified.get("output_tokens", 0)

    synthesis = SynthesisResult(
        node_id=node_id,
        reinforced=result.get("reinforced", []),
        contradictions=result.get("contradictions", []),
        cross_cutting=cross_cutting,
        rescored_observations=all_observations,  # keep originals; rescoring is in the JSON
        discovered_questions=result.get("discovered_questions", []),
        unresolved_threads=result.get("unresolved_threads", []),
        raw_reasoning=raw_text,
        token_usage=usage,
        cost=cost,
    )

    return synthesis


def _format_investigator_reports(children: list[NodeResult]) -> str:
    """Format children's results into the investigator report format.

    Includes tree position and parent ID so synthesis can distinguish
    independent convergence from hierarchical echo.
    """
    # Build parent lookup for structural annotations
    id_to_pos = {c.node_id: c.tree_position for c in children if c.tree_position}
    id_to_idx = {}

    reports = []
    for i, child in enumerate(children, 1):
        id_to_idx[child.node_id] = i
        obs_text = []
        for obs in child.observations:
            obs_text.append(
                f"  - [{obs.observation_type}] {obs.raw_evidence}\n"
                f"    Source: {obs.source.title} ({obs.source.doc_id}, {obs.source.date})\n"
                f"    Agency: {obs.source.agency}\n"
                f"    Statistical grounding: {obs.statistical_grounding}\n"
                f"    Hypothesis: {obs.local_hypothesis}\n"
                f"    Surprising because: {obs.surprising_because}"
            )

        unresolved_text = ""
        if child.unresolved:
            unresolved_text = "\n  Unresolved threads:\n" + "\n".join(
                f"  - {u}" for u in child.unresolved
            )

        # Tree structure annotation
        pos = child.tree_position or "?"
        parent_idx = id_to_idx.get(child.parent_id, None) if child.parent_id else None
        parent_note = f", child of INVESTIGATOR {parent_idx}" if parent_idx else ""

        reports.append(
            f"INVESTIGATOR {i} (pos={pos}{parent_note}, assigned to: {child.scope_description}):\n"
            f"  Survey: {child.survey[:300]}\n\n"
            f"  Observations:\n" + "\n".join(obs_text) +
            unresolved_text
        )

    return "\n\n---\n\n".join(reports)


def _empty_synthesis(node_id: str) -> SynthesisResult:
    """Return an empty synthesis when there's nothing to cross-reference."""
    return SynthesisResult(
        node_id=node_id,
        reinforced=[],
        contradictions=[],
        cross_cutting=[],
        rescored_observations=[],
        discovered_questions=[],
        unresolved_threads=[],
        raw_reasoning="(skipped — insufficient observations for synthesis)",
        token_usage={},
        cost=0.0,
    )


def _verify_cross_cutting(data_source, findings: list[dict]) -> dict:
    """Verify cross-cutting patterns against the corpus database.

    For each finding, extract key claims and run COUNT/SELECT queries
    to check whether the pattern holds in the actual data.
    """
    import re
    data_source._ensure_catalog_db()
    db = data_source._catalog_db

    # Get column names for query construction
    try:
        cursor = db.execute("PRAGMA table_info(records)")
        columns = {row[1] for row in cursor.fetchall()}
    except Exception:
        columns = set()

    id_cols = [c for c in ("name", "company", "title") if c in columns]

    for finding in findings:
        pattern = finding.get("pattern", "")
        evidence_chain = finding.get("evidence_chain", [])

        # Extract entity names from the finding for lookup
        entities = set()
        text = pattern + " " + " ".join(
            c.get("claim", "") for c in evidence_chain if isinstance(c, dict))
        for m in re.findall(r'\b([A-Z][A-Za-z]*(?:\s+[A-Z][A-Za-z]*){1,2})\b', text):
            entities.add(m)
        for m in re.findall(r'\b([A-Z][A-Za-z]*(?:\s*&\s*[A-Z][A-Za-z]*)+)\b', text):
            entities.add(m)

        # Query corpus for each entity
        verified_entities = []
        for entity in list(entities)[:8]:
            for col in id_cols:
                try:
                    rows = db.execute(
                        f"SELECT {col}, * FROM records WHERE {col} LIKE ? LIMIT 2",
                        (f"%{entity}%",)
                    ).fetchall()
                    if rows:
                        rec = dict(rows[0])
                        # Build a summary (skip huge text fields)
                        summary_parts = []
                        for k, v in rec.items():
                            if v and str(v).strip() and len(str(v)) < 200:
                                summary_parts.append(f"{k}={v}")
                        verified_entities.append({
                            "entity": entity,
                            "found": True,
                            "record_summary": ", ".join(summary_parts[:8]),
                            "match_count": len(rows),
                        })
                        break
                except Exception:
                    continue

        if verified_entities:
            confirmed = sum(1 for v in verified_entities if v["found"])
            total = len(verified_entities)
            if confirmed == total:
                finding["corpus_verification"] = "CONFIRMED"
            elif confirmed > 0:
                finding["corpus_verification"] = "PARTIAL"
            else:
                finding["corpus_verification"] = "UNVERIFIABLE"
            finding["verified_entities"] = verified_entities
        else:
            finding["corpus_verification"] = "UNVERIFIABLE"
            finding["verified_entities"] = []

    return {"findings": findings, "cost": 0, "input_tokens": 0, "output_tokens": 0}


def _parse_json(text: str) -> dict:
    """Extract and parse JSON from LLM output."""
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    if "```json" in text:
        start = text.find("```json") + 7
        end = text.find("```", start)
        if end > start:
            return json.loads(text[start:end].strip())
    if "```" in text:
        start = text.find("```") + 3
        end = text.find("```", start)
        if end > start:
            return json.loads(text[start:end].strip())
    start = text.find("{")
    end = text.rfind("}") + 1
    if start >= 0 and end > start:
        return json.loads(text[start:end])
    raise ValueError("Could not extract JSON from response")
