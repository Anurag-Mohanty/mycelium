"""Genesis Node — Phase F charter generation.

Reads corpus metadata, survey results, and briefing. Produces the
organizational charter in CEO directive voice. The charter sets purpose,
standards, and stakes for the entire investigation.
"""

import json
import random
import anthropic
from . import prompts as _prompts


async def run_genesis(data_source, hints: list[str] = None,
                      catalog_records: list[dict] = None,
                      survey_results: dict = None,
                      briefing_text: str = "") -> dict:
    """Generate the organizational charter for this investigation.

    Args:
        data_source: A DataSource instance
        hints: Optional user context
        catalog_records: Raw records from fetch_bulk_metadata (if available)
        survey_results: Statistical survey results from ProgrammaticSurvey
        briefing_text: Common knowledge briefing text

    Returns:
        dict with charter, corpus_summary, lenses (empty — replaced by charter),
        and token usage
    """
    if catalog_records and len(catalog_records) > 0:
        sample_size = min(200, len(catalog_records))
        sample = random.sample(catalog_records, sample_size)
        lightweight_sample = []
        for rec in sample:
            light = {k: v for k, v in rec.items()
                     if k not in ("risk_factors_text", "abstract", "dependencies",
                                  "dev_dependencies", "description")
                     or (isinstance(v, str) and len(v) < 200)}
            lightweight_sample.append(light)
        corpus_metadata = json.dumps({
            "source": data_source.__class__.__name__,
            "total_packages": f"{len(catalog_records)} cataloged (sample of {sample_size} shown)",
            "scope": "broad ecosystem survey",
            "packages": lightweight_sample,
        }, indent=2)
    else:
        survey = await data_source.survey({})
        corpus_metadata = json.dumps(survey, indent=2)

    # Build survey findings summary
    survey_findings_str = "No statistical survey available yet."
    if survey_results:
        lines = []
        lines.append(f"Records analyzed: {survey_results.get('record_count', '?')}")
        lines.append(f"Techniques applied: {', '.join(survey_results.get('techniques_applied', []))}")
        lines.append(f"Outliers found: {len(survey_results.get('outliers', []))}")
        lines.append(f"Concentrations: {len(survey_results.get('concentrations', []))}")
        clusters = survey_results.get("anomaly_clusters", [])
        if clusters:
            lines.append(f"Multi-flagged anomaly clusters: {len(clusters)}")
            for c in clusters[:10]:
                lines.append(f"  - [{c.get('severity', '?')}] {c.get('name', c.get('description', '?'))}")
        by_tech = survey_results.get("anomalies_by_technique", {})
        for tech, data in by_tech.items():
            if isinstance(data, dict):
                count = len(data.get("anomalies", []))
                if count:
                    lines.append(f"{tech}: {count} anomalies")
        survey_findings_str = "\n".join(lines)

    # Use briefing if provided, otherwise fall back
    if not briefing_text:
        briefing_text = "(No existing briefing available — use survey findings as proxy for common knowledge.)"

    import datetime
    prompt = _prompts.CHARTER_PROMPT.format(
        current_date=datetime.date.today().isoformat(),
        corpus_metadata=corpus_metadata,
        survey_findings=survey_findings_str,
        briefing=briefing_text,
        budget=10.0,  # charter doesn't need exact budget — it sets standards
    )

    client = anthropic.Anthropic()
    response = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=2000,
        messages=[{"role": "user", "content": prompt}],
    )

    charter = response.content[0].text
    usage = {
        "input_tokens": response.usage.input_tokens,
        "output_tokens": response.usage.output_tokens,
    }
    cost = (usage["input_tokens"] * 3 + usage["output_tokens"] * 15) / 1_000_000

    # Return with backward-compat fields for downstream pipeline
    return {
        "charter": charter,
        # Backward compat — downstream code references these
        "corpus_summary": f"Phase F charter generated ({len(charter.split())} words)",
        "lenses": [],  # replaced by charter directive
        "suggested_entry_points": [],
        "natural_structure": {},
        "token_usage": usage,
        "cost": cost,
    }
