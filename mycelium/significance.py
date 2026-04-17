"""Significance Gate — filters findings before impact analysis.

Domain-agnostic "so what?" filter. Prevents obvious, known, or unactionable
findings from getting expensive impact analysis. Scores on novelty and
actionability. Only findings scoring 3.0+ proceed to impact analysis.
"""

import json
import anthropic
from .prompts import SIGNIFICANCE_PROMPT


async def assess_significance(finding_id: str, finding: dict,
                               validation: dict) -> dict:
    """Score a validated finding on novelty and actionability.

    Args:
        finding_id: Unique ID
        finding: The original finding dict
        validation: The validation result dict

    Returns:
        dict with scores, tier assignment, headline, recommendation
    """
    finding_desc = (validation.get("revised_finding")
                    or finding.get("what_conflicts", "")
                    or finding.get("pattern", ""))
    evidence = json.dumps(finding, indent=2, default=str)

    prompt = SIGNIFICANCE_PROMPT.format(
        finding=finding_desc,
        evidence=evidence,
        validation_status=validation.get("verdict", "unknown"),
    )

    client = anthropic.Anthropic()
    response = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=1500,
        messages=[{"role": "user", "content": prompt}],
    )

    raw_text = response.content[0].text
    usage = {
        "input_tokens": response.usage.input_tokens,
        "output_tokens": response.usage.output_tokens,
    }
    cost = (usage["input_tokens"] * 3 + usage["output_tokens"] * 15) / 1_000_000

    try:
        result = _parse_json(raw_text)
    except (json.JSONDecodeError, ValueError):
        result = {
            "genuine": True, "novelty": 2, "actionability": 2,
            "composite_score": 2.0, "tier_assignment": "noted",
            "headline": finding_desc[:100],
            "recommendation": "note_only",
        }

    result["finding_id"] = finding_id
    result["cost"] = cost
    result["token_usage"] = usage
    return result


def _parse_json(text: str) -> dict:
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    if "```json" in text:
        s = text.find("```json") + 7
        e = text.find("```", s)
        if e > s:
            return json.loads(text[s:e].strip())
    s = text.find("{")
    e = text.rfind("}") + 1
    if s >= 0 and e > s:
        return json.loads(text[s:e])
    raise ValueError("Could not extract JSON")
