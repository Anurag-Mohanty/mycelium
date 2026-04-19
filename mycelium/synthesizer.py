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
from .prompts import SYNTHESIS_PROMPT, SYNTHESIS_LIGHT_PROMPT


async def synthesize(parent_result: NodeResult, children_results: list[NodeResult],
                     lenses: list[str], light: bool = False) -> SynthesisResult:
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

    template = SYNTHESIS_LIGHT_PROMPT if light else SYNTHESIS_PROMPT
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

    synthesis = SynthesisResult(
        node_id=node_id,
        reinforced=result.get("reinforced", []),
        contradictions=result.get("contradictions", []),
        cross_cutting=result.get("cross_cutting_patterns", []),
        rescored_observations=all_observations,  # keep originals; rescoring is in the JSON
        discovered_questions=result.get("discovered_questions", []),
        unresolved_threads=result.get("unresolved_threads", []),
        raw_reasoning=raw_text,
        token_usage=usage,
        cost=cost,
    )

    return synthesis


def _format_investigator_reports(children: list[NodeResult]) -> str:
    """Format children's results into the investigator report format."""
    reports = []
    for i, child in enumerate(children, 1):
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

        reports.append(
            f"INVESTIGATOR {i} (assigned to: {child.scope_description}):\n"
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
