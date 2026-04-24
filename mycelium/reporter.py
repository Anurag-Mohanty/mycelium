"""Reporter — generates the five-tier exploration report.

Takes the complete exploration data (genesis, nodes, synthesis, validation,
impact) and produces a structured markdown report with the LLM.
"""

import json
import anthropic
from .prompts import REPORT_PROMPT


async def generate_report(exploration_data: dict) -> str:
    """Generate the final report with validation and impact sections.

    Args:
        exploration_data: Complete exploration data from the orchestrator

    Returns:
        Markdown string of the full report
    """
    print("\n  Generating final report...")

    stats = exploration_data["stats"]
    genesis = exploration_data["genesis"]
    syntheses = exploration_data["syntheses"]
    node_results = exploration_data["node_results"]
    validations = exploration_data.get("validations", [])
    impacts = exploration_data.get("impacts", [])
    hints = exploration_data.get("hints", [])

    # Gather all observations
    all_observations = []
    for nr in node_results:
        for obs in nr.get("observations", []):
            all_observations.append(obs)

    # Gather all unresolved threads
    all_unresolved = []
    for nr in node_results:
        all_unresolved.extend(nr.get("unresolved", []))
    for syn in syntheses:
        all_unresolved.extend(syn.get("unresolved_threads", []))

    # Format metadata
    elapsed = stats.get("elapsed_seconds", 0)
    minutes, seconds = int(elapsed // 60), int(elapsed % 60)
    phase_costs = stats.get("phase_costs", {})

    exploration_metadata = (
        f"Nodes spawned: {stats['nodes_spawned']}\n"
        f"Nodes resolved: {stats.get('nodes_resolved', 'N/A')}\n"
        f"Observations collected: {stats['observations_collected']}\n"
        f"Max depth reached: {stats['max_depth_reached']}\n"
        f"Avg branching factor: {stats.get('avg_branching_factor', 0):.1f}\n"
        f"Chain breaker fired: {stats.get('chain_breaker_fired', 0)} times\n"
        f"Findings validated: {stats.get('findings_validated', 0)}\n"
        f"Findings confirmed: {stats.get('findings_confirmed', 0)}\n"
        f"Total API calls: {stats['api_calls']}\n"
        f"Total tokens: {stats['total_tokens']:,}\n"
        f"Total cost: ${stats['total_cost']:.2f}\n"
        f"Cost breakdown: explore=${phase_costs.get('exploration', 0):.2f} "
        f"synth=${phase_costs.get('synthesis', 0):.2f} "
        f"valid=${phase_costs.get('validation', 0):.2f} "
        f"impact=${phase_costs.get('impact', 0):.2f} "
        f"overhead=${phase_costs.get('overhead', 0):.2f}\n"
        f"Time elapsed: {minutes}m {seconds}s"
    )

    # Separate pipeline issues from corpus findings
    corpus_validations = [v for v in validations if not v.get("is_pipeline_issue")]
    pipeline_validations = [v for v in validations if v.get("is_pipeline_issue")]

    # Format sections (corpus findings only for the main report)
    synthesis_text = _format_syntheses(syntheses)
    observations_text = _format_observations(all_observations)
    validated_text = _format_validations(corpus_validations)
    impact_text = _format_impacts(impacts)
    unresolved_text = "\n".join(f"- {u}" for u in all_unresolved) if all_unresolved else "(none)"
    hints_text = "\n".join(f"- {h}" for h in hints) if hints else "none — fully autonomous"

    prompt = REPORT_PROMPT.format(
        exploration_metadata=exploration_metadata,
        corpus_summary=genesis.get("corpus_summary", ""),
        lenses=", ".join(genesis.get("lenses", [])),
        hints=hints_text,
        all_synthesis=synthesis_text,
        all_observations=observations_text,
        validated_findings=validated_text,
        impact_analyses=impact_text,
        unresolved=unresolved_text,
    )

    # Generate report
    client = anthropic.Anthropic()
    response = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=8192,
        messages=[{"role": "user", "content": prompt}],
    )

    report = response.content[0].text
    usage = {
        "input_tokens": response.usage.input_tokens,
        "output_tokens": response.usage.output_tokens,
    }
    cost = (usage["input_tokens"] * 3 + usage["output_tokens"] * 15) / 1_000_000

    # Add pipeline warning banner if any pipeline issues found
    if pipeline_validations:
        n = len(pipeline_validations)
        banner = (
            f"\n> **{n} finding(s) in this run were flagged as data pipeline issues "
            f"rather than corpus discoveries — see the \"Data Pipeline Observations\" "
            f"section below. Corpus findings in this report were produced from data "
            f"that may be affected by the same underlying issues.**\n"
        )
        # Insert after first heading + corpus summary paragraph
        insert_pos = report.find("\n## ")
        if insert_pos > 0:
            report = report[:insert_pos] + "\n" + banner + report[insert_pos:]

        # Append pipeline section
        pipeline_section = "\n\n## Data Pipeline Observations\n\n"
        pipeline_section += (
            "The following findings describe properties of Mycelium's data collection "
            "and extraction process rather than the corpus itself.\n\n"
        )
        for pv in pipeline_validations:
            finding = pv.get("original_finding", {})
            desc = (finding.get("what_conflicts", "") or finding.get("pattern", ""))[:200]
            pipeline_section += f"### Pipeline Finding: {desc[:80]}\n"
            pipeline_section += f"**What was observed:** {desc}\n"
            pipeline_section += f"**Likely cause:** {pv.get('pipeline_issue_reasoning', '')[:200]}\n"
            pipeline_section += f"**Validation:** {pv.get('verdict', '?').upper()}\n\n"
        report += pipeline_section

    print(f"  Report generated. Cost: ${cost:.4f}")
    return report


def _format_syntheses(syntheses: list[dict]) -> str:
    if not syntheses:
        return "(no synthesis performed)"

    parts = []
    for i, syn in enumerate(syntheses, 1):
        lines = [f"--- Synthesis {i} (node {syn['node_id'][:8]}) ---"]

        if syn.get("reinforced"):
            lines.append("\nReinforced patterns:")
            for r in syn["reinforced"]:
                lines.append(f"  - {r.get('pattern', '')}")
                lines.append(f"    Sources: {r.get('sources', [])}")
                lines.append(f"    Confidence: {r.get('confidence', 'N/A')}")

        if syn.get("contradictions"):
            lines.append("\nContradictions:")
            for c in syn["contradictions"]:
                lines.append(f"  - {c.get('what_conflicts', '')}")
                lines.append(f"    Side A: {c.get('side_a', {}).get('observation', '')}")
                lines.append(f"    Side B: {c.get('side_b', {}).get('observation', '')}")
                lines.append(f"    Significance: {c.get('significance', '')}")

        if syn.get("cross_cutting"):
            lines.append("\nCross-cutting patterns:")
            for cc in syn["cross_cutting"]:
                lines.append(f"  - {cc.get('pattern', '')}")
                lines.append(f"    Evidence: {cc.get('evidence_chain', [])}")
                lines.append(f"    Confidence: {cc.get('confidence', 'N/A')}")

        if syn.get("discovered_questions"):
            lines.append("\nDiscovered questions:")
            for q in syn["discovered_questions"]:
                lines.append(f"  - {q}")

        parts.append("\n".join(lines))

    return "\n\n".join(parts)


def _format_observations(observations: list[dict]) -> str:
    if not observations:
        return "(no observations collected)"

    by_type: dict[str, list] = {}
    for obs in observations:
        obs_type = obs.get("observation_type", "other")
        by_type.setdefault(obs_type, []).append(obs)

    lines = []
    for obs_type, obs_list in by_type.items():
        lines.append(f"\n[{obs_type.upper()}] ({len(obs_list)} observations)")
        for obs in obs_list[:10]:
            source = obs.get("source", {})
            lines.append(
                f"  - {obs.get('raw_evidence', obs.get('what_i_saw', ''))[:200]}\n"
                f"    Source: {source.get('title', '')} ({source.get('doc_id', '')}, {source.get('date', '')})\n"
                f"    Agency: {source.get('agency', '')}"
            )
        if len(obs_list) > 10:
            lines.append(f"  ... and {len(obs_list) - 10} more")

    return "\n".join(lines)


def _format_validations(validations: list[dict]) -> str:
    if not validations:
        return "(no findings validated)"

    lines = []
    for v in validations:
        verdict_icon = {"confirmed": "CONFIRMED", "confirmed_with_caveats": "CONFIRMED*",
                        "weakened": "WEAKENED", "refuted": "REFUTED",
                        "needs_verification": "UNVERIFIED"
                        }.get(v.get("verdict", ""), "UNKNOWN")
        finding = v.get("original_finding", {})
        desc = (finding.get("what_conflicts", "") or finding.get("pattern", ""))[:100]
        lines.append(
            f"  [{verdict_icon}] {desc}\n"
            f"    Confidence: {v.get('adjusted_confidence', 0):.2f}\n"
            f"    Reasoning: {v.get('reasoning', '')[:200]}\n"
            f"    Verification needed: {v.get('verification_action', '')[:100]}"
        )
        if v.get("revised_finding"):
            lines.append(f"    Revised: {v['revised_finding'][:200]}")

    return "\n".join(lines)


def _format_impacts(impacts: list[dict]) -> str:
    if not impacts:
        return "(no impact analyses performed)"

    lines = []
    for im in impacts:
        lines.append(
            f"  Finding: {im.get('finding_id', '')}\n"
            f"    Affected: {', '.join(im.get('affected_parties', []))}\n"
            f"    Scale: {im.get('estimated_scale', 'unknown')}\n"
            f"    Financial: {im.get('financial_exposure', '')[:150]}\n"
            f"    Risk scenario: {im.get('risk_scenario', '')[:200]}\n"
            f"    Who needs to know: {', '.join(im.get('who_needs_to_know', []))}\n"
            f"    Urgency: {im.get('urgency', 'medium')}\n"
            f"    Action: {im.get('actionability', '')[:150]}"
        )

    return "\n".join(lines)
