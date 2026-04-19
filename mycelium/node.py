"""Core Reasoning Node — the fundamental primitive of Mycelium.

Every node runs the same 5-step loop:
1. SURVEY — inventory what's in scope
2. ORIENT — notice patterns, anomalies, gaps
3. HYPOTHESIZE — form suspicions from observations
4. ASSESS — decide what to resolve vs. delegate
5. PRODUCE — output observations and/or child directives

One LLM call per node. The prompt does all five steps.
The code is pure plumbing — it never makes exploration decisions.

The ONE exception: chain circuit breaker. If a node is 8+ levels into
a single-child chain, the prompt gets an override to force resolution.
This is a safety check, not an exploration decision.
"""

import json
import uuid
import anthropic
from .schemas import Directive, Observation, Source, NodeResult, Scope
from .prompts import NODE_REASONING_PROMPT, NODE_FORCE_RESOLVE_OVERRIDE

# Must match orchestrator.py
MAX_CHAIN_DEPTH = 8


async def run_node(directive: Directive, data_source, budget_remaining: float,
                   total_budget: float = 20.0, segment_context: str = "",
                   capacity_context: str = "") -> NodeResult:
    """Execute the 5-step reasoning loop for a single node.

    The code only: fetches data, formats for LLM, sends prompt,
    parses response, returns whatever the LLM decided.
    """
    node_id = directive.node_id
    pos = directive.tree_position

    # Fetch data for this node's scope
    documents = await data_source.fetch(directive.scope.filters, max_results=100)

    # Retry with broader filters if zero results (error recovery, not exploration)
    if not documents:
        broader_filters = dict(directive.scope.filters)
        for key in ["document_types", "agencies", "date_range"]:
            if key in broader_filters:
                del broader_filters[key]
                documents = await data_source.fetch(broader_filters, max_results=100)
                if documents:
                    break

        if not documents:
            return NodeResult(
                node_id=node_id, parent_id=directive.parent_id,
                scope_description=directive.scope.description,
                survey="No documents found even after broadening filters.",
                observations=[], child_directives=[],
                unresolved=[f"Scope '{directive.scope.description}' returned zero documents"],
                raw_reasoning="", token_usage={}, cost=0.0,
            )

    # Chain circuit breaker — the ONLY hardcoded exploration check
    force_resolve = ""
    if directive.chain_depth >= MAX_CHAIN_DEPTH:
        force_resolve = NODE_FORCE_RESOLVE_OVERRIDE.format(
            chain_depth=directive.chain_depth
        )

    # Format data — full abstracts for the LLM to read
    fetched_data = _format_documents(documents)
    lenses_str = ", ".join(directive.lenses)
    parent_ctx = directive.parent_context or "You are the first to enter. No prior context."

    budget_pct = (budget_remaining / total_budget * 100) if total_budget > 0 else 0

    # Natural language budget stage interpretation
    if budget_pct > 70:
        budget_stage = "EARLY STAGE — explore broadly and ambitiously. Decompose large spaces."
    elif budget_pct > 40:
        budget_stage = "MID EXPLORATION — balance breadth and depth. Decompose for high-signal areas."
    elif budget_pct > 15:
        budget_stage = "LATE STAGE — resolve what you have. Only decompose for very high-signal findings."
    else:
        budget_stage = "WRAPPING UP — resolve immediately with current observations."

    prompt = NODE_REASONING_PROMPT.format(
        parent_context=parent_ctx,
        scope_description=directive.scope.description,
        lenses=lenses_str,
        budget_remaining=budget_remaining,
        total_budget=total_budget,
        budget_pct=budget_pct,
        budget_stage=budget_stage,
        capacity_context=capacity_context or "",
        segment_context=segment_context or "",
        doc_count=len(documents),
        fetched_data=fetched_data,
        force_resolve=force_resolve,
    )

    # Send to LLM with extended thinking
    client = anthropic.Anthropic()
    response = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=16000,
        thinking={
            "type": "enabled",
            "budget_tokens": 5000,
        },
        messages=[{"role": "user", "content": prompt}],
    )

    # Extract thinking and text from response blocks
    thinking_text = ""
    raw_text = ""
    for block in response.content:
        if block.type == "thinking":
            thinking_text = block.thinking
        elif block.type == "text":
            raw_text = block.text

    usage = {
        "input_tokens": response.usage.input_tokens,
        "output_tokens": response.usage.output_tokens,
    }
    cost = (usage["input_tokens"] * 3 + usage["output_tokens"] * 15) / 1_000_000

    # Parse the JSON output (from the text block, not thinking)
    try:
        result = _parse_json(raw_text)
    except (json.JSONDecodeError, ValueError) as e:
        return NodeResult(
            node_id=node_id, parent_id=directive.parent_id,
            scope_description=directive.scope.description,
            survey="(parse error)", observations=[], child_directives=[],
            unresolved=[f"Parse error: {str(e)[:100]}"],
            raw_reasoning=raw_text, thinking=thinking_text,
            token_usage=usage, cost=cost,
        )

    # Build observations from LLM output
    observations = []
    for obs_data in result.get("observations", []):
        src = obs_data.get("source", {})
        observations.append(Observation(
            node_id=node_id,
            raw_evidence=obs_data.get("raw_evidence", obs_data.get("what_i_saw", "")),
            source=Source(
                doc_id=src.get("doc_id", ""),
                title=src.get("title", ""),
                agency=src.get("agency", ""),
                date=src.get("date", ""),
                section=src.get("section", ""),
                url=src.get("url", ""),
            ),
            observation_type=obs_data.get("observation_type", "pattern"),
            statistical_grounding=obs_data.get("statistical_grounding", ""),
            local_hypothesis=obs_data.get("local_hypothesis", obs_data.get("reasoning", "")),
            confidence=obs_data.get("confidence", 0.5),
            surprising_because=obs_data.get("surprising_because", ""),
        ))

    # Build child directives from LLM output
    child_directives = []
    raw_children = result.get("child_directives", [])

    # Single-child safety: if LLM spawned exactly 1 child despite the prompt
    # telling it not to, log it but still execute (the prompt should prevent this,
    # but if it doesn't, we track it for diagnostics)
    for i, child_data in enumerate(raw_children, 1):
        child_filters = child_data.get("filters", {})
        merged_filters = {**directive.scope.filters, **child_filters}

        # Track chain depth: if this node has exactly 1 child, increment chain
        new_chain_depth = (directive.chain_depth + 1) if len(raw_children) == 1 else 0

        child_directives.append(Directive(
            scope=Scope(
                source=directive.scope.source,
                filters=merged_filters,
                description=child_data.get("scope_description", ""),
            ),
            lenses=directive.lenses,
            parent_context=child_data.get("parent_context", ""),
            node_id=str(uuid.uuid4()),
            parent_id=node_id,
            tree_position=f"{pos}.{i}" if pos != "ROOT" else str(i),
            chain_depth=new_chain_depth,
        ))

    return NodeResult(
        node_id=node_id,
        parent_id=directive.parent_id,
        scope_description=directive.scope.description,
        survey=result.get("survey", ""),
        observations=observations,
        child_directives=child_directives,
        unresolved=result.get("unresolved", []),
        raw_reasoning=raw_text,
        thinking=thinking_text,
        token_usage=usage,
        cost=cost,
    )


def _format_documents(documents: list[dict]) -> str:
    """Format records for the LLM to read. Domain-agnostic — works with any fields."""
    lines = []
    for i, doc in enumerate(documents, 1):
        # Use title or first string field as the header
        title = doc.get("title", doc.get("name", doc.get("id", f"Record {i}")))
        header = f"[{i}] {title}"

        # Format all fields (skip very long values and nested structures)
        fields = []
        for key, val in doc.items():
            if key == "title" or key == "name":
                continue  # already in header
            if isinstance(val, (dict, list)):
                if isinstance(val, list) and len(val) <= 5:
                    fields.append(f"    {key}: {', '.join(str(v) for v in val)}")
                continue
            val_str = str(val)
            if len(val_str) > 300:
                val_str = val_str[:300] + "..."
            fields.append(f"    {key}: {val_str}")

        lines.append(header + "\n" + "\n".join(fields) + "\n")
    return "\n".join(lines)


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
