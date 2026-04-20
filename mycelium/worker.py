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
        self.metrics = {}             # purpose_addressed, evidence_quality, budget_efficiency
        self.token_usage = {"input_tokens": 0, "output_tokens": 0}
        self.status = "created"
        self._diagnostics = {}        # raw data for diagnostic log

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
            top_obs = self.observations[0].get("raw_evidence", "")[:80] if self.observations else ""
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
                purpose=cd.get("purpose", cd.get("hypothesis", "")),
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

                # Honor the LLM's continue/resolve decision
                decision = turn2.get("continue_or_resolve", "resolve")
                followups = turn2.get("followup_children", [])
                if decision == "continue" and followups and self.surplus > 0.05:
                    self._log(f"Spawning {len(followups)} follow-up investigations")
                    await self._run_followups(followups)
                elif followups:
                    reasoning = turn2.get("continue_reasoning", "resolving with current evidence")
                    self._log(f"RESOLVING (LLM decided): {reasoning[:80]}")

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

        # Validate filters against data source schema before fetching
        schema = self.data_source.filter_schema() if hasattr(self.data_source, 'filter_schema') else {}
        filters = dict(self.directive.scope.filters)
        if schema and filters:
            valid_params = set(schema.keys())
            unknown = [k for k in filters if k not in valid_params]
            if unknown:
                self._log(f"Filter validation: unknown params {unknown} removed")
                filters = {k: v for k, v in filters.items() if k in valid_params}

        # Fetch data — no retry heuristics. If filter produces 0 records,
        # the node resolves with 0 records and self-eval states what happened.
        # The parent's Turn 2 decides whether to respawn with a different filter.
        documents = await self.data_source.fetch(filters, max_results=100)

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

        # Store diagnostic data
        self._diagnostics["data_received"] = {
            "record_count": len(documents),
            "fields_present": list(documents[0].keys()) if documents else [],
            "avg_text_length": int(sum(
                len(str(d.get("risk_factors_text", d.get("description", ""))))
                for d in documents
            ) / max(1, len(documents))),
            "sample_record_summary": str(documents[0])[:100] if documents else "",
        }
        # Parse anomaly targets that were actually sent to the agent
        raw_anomalies = self.directive.survey_anomalies or []
        self._diagnostics["anomaly_targets_received"] = {
            "count": len(raw_anomalies),
            "targets": [
                {
                    "type": a.get("type", "?"),
                    "description": str(a.get("description", ""))[:200],
                    "has_evidence": bool(a.get("evidence")),
                    "evidence_keys": list(a.get("evidence", {}).keys()) if isinstance(a.get("evidence"), dict) else [],
                }
                for a in raw_anomalies[:10]
            ],
        }

        purpose = self.directive.purpose or "Investigate the data in your scope and report what you find."

        # Format filter schema for the prompt
        schema = self.data_source.filter_schema() if hasattr(self.data_source, 'filter_schema') else {}
        if schema:
            schema_lines = []
            for param, info in schema.items():
                schema_lines.append(
                    f"  {param} ({info.get('type', 'string')}): {info.get('description', '')}"
                    f" Example: {info.get('example', 'N/A')}"
                )
            filter_schema_str = "\n".join(schema_lines)
        else:
            filter_schema_str = "No schema available."

        import datetime
        prompt = NODE_REASONING_PROMPT.format(
            current_date=datetime.date.today().isoformat(),
            purpose=purpose,
            parent_context=parent_ctx,
            scope_description=self.directive.scope.description,
            lenses=lenses_str,
            filter_schema=filter_schema_str,
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
            thinking, output, cost, usage = await _call_llm(prompt)

        self.spent += cost
        self.token_usage["input_tokens"] += usage["input_tokens"]
        self.token_usage["output_tokens"] += usage["output_tokens"]
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

        # Build evidence packet dicts
        observations = []
        for obs_data in result.get("observations", []):
            src = obs_data.get("source", {})
            observations.append({
                "raw_evidence": obs_data.get("raw_evidence", obs_data.get("what_i_saw", "")),
                "statistical_grounding": obs_data.get("statistical_grounding", ""),
                "local_hypothesis": obs_data.get("local_hypothesis", obs_data.get("reasoning", "")),
                "source": src,
                "observation_type": obs_data.get("observation_type", "pattern"),
                "confidence": obs_data.get("confidence", 0.5),
                "surprising_because": obs_data.get("surprising_because", ""),
            })

        # Capture self-evaluation
        self_eval = result.get("self_evaluation", {})
        self.metrics = {
            "purpose_addressed": self_eval.get("purpose_addressed", True),
            "purpose_gap": self_eval.get("purpose_gap", ""),
            "evidence_quality": self_eval.get("evidence_quality", "medium"),
            "budget_efficiency": 0.0,  # computed after all work completes
        }

        return {
            "survey": result.get("survey", ""),
            "observations": observations,
            "child_directives": result.get("child_directives", []),
            "unresolved": result.get("unresolved", []),
            "self_evaluation": self_eval,
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
            thinking, output, cost, usage = await _call_llm(prompt)

        self.spent += cost
        self.token_usage["input_tokens"] += usage["input_tokens"]
        self.token_usage["output_tokens"] += usage["output_tokens"]
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
                purpose=fd.get("purpose", fd.get("parent_context", "Follow-up investigation")),
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

    def _build_diagnostic(self) -> dict:
        """Build the diagnostic log object from current node state."""
        # Classify observations
        obs_with_evidence = 0
        obs_generic = 0
        for obs in self.observations:
            raw = obs.get("raw_evidence", "")
            # Heuristic: if it contains numbers or quotes specific values, it's evidence-cited
            has_numbers = any(c.isdigit() for c in raw)
            if has_numbers and len(raw) > 50:
                obs_with_evidence += 1
            else:
                obs_generic += 1

        # Extract ASSESS reasoning from thinking
        assess_reasoning = ""
        if self.thinking_log:
            thinking = self.thinking_log[0].get("thinking", "")
            # Look for STEP 4 / ASSESS section
            for marker in ["STEP 4", "ASSESS", "coverage"]:
                idx = thinking.lower().find(marker.lower())
                if idx >= 0:
                    assess_reasoning = thinking[idx:idx + 300]
                    break

        self_eval = self.metrics or {}
        default_data = {"record_count": 0, "fields_present": [], "avg_text_length": 0, "sample_record_summary": ""}
        default_targets = {"count": 0, "targets": []}

        return {
            "node_id": self.node_id,
            "tree_position": self.pos,
            "scope": self.directive.scope.description[:200],
            "purpose": (self.directive.purpose or "")[:200],
            "data_received": self._diagnostics.get("data_received", default_data),
            "anomaly_targets_received": self._diagnostics.get("anomaly_targets_received", default_targets),
            "thinking_summary": self.thinking_log[0]["thinking"][:500] if self.thinking_log else "",
            "output": {
                "observations_count": len(self.observations),
                "children_spawned": len(self.child_workers),
                "evidence_cited": obs_with_evidence,
                "generic_description": obs_generic,
                "sample_observation": self.observations[0] if self.observations else None,
            },
            "self_evaluation": {
                "purpose_addressed": self_eval.get("purpose_addressed", None),
                "evidence_quality": self_eval.get("evidence_quality", None),
                "purpose_gap": self_eval.get("purpose_gap", ""),
            },
            "budget": {
                "allocated": round(self.budget, 3),
                "spent": round(self.spent, 3),
                "returned": round(self.surplus, 3),
            },
            "decision": "decomposed" if self.child_workers else "resolved",
            "decision_reasoning": assess_reasoning[:300],
        }

    def _result(self) -> dict:
        """Package results for parent or orchestrator."""
        # Compute budget efficiency
        total_tree_spent = self._estimate_total_spent()
        if self.budget > 0:
            self.metrics["budget_efficiency"] = round(total_tree_spent / self.budget * 100, 1)

        return {
            "node_id": self.node_id,
            "parent_id": self.directive.parent_id,
            "scope_description": self.directive.scope.description,
            "purpose": self.directive.purpose,
            "observations": self.observations,
            "findings": self.findings,
            "thinking_log": self.thinking_log,
            "cost": self.spent,
            "surplus": self.surplus,
            "children_count": len(self.child_workers),
            "child_results": self.child_results,
            "status": self.status,
            "metrics": self.metrics,
            "token_usage": self.token_usage,
            "diagnostic": self._build_diagnostic(),
        }


# === Module-level helpers (no domain-specific logic) ===

async def _call_llm(prompt: str, thinking_budget: int = 5000) -> tuple[str, str, float, dict]:
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

    input_tokens = response.usage.input_tokens
    output_tokens = response.usage.output_tokens
    cost = (input_tokens * 3 + output_tokens * 15) / 1_000_000
    usage = {"input_tokens": input_tokens, "output_tokens": output_tokens}
    return thinking, output, cost, usage


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

1. PURPOSE REVIEW: For each worker, assess whether its output addressed the purpose \
you gave it. Did it deliver what you needed, or did it go off track? Workers that \
went off track should not be incorporated into synthesis — note them as wasted budget.

2. SYNTHESIZE: What patterns connect findings across workers that DID address their \
purpose? What did one worker find that changes the meaning of another's findings?

3. EVALUATE: Which findings are strongest? Which workers found rich veins vs dead ends?

4. CONTINUE OR RESOLVE: Before deciding to spawn more workers, honestly assess: \
given what my children just returned, does the accumulated evidence justify \
continuing to decompose this line of investigation?

Consider:
- How many of my children returned substantive observations with specific evidence?
- How many returned zero records or zero observations?
- How many self-evaluated as having gaps — unable to address their purpose?
- Is there a specific thread with strong evidence that would benefit from \
one more level of depth? Or am I chasing data that can't be fetched?

If most children returned empty or low-signal results, RESOLVE with what you have. \
Synthesize the evidence that exists and return it. Do not spawn more children \
hoping the next level will find what this level couldn't.

Only spawn follow-up children if: a specific child returned STRONG evidence that \
opens a concrete new question, AND you have budget to pursue it.

5. RESOURCE DECISION: You have ${budget_remaining:.2f} left. Based on your assessment above:
   a) If continuing: spawn follow-up workers for SPECIFIC strong evidence threads only
   b) If resolving: return surplus to your manager

6. FINDINGS: What cross-cutting findings emerge from synthesizing all workers' observations?

Return JSON:
{{
    "continue_or_resolve": "continue | resolve",
    "continue_reasoning": "one sentence: why you are continuing to decompose OR why you are resolving with current evidence",
    "worker_reviews": [
        {{
            "worker_scope": "what this worker was asked to investigate",
            "purpose_aligned": true,
            "assessment": "did this worker deliver what I needed? one sentence."
        }}
    ],
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
            "purpose": "why this follow-up is needed and what you need from it",
            "parent_context": "the specific evidence that motivated this",
            "budget": 0.10
        }}
    ],
    "surplus_to_return": 0.00,
    "surplus_reason": "why returning this amount"
}}

Respond ONLY with valid JSON, no other text."""


def _format_anomalies(anomalies: list[dict], documents: list[dict]) -> str:
    """Format survey anomalies as INVESTIGATION TARGETS — the agent's primary job.

    Filters anomalies to those referencing records in the current scope,
    then formats as numbered targets with full evidence. Domain-agnostic.
    """
    if not anomalies:
        return ""

    # Build set of record identifiers AND company names from fetched documents
    doc_ids = set()
    doc_names = set()
    for doc in documents:
        for key in ("name", "id", "title"):
            val = doc.get(key)
            if val:
                doc_ids.add(str(val).lower())
                # Also index partial IDs (e.g. CIK number from "861459/0001437749-23-004014")
                parts = str(val).split("/")
                for part in parts:
                    if len(part) > 3:
                        doc_ids.add(part.lower())
        # Also index by company name and CIK for substring matching
        for key in ("company", "name", "title", "cik"):
            val = doc.get(key, "")
            if val and len(str(val)) > 3:
                doc_names.add(str(val).lower())

    def _record_in_scope(anomaly: dict) -> bool:
        """Check if an anomaly references an entity in the fetched data."""
        record = str(anomaly.get("record", anomaly.get("entity", ""))).lower()
        if not record:
            return False
        # Exact match on IDs
        if record in doc_ids:
            return True
        # Substring match on company names (e.g. "DUKE ENERGY FLORIDA" in title)
        for name in doc_names:
            if record in name or name in record:
                return True
        return False

    # Filter anomalies: ONLY include those referencing records in the fetched data,
    # OR scope-wide stats that don't reference a specific record.
    relevant = []
    for a in anomalies:
        record = str(a.get("record", a.get("entity", ""))).lower()
        if _record_in_scope(a):
            # Anomaly references a record we have — include it
            relevant.append(a)
        elif not record and a.get("type") in ("concentration", "unusual_combination"):
            # Scope-wide stat without a specific record — include it
            relevant.append(a)

    if not relevant:
        return ""

    lines = [
        "INVESTIGATION TARGETS (from statistical survey):",
        "These anomalies were flagged by MATH — independent statistical techniques",
        "that identified something unusual in your data. Your job is to EXPLAIN",
        "why each anomaly exists, not to describe what the data contains.",
        "",
    ]

    for i, a in enumerate(relevant[:12], 1):
        lines.append(f"TARGET {i}: [{a.get('type', '?')}] {a.get('description', '?')}")

        # Record(s) involved
        record = a.get("record", a.get("entity", ""))
        if record:
            lines.append(f"  Record: {record}")

        # Flagged by which techniques
        techniques = a.get("flagged_by", [a.get("type", "unknown")])
        if isinstance(techniques, str):
            techniques = [techniques]
        lines.append(f"  Flagged by: {', '.join(str(t) for t in techniques)}")

        # Full evidence — this is the critical context the agent needs
        evidence = a.get("evidence", {})
        if evidence:
            lines.append("  Evidence:")
            for ek, ev in evidence.items():
                if isinstance(ev, list) and ev:
                    lines.append(f"    {ek}: {', '.join(str(v) for v in ev[:8])}")
                elif isinstance(ev, dict):
                    for dk, dv in ev.items():
                        lines.append(f"    {dk}: {dv}")
                elif ev is not None:
                    lines.append(f"    {ek}: {ev}")

        lines.append("")

    lines.append(
        "For each target, produce an evidence packet explaining whether the "
        "anomaly is genuinely surprising or has a mundane explanation. "
        "Reference the SPECIFIC data from the records below.\n\n"
        "RAW DATA (for reference):\n"
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
