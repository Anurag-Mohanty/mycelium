"""Reader Test — scores each finding against the charter's standards.

Per-finding score (yes / marginal / no) with reasoning.
The question: would a knowledgeable reader say "I didn't know that"?

Corpus-agnostic — reads the charter from the run and scores against
whatever standards that charter sets.
"""

import json
import anthropic


READER_TEST_PROMPT = """\
You are evaluating a finding from an investigation team.

YOUR ROLE:
Name: {role_name}
Mission: {role_mission}
Bar (minimum to pass): {role_bar}
Heuristic: {role_heuristic}

ORGANIZATIONAL CHARTER:
{charter}

FINDING:
{finding}

EVIDENCE:
{evidence}

VALIDATION STATUS: {validation_status}

---

Evaluate this finding against your role's bar. The question is: does this \
finding clear your bar? Would the reader described in your role say "I \
didn't know that" or "this is worth knowing"?

NOVELTY CALIBRATION: Distinguish between CATEGORY AWARENESS and SPECIFIC \
ACTIONABLE INSTANCE. A practitioner who knows "single-maintainer packages \
exist" does NOT already know "lodash specifically has 580M downloads under \
one maintainer named jdalton." Category awareness is common knowledge. A \
specific, quantified, named instance with an actionable recommendation is \
novel even if the category is known. Score novel when the finding names \
specific entities, provides specific measurements, and enables a specific \
action the practitioner could not take from category knowledge alone.

Apply your role's heuristic when uncertain.

Return JSON:
{{
    "passes_bar": true,
    "combined_score": "yes | yes_factual | marginal | no",
    "reasoning": "why this finding passes or fails your bar",
    "what_practitioner_knows": "closest known fact to this finding",
    "what_is_new": "what this finding adds beyond existing knowledge",
    "elevation_recommendation": "headline | include | background | exclude"
}}

Respond ONLY with valid JSON, no other text.
"""


async def score_findings(charter: str, findings: list[dict],
                         role: dict = None) -> list[dict]:
    """Score each finding against the charter's standards.

    Args:
        charter: The organizational charter text
        findings: List of finding dicts with at minimum 'summary' and 'evidence'
        role: Authored role dict with name, mission, bar, heuristic

    Returns:
        List of score dicts with 'finding_id', 'score', 'reasoning'
    """
    client = anthropic.Anthropic()
    scores = []

    role = role or {}
    role_name = role.get("name", "knowledgeable reader")
    role_mission = role.get("mission", "evaluate whether findings add factual novelty")
    role_bar = role.get("bar", "finding must contain specific data a practitioner would not already know")
    role_heuristic = role.get("heuristic", "when uncertain, lean toward inclusion if evidence is specific")

    for i, finding in enumerate(findings):
        summary = finding.get("summary", finding.get("what_conflicts",
                  finding.get("pattern", str(finding)[:200])))
        evidence = finding.get("evidence", finding.get("evidence_chain", ""))
        if isinstance(evidence, list):
            evidence = "\n".join(str(e) for e in evidence)
        validation = finding.get("validation_status",
                    finding.get("verdict", "not validated"))

        prompt = READER_TEST_PROMPT.format(
            charter=charter,
            finding=summary,
            evidence=str(evidence)[:2000],
            validation_status=validation,
            role_name=role_name,
            role_mission=role_mission,
            role_bar=role_bar,
            role_heuristic=role_heuristic,
        )

        response = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=1000,
            messages=[{"role": "user", "content": prompt}],
        )

        raw = response.content[0].text
        cost = (response.usage.input_tokens * 3 + response.usage.output_tokens * 15) / 1_000_000

        try:
            result = json.loads(raw)
        except json.JSONDecodeError:
            start = raw.find("{")
            end = raw.rfind("}") + 1
            result = json.loads(raw[start:end]) if start >= 0 and end > start else {
                "combined_score": "no", "reasoning": "parse error"
            }

        combined = result.get("combined_score", result.get("score", "no"))
        scores.append({
            "finding_index": i,
            "finding_summary": str(summary)[:100],
            "score": combined,
            "factual_novelty": result.get("factual_novelty", ""),
            "interpretive_certainty": result.get("interpretive_certainty", ""),
            "reasoning": result.get("reasoning", ""),
            "what_practitioner_knows": result.get("what_practitioner_knows", ""),
            "what_is_new": result.get("what_is_new", ""),
            "elevation_recommendation": result.get("elevation_recommendation", ""),
            "cost": cost,
        })

    return scores


async def score_run(run_dir: str) -> dict:
    """Score all findings from a completed run."""
    import re
    from pathlib import Path

    run_path = Path(run_dir)

    # Load charter
    charter_path = run_path / "workspace" / "charter.md"
    if not charter_path.exists():
        return {"error": "no charter found", "scores": []}
    charter = charter_path.read_text()

    # Extract findings from the report (Tier 3-5 sections)
    report_path = run_path / "report.md"
    if not report_path.exists():
        return {"error": "no report found", "scores": []}

    report = report_path.read_text()
    findings = []

    # Parse Tier 3, 4, 5 findings from report markdown
    # Each finding starts with ### Finding N.N: or ### Pattern N.N:
    finding_pattern = re.compile(
        r'###\s+(?:Finding|Pattern)\s+\d+\.\d+[:\s]*(.*?)(?=\n###|\n## |\Z)',
        re.DOTALL
    )
    for match in finding_pattern.finditer(report):
        block = match.group(0)
        # Extract title and evidence from the block
        lines = block.strip().split('\n')
        title = lines[0] if lines else ""
        # Collect everything as evidence
        evidence_lines = [l for l in lines[1:] if l.strip() and not l.startswith('**Impact')]
        findings.append({
            "summary": title[:300],
            "evidence": "\n".join(evidence_lines[:15]),
            "validation_status": "validated",
        })

    # Fallback: if no structured findings found, try broader section parsing
    if not findings:
        for section in ["## Tier 3", "## Tier 4", "## Tier 5"]:
            idx = report.find(section)
            if idx >= 0:
                # Get content until next ## section
                next_section = report.find("\n## ", idx + len(section))
                section_text = report[idx:next_section] if next_section > 0 else report[idx:]
                if len(section_text) > 50:  # has real content
                    findings.append({
                        "summary": section_text[:300],
                        "evidence": section_text[:1000],
                        "validation_status": "validated",
                    })

    if not findings:
        return {"error": "no findings to score in report", "scores": []}

    scores = await score_findings(charter, findings[:10])  # Cap at 10 for cost

    return {
        "charter_length": len(charter),
        "findings_scored": len(scores),
        "scores": scores,
        "summary": {
            "yes": sum(1 for s in scores if s["score"] in ("yes", "yes_factual")),
            "yes_factual": sum(1 for s in scores if s["score"] == "yes_factual"),
            "marginal": sum(1 for s in scores if s["score"] == "marginal"),
            "no": sum(1 for s in scores if s["score"] == "no"),
        },
        "total_cost": sum(s["cost"] for s in scores),
    }
