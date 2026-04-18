"""WorkerNode — persistent agent that lives across multiple LLM calls.

Unlike node.py (single LLM call, fire and forget), a WorkerNode:
- Turn 1: Surveys data, produces observations, decides to delegate or resolve
- Waits for children to complete (if delegating)
- Turn 2: Reviews children's results, synthesizes, redistributes budget, spawns follow-ups
- Turn 3: Final report to parent

Each turn uses extended thinking so reasoning is genuine and visible.
The code is pure plumbing — the LLM makes all decisions about
delegation, budget allocation, and follow-up investigations.
"""

import asyncio
import json
import uuid
import anthropic
from .schemas import Directive, Observation, Source, NodeResult, Scope
from .prompts import NODE_REASONING_PROMPT, NODE_FORCE_RESOLVE_OVERRIDE
from . import events

MAX_CHAIN_DEPTH = 8


class WorkerNode:
    """A persistent agent that manages its own work across multiple LLM turns."""

    def __init__(self, directive: Directive, data_source, budget: float,
                 total_budget: float, parent_worker=None, lenses: list[str] = None,
                 semaphore: asyncio.Semaphore = None, budget_pool=None):
        self.directive = directive
        self.data_source = data_source
        self.budget = budget          # my allocation
        self.total_budget = total_budget
        self.spent = 0.0
        self.parent_worker = parent_worker
        self.lenses = lenses or []
        self._semaphore = semaphore or asyncio.Semaphore(3)
        self._budget_pool = budget_pool  # shared pool — check before every LLM call

        self.node_id = directive.node_id
        self.pos = directive.tree_position
        self.segment_id = directive.segment_id

        self.observations = []
        self.child_workers = []
        self.child_results = []
        self.findings = []            # from Turn 2 synthesis
        self.thinking_log = []        # all thinking blocks across turns
        self.status = "created"

    @property
    def surplus(self) -> float:
        children_spent = sum(c.spent for c in self.child_workers)
        return max(0, self.budget - self.spent - children_spent)

    # === Main lifecycle ===

    async def run(self) -> dict:
        """Execute the full worker lifecycle: explore → delegate → review → report."""
        self.status = "exploring"

        # --- TURN 1: Survey + Orient + Assess ---
        events.emit("node_spawned", {
            "node_id": self.node_id, "parent_id": self.directive.parent_id,
            "tree_position": self.pos, "scope_summary": self.directive.scope.description[:100],
            "segment_id": self.segment_id,
        })

        turn1 = await self._turn_initial()
        if turn1 is None:
            # Budget exhausted or data fetch failed
            self.status = "resolved"
            events.emit("node_resolved", {
                "node_id": self.node_id, "tree_position": self.pos,
                "observations_count": 0, "cost_spent": self.spent,
                "top_observation": "",
            })
            return self._result()

        # Collect observations from Turn 1
        self.observations = turn1.get("observations", [])
        child_directives = turn1.get("child_directives", [])

        n_obs = len(self.observations)
        n_children = len(child_directives)

        events.emit("budget_update", {
            "total_spent": self._estimate_total_spent(),
            "total_remaining": self.total_budget - self._estimate_total_spent(),
            "percent_used": (self._estimate_total_spent() / self.total_budget * 100) if self.total_budget > 0 else 0,
        })

        if not child_directives:
            # Resolved — no delegation needed
            self.status = "resolved"
            top_obs = self.observations[0]["what_i_saw"][:80] if self.observations else ""
            events.emit("node_resolved", {
                "node_id": self.node_id, "tree_position": self.pos,
                "observations_count": n_obs, "cost_spent": self.spent,
                "top_observation": top_obs,
            })
            self._log(f"RESOLVED: {n_obs} observations")
            return self._result()

        # --- DELEGATION: spawn child workers ---
        self.status = "delegating"
        events.emit("node_decomposing", {
            "node_id": self.node_id, "tree_position": self.pos,
            "children_count": n_children, "observations_count": n_obs,
        })
        self._log(f"{n_obs} observations + delegating → {n_children} workers")

        # Allocate budget to children
        remaining_for_children = self.budget - self.spent - 0.10  # reserve $0.10 for Turn 2
        child_budget_each = remaining_for_children / max(1, n_children)

        for i, cd in enumerate(child_directives, 1):
            child_budget = cd.get("budget", child_budget_each)
            child_budget = min(child_budget, remaining_for_children / max(1, n_children - i + 1))

            child_directive = Directive(
                scope=Scope(
                    source=self.directive.scope.source,
                    filters={**self.directive.scope.filters, **cd.get("filters", {})},
                    description=cd.get("scope_description", ""),
                ),
                lenses=self.lenses,
                parent_context=cd.get("parent_context", ""),
                node_id=str(uuid.uuid4()),
                parent_id=self.node_id,
                tree_position=f"{self.pos}.{i}" if self.pos != "ROOT" else str(i),
                chain_depth=(self.directive.chain_depth + 1) if n_children == 1 else 0,
                segment_id=self.segment_id,
                survey_anomalies=self.directive.survey_anomalies,
            )

            child = WorkerNode(
                directive=child_directive,
                data_source=self.data_source,
                budget=child_budget,
                total_budget=self.total_budget,
                parent_worker=self,
                lenses=self.lenses,
                semaphore=self._semaphore,
                budget_pool=self._budget_pool,
            )
            self.child_workers.append(child)

        # Run children concurrently
        self.status = "waiting_for_children"
        self.child_results = await asyncio.gather(
            *[child.run() for child in self.child_workers],
            return_exceptions=True,
        )

        # Filter out exceptions
        self.child_results = [
            r for r in self.child_results if isinstance(r, dict)
        ]

        # --- TURN 2: Review children's work ---
        if self.child_results and self.surplus > 0.02:
            self.status = "reviewing"
            events.emit("node_status", {
                "node_id": self.node_id, "tree_position": self.pos,
                "status": "reviewing",
            })
            self._log("REVIEWING children's work...")

            turn2 = await self._turn_review()

            if turn2:
                # Collect synthesis findings
                self.findings = turn2.get("findings", [])

                # Handle follow-up children
                followups = turn2.get("followup_children", [])
                if followups and self.surplus > 0.05:
                    self._log(f"Spawning {len(followups)} follow-up investigations")
                    await self._run_followups(followups)

                # Emit synthesis events
                for f in self.findings:
                    events.emit("finding_discovered", {
                        "node_id": self.node_id,
                        "summary": str(f.get("summary", ""))[:80],
                        "type": f.get("type", "pattern"),
                    })

        # --- Report to parent ---
        self.status = "complete"
        surplus = self.surplus
        if surplus > 0.01:
            events.emit("budget_returned", {
                "from_node": self.node_id,
                "to_node": self.directive.parent_id or "root",
                "amount": round(surplus, 3),
                "reason": "work complete, returning surplus",
            })
            self._log(f"Returning ${surplus:.3f} surplus to parent")

        return self._result()

    # === Turn 1: Initial exploration ===

    async def _turn_initial(self) -> dict | None:
        """First LLM call: survey the data, produce observations, decide to delegate."""

        # Fetch data
        documents = await self.data_source.fetch(self.directive.scope.filters, max_results=100)

        # Retry with broader filters if zero results
        if not documents:
            broader_filters = dict(self.directive.scope.filters)
            for key in ["document_types", "agencies", "date_range"]:
                if key in broader_filters:
                    del broader_filters[key]
                    documents = await self.data_source.fetch(broader_filters, max_results=100)
                    if documents:
                        break

        if not documents:
            return None

        # Chain circuit breaker
        force_resolve = ""
        if self.directive.chain_depth >= MAX_CHAIN_DEPTH:
            force_resolve = NODE_FORCE_RESOLVE_OVERRIDE.format(
                chain_depth=self.directive.chain_depth)

        # Format data
        fetched_data = _format_documents(documents)
        lenses_str = ", ".join(self.lenses)
        parent_ctx = self.directive.parent_context or "You are the first to enter. No prior context."

        # Budget context: show the POOL total for ambition, but use the worker's
        # own allocation percentage for the stage signal. This matches the old
        # _explore_node behavior where each node saw the pool's remaining balance.
        remaining_own = max(0, self.budget - self.spent)
        budget_pct = (remaining_own / self.budget * 100) if self.budget > 0 else 0
        if budget_pct > 70:
            budget_stage = "EARLY STAGE — explore broadly and ambitiously. Decompose large spaces."
        elif budget_pct > 40:
            budget_stage = "MID EXPLORATION — balance breadth and depth. Decompose for high-signal areas."
        elif budget_pct > 15:
            budget_stage = "LATE STAGE — resolve what you have. Only decompose for very high-signal findings."
        else:
            budget_stage = "WRAPPING UP — resolve immediately with current observations."

        est_nodes = int(remaining_own / 0.05)
        capacity = (
            f"At ~$0.05/node, you can afford ~{est_nodes} more reasoning steps. "
            f"Children that focus on subsets cost ~$0.04 each."
        ) if est_nodes >= 2 else ""

        # Build anomaly context from survey data
        anomaly_ctx = _format_anomalies(self.directive.survey_anomalies, documents)

        prompt = NODE_REASONING_PROMPT.format(
            parent_context=parent_ctx,
            scope_description=self.directive.scope.description,
            lenses=lenses_str,
            budget_remaining=remaining_own,
            total_budget=self.budget,
            budget_pct=budget_pct,
            budget_stage=budget_stage,
            capacity_context=capacity,
            segment_context=f"- This segment is part of a ${self.total_budget:.2f} exploration pool.\n",
            doc_count=len(documents),
            fetched_data=anomaly_ctx + fetched_data,
            force_resolve=force_resolve,
        )

        # Budget gate: check shared pool before LLM call
        if self._budget_pool and self._budget_pool.exploration_exhausted:
            return None

        # LLM call with extended thinking
        async with self._semaphore:
            thinking, output, cost = await _call_llm(prompt)

        self.spent += cost
        if self._budget_pool:
            self._budget_pool.record("exploration", cost)
        self.thinking_log.append({"turn": "initial", "thinking": thinking})

        # Emit thinking
        _emit_thinking_chunks(self.node_id, "initial", thinking)

        # Parse output
        try:
            result = _parse_json(output)
        except (json.JSONDecodeError, ValueError):
            return {"observations": [], "child_directives": []}

        # Build Observation objects
        observations = []
        for obs_data in result.get("observations", []):
            src = obs_data.get("source", {})
            observations.append({
                "what_i_saw": obs_data.get("what_i_saw", ""),
                "source": src,
                "observation_type": obs_data.get("observation_type", "pattern"),
                "preliminary_relevance": obs_data.get("preliminary_relevance", {}),
                "reasoning": obs_data.get("reasoning", ""),
                "potential_connections": obs_data.get("potential_connections", []),
            })

        return {
            "survey": result.get("survey", ""),
            "observations": observations,
            "child_directives": result.get("child_directives", []),
            "unresolved": result.get("unresolved", []),
        }

    # === Turn 2: Review children ===

    async def _turn_review(self) -> dict | None:
        """Second LLM call: review children's results, synthesize, decide follow-ups."""

        # Build children summary for the prompt
        children_summary = []
        for child, result in zip(self.child_workers, self.child_results):
            if not isinstance(result, dict):
                continue
            children_summary.append({
                "scope": child.directive.scope.description[:100],
                "observations": result.get("observations", [])[:5],
                "findings": result.get("findings", []),
                "cost": round(child.spent, 3),
                "surplus": round(child.surplus, 3),
                "status": child.status,
                "thinking_excerpt": child.thinking_log[0]["thinking"][:200] if child.thinking_log else "",
            })

        if not children_summary:
            return None

        prompt = _build_review_prompt(
            my_observations=self.observations,
            children_summary=children_summary,
            budget_remaining=self.surplus,
            total_budget=self.budget,
        )

        # Budget gate
        if self._budget_pool and self._budget_pool.exploration_exhausted:
            return None

        async with self._semaphore:
            thinking, output, cost = await _call_llm(prompt)

        self.spent += cost
        if self._budget_pool:
            self._budget_pool.record("exploration", cost)
        self.thinking_log.append({"turn": "review", "thinking": thinking})

        _emit_thinking_chunks(self.node_id, "review", thinking)

        try:
            return _parse_json(output)
        except (json.JSONDecodeError, ValueError):
            return None

    # === Follow-up children ===

    async def _run_followups(self, followup_directives: list):
        """Spawn follow-up children from Turn 2 review decisions."""
        for i, fd in enumerate(followup_directives, 1):
            if self.surplus < 0.03:
                break

            child_budget = min(fd.get("budget", 0.10), self.surplus * 0.8)
            child_directive = Directive(
                scope=Scope(
                    source=self.directive.scope.source,
                    filters=fd.get("filters", {"keyword": fd.get("scope_description", "")[:30]}),
                    description=fd.get("scope_description", ""),
                ),
                lenses=self.lenses,
                parent_context=fd.get("parent_context", "Follow-up investigation"),
                node_id=str(uuid.uuid4()),
                parent_id=self.node_id,
                tree_position=f"{self.pos}.F{i}",
                segment_id=self.segment_id,
                survey_anomalies=self.directive.survey_anomalies,
            )

            child = WorkerNode(
                directive=child_directive,
                data_source=self.data_source,
                budget=child_budget,
                total_budget=self.total_budget,
                parent_worker=self,
                lenses=self.lenses,
                semaphore=self._semaphore,
                budget_pool=self._budget_pool,
            )
            self.child_workers.append(child)

            events.emit("followup_spawned", {
                "parent_node": self.node_id,
                "child_node": child.node_id,
                "reason": fd.get("parent_context", "")[:100],
                "budget": round(child_budget, 3),
            })

        # Run follow-ups
        followup_results = await asyncio.gather(
            *[c.run() for c in self.child_workers if c.status == "created"],
            return_exceptions=True,
        )
        self.child_results.extend(
            r for r in followup_results if isinstance(r, dict)
        )

    # === Helpers ===

    def _log(self, msg: str):
        depth = self.pos.count(".") if self.pos != "ROOT" else 0
        indent = "│ " * depth
        print(f"  {indent}[{self.pos}] {msg}")

    def _estimate_total_spent(self) -> float:
        """Rough estimate of total pipeline spend (for budget_update events)."""
        return self.spent + sum(c.spent for c in self.child_workers)

    def _result(self) -> dict:
        """Package results for parent or orchestrator."""
        return {
            "node_id": self.node_id,
            "parent_id": self.directive.parent_id,
            "scope_description": self.directive.scope.description,
            "observations": self.observations,
            "findings": self.findings,
            "thinking_log": self.thinking_log,
            "cost": self.spent,
            "surplus": self.surplus,
            "children_count": len(self.child_workers),
            "child_results": self.child_results,
            "status": self.status,
        }


# === Module-level helpers (no domain-specific logic) ===

async def _call_llm(prompt: str, thinking_budget: int = 5000) -> tuple[str, str, float]:
    """Make one LLM call with extended thinking. Returns (thinking, output, cost)."""
    client = anthropic.Anthropic()
    response = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=16000,
        thinking={"type": "enabled", "budget_tokens": thinking_budget},
        messages=[{"role": "user", "content": prompt}],
    )

    thinking = ""
    output = ""
    for block in response.content:
        if block.type == "thinking":
            thinking = block.thinking
        elif block.type == "text":
            output = block.text

    cost = (response.usage.input_tokens * 3 + response.usage.output_tokens * 15) / 1_000_000
    return thinking, output, cost


def _emit_thinking_chunks(node_id: str, turn: str, thinking: str):
    """Split thinking into chunks and emit for visualizer."""
    if not thinking:
        return
    paragraphs = [p.strip() for p in thinking.split("\n\n") if p.strip()]
    chunks = []
    current = ""
    for p in paragraphs:
        if len(current) + len(p) < 350:
            current += ("\n" + p if current else p)
        else:
            if current:
                chunks.append(current)
            current = p
    if current:
        chunks.append(current)
    if not chunks:
        chunks = [thinking[:300]]

    for i, chunk in enumerate(chunks):
        events.emit("node_thinking", {
            "node_id": node_id,
            "turn": turn,
            "chunk_index": i,
            "total_chunks": len(chunks),
            "text": chunk[:300],
        })


def _build_review_prompt(my_observations: list, children_summary: list,
                          budget_remaining: float, total_budget: float) -> str:
    """Build the Turn 2 review prompt."""
    obs_text = json.dumps(my_observations[:10], indent=2, default=str)[:2000]
    children_text = json.dumps(children_summary, indent=2, default=str)[:4000]

    return f"""Your workers have reported back. Review their results and decide what to do next.

YOUR ORIGINAL OBSERVATIONS:
{obs_text}

WORKER REPORTS:
{children_text}

BUDGET STATUS:
  Your remaining budget: ${budget_remaining:.2f}
  Your total allocation: ${total_budget:.2f}

Now reason about what your team found:

1. SYNTHESIZE: What patterns connect findings across workers? What did one worker
   find that changes the meaning of another's findings?

2. EVALUATE: Which findings are strongest? Which workers found rich veins vs dead ends?

3. RESOURCE DECISION: You have ${budget_remaining:.2f} left. Options:
   a) Spawn follow-up workers to trace specific findings deeper
   b) Return surplus to your manager (if coverage is sufficient)

4. FINDINGS: What cross-cutting findings emerge from synthesizing all workers' observations?

Return JSON:
{{
    "synthesis": {{
        "patterns": ["cross-cutting pattern descriptions"],
        "contradictions": ["things that conflict"],
        "strongest_findings": ["what's most solid"],
        "weakest_findings": ["what's speculative"]
    }},
    "findings": [
        {{
            "type": "contradiction|gap|cross_cutting",
            "summary": "description",
            "evidence": ["from which workers"],
            "confidence": 0.8
        }}
    ],
    "followup_children": [
        {{
            "scope_description": "what to investigate next",
            "parent_context": "why this follow-up is needed",
            "budget": 0.10
        }}
    ],
    "surplus_to_return": 0.00,
    "surplus_reason": "why returning this amount"
}}

Respond ONLY with valid JSON, no other text."""


def _format_anomalies(anomalies: list[dict], documents: list[dict]) -> str:
    """Format survey anomalies relevant to the fetched documents.

    Filters anomalies to those referencing records in the current scope,
    then formats as a text block prepended to the data. Domain-agnostic.
    """
    if not anomalies:
        return ""

    # Build set of record identifiers from fetched documents
    doc_ids = set()
    for doc in documents:
        for key in ("name", "id", "title"):
            val = doc.get(key)
            if val:
                doc_ids.add(str(val).lower())

    # Filter anomalies to those matching fetched records or scope-wide stats
    relevant = []
    for a in anomalies:
        record = str(a.get("record", "")).lower()
        # Include if it references a record we fetched, or if it's a
        # scope-wide statistical finding (concentration, unusual combo)
        if record in doc_ids or a.get("type") in ("concentration", "unusual_combination"):
            relevant.append(a)

    if not relevant:
        return ""

    lines = ["STATISTICAL ANOMALIES IN YOUR SCOPE (from programmatic survey):"]
    for a in relevant[:10]:
        if a["type"] == "outlier":
            lines.append(
                f"  - {a.get('record', '?')}: {a.get('field', '?')} = {a.get('value', '?')} "
                f"(z-score {a.get('z_score', '?')}, {a.get('direction', '?')} outlier)"
            )
        elif a["type"] == "unusual_combination":
            lines.append(f"  - {a.get('description', '?')}")
        elif a["type"] == "concentration":
            lines.append(f"  - {a.get('field', '?')}: {a.get('description', '?')}")

    lines.append(
        "\nThe statistical survey flagged these anomalies in your scope. "
        "Investigate them — determine whether they're genuinely surprising "
        "or easily explained.\n\n"
    )
    return "\n".join(lines)


def _format_documents(documents: list[dict]) -> str:
    """Format records for the LLM. Domain-agnostic."""
    lines = []
    for i, doc in enumerate(documents, 1):
        title = doc.get("title", doc.get("name", doc.get("id", f"Record {i}")))
        header = f"[{i}] {title}"
        fields = []
        for key, val in doc.items():
            if key in ("title", "name"):
                continue
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
    """Extract JSON from LLM output."""
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
    raise ValueError("Could not extract JSON")
