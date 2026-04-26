"""WorkerNode V2 — role-authoring path.

Every node receives a role definition (name, bar, heuristic). On formation,
it assesses whether it can do the work alone or needs to hire. If hiring,
it authors role definitions for each hire. The recursion is identical at
every layer.

This is the NEW PATH — runs alongside the existing WorkerNode when
USE_ROLE_AUTHORING_PATH is set. Does not modify the existing worker.
"""

import asyncio
import json
import uuid
import anthropic
from .schemas import Directive, Observation, Source, NodeResult, Scope, RoleDefinition
from . import prompts as _prompts
from . import events

MAX_CHAIN_DEPTH = 8


class RoleWorkerNode:
    """A node that reasons from its role definition."""

    def __init__(self, directive: Directive, data_source, budget: float,
                 total_budget: float, parent_worker=None,
                 semaphore: asyncio.Semaphore = None, budget_pool=None,
                 parent_pool_available: float = 0.0,
                 depth: int = 0, max_depth: int = 6,
                 leaf_viable_envelope: float = 0.12):
        self.directive = directive
        self.data_source = data_source
        self.budget = budget
        self.total_budget = total_budget
        self.spent = 0.0
        self.parent_worker = parent_worker
        self._semaphore = semaphore or asyncio.Semaphore(3)
        self._budget_pool = budget_pool
        self._parent_pool_available = parent_pool_available
        self.depth = depth
        self.max_depth = max_depth
        self.leaf_viable_envelope = leaf_viable_envelope

        self.node_id = directive.node_id
        self.pos = directive.tree_position
        self.segment_id = directive.segment_id
        self._workspace_path = directive.workspace_path
        self._role = directive.role or RoleDefinition()

        self.observations = []
        self.child_workers = []
        self.child_results = []
        self.findings = []
        self.thinking_log = []
        self.metrics = {}
        self.token_usage = {"input_tokens": 0, "output_tokens": 0}
        self.status = "created"
        self._diagnostics = {}

    @property
    def envelope(self) -> float:
        return self.budget

    @property
    def envelope_exhausted(self) -> bool:
        return self.envelope - self.spent < 0.03

    @property
    def surplus(self) -> float:
        own_remaining = max(0, self.envelope - self.spent)
        returned_from_children = sum(
            max(0, c.envelope - c.spent) for c in self.child_workers
        )
        return own_remaining + returned_from_children

    def _log(self, message: str):
        depth_indent = "│ " * self.depth
        cost_str = f"${self.spent:.2f}/{self.budget:.2f}"
        print(f"  {depth_indent}[{self.pos}] {message} ({cost_str})")

    # === Main lifecycle ===

    async def run(self) -> dict:
        try:
            return await self._run_inner()
        except Exception as e:
            import traceback
            self._log(f"EXCEPTION: {e}\n{traceback.format_exc()}")
            self.status = "error"
            events.emit("node_resolved", {
                "node_id": self.node_id, "tree_position": self.pos,
                "observations_count": 0, "cost_spent": self.spent,
                "top_observation": f"ERROR: {e}",
            })
            raise

    async def _run_inner(self) -> dict:
        self.status = "assessing"
        events.emit("node_spawned", {
            "node_id": self.node_id, "parent_id": self.directive.parent_id,
            "tree_position": self.pos,
            "scope_summary": self.directive.scope.description[:100],
            "segment_id": self.segment_id,
            "role_name": self._role.name,
        })

        # Single LLM call — formation assessment + work
        result = await self._reason()
        if result is None:
            self.status = "resolved"
            events.emit("node_resolved", {
                "node_id": self.node_id, "tree_position": self.pos,
                "observations_count": 0, "cost_spent": self.spent,
                "top_observation": "",
            })
            return self._result()

        formation = result.get("formation_assessment", {})
        decision = formation.get("decision", "investigate")

        if decision == "hire":
            return await self._handle_hiring(result)
        else:
            return self._handle_investigation(result)

    async def _handle_hiring(self, result: dict) -> dict:
        """Process hire directives — become a manager."""
        hire_directives = result.get("hire_directives", [])
        self.observations = self._parse_observations(result.get("observations", []))
        self._synthesis_role = result.get("synthesis_role", None)
        n_hires = len(hire_directives)

        if n_hires < 2:
            # Can't hire just one — resolve with own observations
            self._log(f"Only {n_hires} hire(s) proposed, resolving directly")
            self.status = "resolved"
            events.emit("node_resolved", {
                "node_id": self.node_id, "tree_position": self.pos,
                "observations_count": len(self.observations),
                "cost_spent": self.spent,
                "top_observation": "",
            })
            return self._result()

        self.status = "hiring"
        events.emit("node_decomposing", {
            "node_id": self.node_id, "tree_position": self.pos,
            "children_count": n_hires, "observations_count": len(self.observations),
        })
        self._log(f"Hiring {n_hires} workers (role: {self._role.name})")

        # Reserve budget for Turn 2
        turn2_reserve = min(0.10, max(0, self.budget - self.spent) * 0.2)
        remaining_for_hires = max(0, self.budget - self.spent - turn2_reserve)

        if remaining_for_hires <= 0:
            self.status = "resolved"
            self._log(f"No budget for hires. Resolving.")
            return self._result()

        for i, hd in enumerate(hire_directives, 1):
            hire_budget = hd.get("budget", remaining_for_hires / n_hires)
            hire_budget = min(hire_budget, remaining_for_hires / max(1, n_hires - i + 1))

            if self.depth + 1 > self.max_depth:
                self._log(f"Rejected hire: depth cap ({self.depth + 1} > {self.max_depth})")
                continue

            if hire_budget < self.leaf_viable_envelope:
                self._log(f"Rejected hire: envelope floor (${hire_budget:.3f} < ${self.leaf_viable_envelope:.2f})")
                continue

            # Parse role definition from hire directive
            role_data = hd.get("role", {})
            role = RoleDefinition(
                name=role_data.get("name", f"hire_{i}"),
                success_bar=role_data.get("success_bar", ""),
                heuristic=role_data.get("heuristic", ""),
            )

            # Build child directive
            child_filters = hd.get("data_filter", {})
            if not child_filters or not isinstance(child_filters, dict):
                child_filters = self.directive.data_filter or self.directive.scope.filters

            child_directive = Directive(
                scope=Scope(
                    source=self.directive.scope.source,
                    filters=child_filters,
                    description=hd.get("scope_description", ""),
                ),
                lenses=[],
                parent_context=hd.get("parent_context", ""),
                purpose=hd.get("purpose", ""),
                data_filter=child_filters,
                node_id=str(uuid.uuid4()),
                parent_id=self.node_id,
                tree_position=f"{self.pos}.{i}" if self.pos != "ROOT" else str(i),
                chain_depth=(self.directive.chain_depth + 1) if n_hires == 1 else 0,
                segment_id=self.segment_id,
                survey_anomalies=self.directive.survey_anomalies,
                workspace_path=self._workspace_path,
                role=role,
            )

            child = RoleWorkerNode(
                directive=child_directive,
                data_source=self.data_source,
                budget=hire_budget,
                total_budget=self.total_budget,
                parent_worker=self,
                semaphore=self._semaphore,
                budget_pool=self._budget_pool,
                parent_pool_available=max(0, self.budget - self.spent),
                depth=self.depth + 1,
                max_depth=self.max_depth,
                leaf_viable_envelope=self.leaf_viable_envelope,
            )
            self.child_workers.append(child)

        if not self.child_workers:
            self.status = "resolved"
            self._log(f"All hires rejected. Resolving.")
            return self._result()

        # Run children
        self.status = "waiting_for_hires"
        self.child_results = await asyncio.gather(
            *[child.run() for child in self.child_workers],
            return_exceptions=True,
        )
        self.child_results = [r for r in self.child_results if isinstance(r, dict)]

        n_obs = sum(len(c.observations) for c in self.child_workers)
        self._log(f"All hires returned. {n_obs} total observations from team.")

        # --- TURN 2: Evaluate hires against authored bars ---
        remaining = max(0, self.budget - self.spent)
        review_has_budget = remaining >= 0.03
        if self.child_results and review_has_budget:
            self._log(f"REVIEWING hires against authored bars...")
            turn2_result = await self._turn2_evaluate()
            if turn2_result:
                # Handle continuations
                continuations = turn2_result.get("continuation_decision", {})
                action = continuations.get("action", "RESOLVE")
                cont_directives = continuations.get("continuation_directives", [])

                if action in ("CONTINUE", "REHIRE") and cont_directives:
                    await self._spawn_continuations(cont_directives)

                # Collect synthesized findings
                self.findings = turn2_result.get("synthesized_findings", [])
                # Collect any observations the manager produced in synthesis
                turn2_obs = self._parse_observations(turn2_result.get("observations", []))
                self.observations.extend(turn2_obs)

        self.status = "resolved"
        total_obs = len(self.observations) + n_obs
        events.emit("node_resolved", {
            "node_id": self.node_id, "tree_position": self.pos,
            "observations_count": total_obs,
            "cost_spent": self.spent,
            "top_observation": "",
        })
        return self._result()

    async def _turn2_evaluate(self) -> dict | None:
        """Turn 2: Evaluate each hire against the bar the manager authored."""
        # Build hire reports — each child's role definition + output
        hire_reports_parts = []
        for child in self.child_workers:
            role = child._role
            child_obs = child.observations
            child_metrics = child.metrics

            obs_text = ""
            for i, obs in enumerate(child_obs, 1):
                raw = obs.get("raw_evidence", "")[:200] if isinstance(obs, dict) else str(obs)[:200]
                sig = obs.get("signal_strength", "?") if isinstance(obs, dict) else "?"
                obs_text += f"  Observation {i} [{sig}]: {raw}\n"

            followups = ""
            if isinstance(child_metrics, dict):
                threads = child_metrics.get("worthwhile_followup_threads", [])
                if threads:
                    followups = "  Follow-up threads flagged:\n"
                    for t in threads[:3]:
                        if isinstance(t, dict):
                            followups += f"    - {t.get('what_to_investigate', '?')[:100]}\n"

            hire_reports_parts.append(
                f"--- HIRE: {role.name} ---\n"
                f"AUTHORED BAR: {role.success_bar}\n"
                f"AUTHORED HEURISTIC: {role.heuristic}\n"
                f"SCOPE: {child.directive.scope.description[:200]}\n"
                f"ACTUAL COST: ${child.spent:.3f} (of ${child.budget:.3f} allocated)\n"
                f"OBSERVATIONS ({len(child_obs)}):\n{obs_text}"
                f"SELF-EVALUATION: bar_met={child_metrics.get('bar_met', '?')}, "
                f"quality={child_metrics.get('evidence_quality', '?')}\n"
                f"{followups}\n"
            )

        hire_reports = "\n".join(hire_reports_parts)

        # Workspace context
        workspace_context = ""
        if self._workspace_path:
            from .workspace import OrgWorkspace
            ws = OrgWorkspace(self._workspace_path)
            charter = ws.read_charter()
            if charter:
                workspace_context = f"## ORGANIZATIONAL CHARTER\n\n{charter}\n\n"

        remaining = max(0, self.budget - self.spent)

        # Compute observable cost data for the manager
        children_costs = [c.spent for c in self.child_workers]
        avg_hire_cost = sum(children_costs) / max(1, len(children_costs))
        total_children_cost = sum(children_costs)
        own_cost = self.spent - total_children_cost  # formation + review overhead

        # Downstream phase estimate from pool if available
        downstream_estimate = 0.50  # conservative default
        if self._budget_pool:
            pool_spent = self._budget_pool.spent
            exploration_spent = self._budget_pool.phase_spent.get("exploration", 0)
            non_exploration_spent = pool_spent - exploration_spent
            if non_exploration_spent > 0.01:
                downstream_estimate = non_exploration_spent  # use actual if available

        cost_context = (
            f"OBSERVABLE COST DATA:\n"
            f"  Your formation cost: ${own_cost:.3f}\n"
            f"  Hires completed: {len(children_costs)}\n"
            f"  Average cost per hire: ${avg_hire_cost:.3f}\n"
            f"  Total spent on hires: ${total_children_cost:.3f}\n"
            f"  Downstream phases estimate: ${downstream_estimate:.2f} "
            f"(synthesis + validation + deep-dive + impact + report)\n"
            f"  Budget after downstream: ${max(0, remaining - downstream_estimate):.2f} "
            f"available for continuation\n"
        )

        import datetime
        prompt = _prompts.MANAGER_TURN2_PROMPT_V2.format(
            budget_remaining=remaining,
            role_name=self._role.name,
            role_bar=self._role.success_bar,
            scope_description=self.directive.scope.description[:500],
            workspace_context=workspace_context,
            hire_reports=hire_reports,
            cost_context=cost_context,
        )

        # Budget gate
        if self.envelope_exhausted:
            return None
        if self._budget_pool and self._budget_pool.exploration_exhausted:
            return None

        async with self._semaphore:
            thinking, output, cost, usage = await _call_llm(prompt)

        self.spent += cost
        self.token_usage["input_tokens"] += usage["input_tokens"]
        self.token_usage["output_tokens"] += usage["output_tokens"]
        if self._budget_pool:
            self._budget_pool.record("review", cost)
        self.thinking_log.append({"turn": "turn2_review", "thinking": thinking})
        _emit_thinking_chunks(self.node_id, "turn2_review", thinking)

        try:
            result = _parse_json(output)
        except (json.JSONDecodeError, ValueError):
            return None

        # Log evaluations
        for ev in result.get("hire_evaluations", []):
            cls = ev.get("classification", "?")
            name = ev.get("hire_role_name", "?")
            self._log(f"  Hire '{name}': {cls}")

        action = result.get("continuation_decision", {}).get("action", "RESOLVE")
        self._log(f"  Turn 2 decision: {action}")

        return result

    async def _spawn_continuations(self, cont_directives: list):
        """Spawn continuation hires from Turn 2 decisions."""
        for i, cd in enumerate(cont_directives, 1):
            cont_budget = cd.get("budget", 0)
            remaining = max(0, self.budget - self.spent)

            if cont_budget > remaining:
                cont_budget = remaining
            if cont_budget < self.leaf_viable_envelope:
                self._log(f"Continuation rejected: envelope floor (${cont_budget:.3f})")
                continue
            if self.depth + 1 > self.max_depth:
                self._log(f"Continuation rejected: depth cap")
                continue

            role_data = cd.get("role", {})
            role = RoleDefinition(
                name=role_data.get("name", f"continuation_{i}"),
                success_bar=role_data.get("success_bar", ""),
                heuristic=role_data.get("heuristic", ""),
            )

            child_filters = cd.get("data_filter", {})
            if not child_filters or not isinstance(child_filters, dict):
                child_filters = self.directive.data_filter or self.directive.scope.filters

            child_directive = Directive(
                scope=Scope(
                    source=self.directive.scope.source,
                    filters=child_filters,
                    description=cd.get("scope_description", ""),
                ),
                lenses=[],
                parent_context=cd.get("parent_context", ""),
                purpose=cd.get("purpose", ""),
                data_filter=child_filters,
                node_id=str(uuid.uuid4()),
                parent_id=self.node_id,
                tree_position=f"{self.pos}.C{i}",
                chain_depth=0,
                segment_id=self.segment_id,
                workspace_path=self._workspace_path,
                role=role,
            )

            child = RoleWorkerNode(
                directive=child_directive,
                data_source=self.data_source,
                budget=cont_budget,
                total_budget=self.total_budget,
                parent_worker=self,
                semaphore=self._semaphore,
                budget_pool=self._budget_pool,
                parent_pool_available=max(0, self.budget - self.spent),
                depth=self.depth + 1,
                max_depth=self.max_depth,
                leaf_viable_envelope=self.leaf_viable_envelope,
            )

            self._log(f"Spawning continuation: {role.name} (${cont_budget:.2f})")
            cont_result = await child.run()
            self.child_workers.append(child)
            if isinstance(cont_result, dict):
                self.child_results.append(cont_result)

    def _handle_investigation(self, result: dict) -> dict:
        """Process direct investigation results."""
        self.observations = self._parse_observations(result.get("observations", []))
        self.metrics = result.get("self_evaluation", {})
        self.status = "resolved"

        n_obs = len(self.observations)
        top_obs = self.observations[0].get("raw_evidence", "")[:80] if self.observations else ""
        self._log(f"RESOLVED: {n_obs} observations (role: {self._role.name})")
        events.emit("node_resolved", {
            "node_id": self.node_id, "tree_position": self.pos,
            "observations_count": n_obs, "cost_spent": self.spent,
            "top_observation": top_obs,
        })
        return self._result()

    # === LLM Call ===

    async def _reason(self) -> dict | None:
        """Single LLM call with formation-time assessment."""
        # Fetch data
        filters = dict(self.directive.data_filter or self.directive.scope.filters)
        schema = self.data_source.filter_schema() if hasattr(self.data_source, 'filter_schema') else {}
        if schema and filters:
            valid_params = set(schema.keys())
            unknown = [k for k in filters if k not in valid_params]
            if unknown:
                self._log(f"Filter validation: unknown params {unknown} removed")
                filters = {k: v for k, v in filters.items() if k in valid_params}

        documents = await self.data_source.fetch(filters, max_results=100)
        if not documents:
            return None

        # Format data
        fetched_data = _format_documents(documents)
        parent_ctx = self.directive.parent_context or "You are the first node. No prior context."
        purpose = self.directive.purpose or "Carry out the work your role demands."
        remaining_own = max(0, self.budget - self.spent)

        # Budget stage
        budget_pct = (remaining_own / self.budget * 100) if self.budget > 0 else 0
        if budget_pct > 70:
            budget_stage = "EARLY — explore broadly."
        elif budget_pct > 40:
            budget_stage = "MID — balance breadth and depth."
        else:
            budget_stage = "LATE — resolve what you have."

        # Depth guidance
        if self.depth >= self.max_depth:
            depth_guidance = "You are at max depth. You cannot hire. Investigate directly."
        else:
            depth_guidance = (
                f"Each hire must receive at least ${self.leaf_viable_envelope:.2f}. "
                f"The system rejects hires below this minimum."
            )

        # Workspace context
        workspace_context = ""
        if self._workspace_path:
            from .workspace import OrgWorkspace
            ws = OrgWorkspace(self._workspace_path)
            charter = ws.read_charter()
            rules = ws.read_rules()
            if charter:
                workspace_context += f"## ORGANIZATIONAL CHARTER\n\n{charter}\n\n"
            if rules:
                workspace_context += f"## RULES OF ENGAGEMENT\n\n{rules}\n\n"

        # Filter schema
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

        # Chain circuit breaker
        force_resolve = ""
        if self.directive.chain_depth >= MAX_CHAIN_DEPTH:
            force_resolve = _prompts.NODE_FORCE_RESOLVE_OVERRIDE_V2.format(
                chain_depth=self.directive.chain_depth)

        # Phase remaining
        phase_remaining = 0.0
        if self._budget_pool:
            phase_remaining = self._budget_pool.exploration_remaining()

        import datetime
        prompt = _prompts.NODE_REASONING_PROMPT_V2.format(
            current_date=datetime.date.today().isoformat(),
            role_name=self._role.name or "investigator",
            role_bar=self._role.success_bar or "Produce specific, evidence-backed findings.",
            role_heuristic=self._role.heuristic or "When uncertain, favor specificity over breadth.",
            scope_description=self.directive.scope.description,
            purpose=purpose,
            parent_context=parent_ctx,
            workspace_context=workspace_context,
            filter_schema=filter_schema_str,
            budget_remaining=remaining_own,
            parent_pool_remaining=self._parent_pool_available,
            phase_remaining=phase_remaining,
            segment_context=f"Part of a ${self.total_budget:.2f} engagement.\n",
            current_depth=self.depth,
            max_depth=self.max_depth,
            leaf_viable_envelope=self.leaf_viable_envelope,
            depth_guidance=depth_guidance,
            budget_stage=budget_stage,
            doc_count=len(documents),
            fetched_data=fetched_data,
            force_resolve=force_resolve,
        )

        # Budget gate
        if self.envelope_exhausted:
            self._log(f"Envelope exhausted (${self.spent:.3f} of ${self.envelope:.3f})")
            return None
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
        self.thinking_log.append({"turn": "formation", "thinking": thinking})

        # Emit thinking
        _emit_thinking_chunks(self.node_id, "formation", thinking)

        try:
            result = _parse_json(output)
        except (json.JSONDecodeError, ValueError):
            return {"observations": [], "hire_directives": [],
                    "formation_assessment": {"decision": "investigate", "reasoning": "parse failure"}}

        return result

    def _parse_observations(self, obs_list: list) -> list[dict]:
        """Parse observation dicts from LLM output."""
        parsed = []
        for obs_data in obs_list:
            if isinstance(obs_data, dict) and obs_data.get("raw_evidence"):
                parsed.append(obs_data)
        return parsed

    def _build_diagnostic(self) -> dict:
        """Build diagnostic data for this node — compatible with orchestrator diagnostics."""
        obs_list = self.observations
        sample_obs = obs_list[0] if obs_list else {}

        return {
            "node_id": self.node_id,
            "tree_position": self.pos,
            "role": self._role.name,
            "role_bar": self._role.success_bar[:200],
            "scope": self.directive.scope.description[:200],
            "purpose": self.directive.purpose[:200],
            "data_received": self._diagnostics.get("data_received", {"record_count": 0}),
            "anomaly_targets_received": self._diagnostics.get("anomaly_targets_received", {"count": 0, "targets": []}),
            "thinking_summary": self.thinking_log[0]["thinking"][:2000] if self.thinking_log else "",
            "output": {
                "observations_count": len(obs_list),
                "children_spawned": len(self.child_workers),
                "evidence_cited": sum(1 for o in obs_list if isinstance(o, dict) and o.get("raw_evidence")),
                "sample_observation": sample_obs if isinstance(sample_obs, dict) else {},
            },
            "self_evaluation": self.metrics if isinstance(self.metrics, dict) else {},
            "budget": {
                "envelope": self.budget,
                "spent": self.spent,
                "surplus": self.surplus,
                "depth": self.depth,
                "max_depth": self.max_depth,
            },
            "decision": "hired" if self.child_workers else "investigated",
            "decision_reasoning": "",
        }

    def _build_node_json(self) -> dict:
        """Build node output JSON — written to nodes/ directory."""
        return {
            "node_id": self.node_id,
            "parent_id": self.directive.parent_id,
            "scope_description": self.directive.scope.description[:200],
            "tree_position": self.pos,
            "role": self._role.name,
            "role_bar": self._role.success_bar[:200],
            "observations": self.observations,
            "child_directives_count": len(self.child_workers),
            "unresolved": [],
            "raw_reasoning": "",
            "thinking": self.thinking_log[0]["thinking"] if self.thinking_log else "",
            "thinking_log": self.thinking_log,
            "turn2_review": self.thinking_log[1]["thinking"] if len(self.thinking_log) > 1 else "",
            "metrics": self.metrics,
            "token_usage": self.token_usage,
            "cost": self.spent,
        }

    def _result(self) -> dict:
        """Package results for parent."""
        # Collect all observations from children recursively
        all_obs = list(self.observations)
        for child in self.child_workers:
            child_result = getattr(child, '_last_result', None)
            if child_result and isinstance(child_result, dict):
                all_obs.extend(child_result.get("all_observations", []))
            # Also collect direct observations
            all_obs.extend(child.observations)

        self._last_result = {
            "node_id": self.node_id,
            "tree_position": self.pos,
            "role": self._role.name,
            "formation_decision": "hire" if self.child_workers else "investigate",
            "observations": self.observations,
            "all_observations": all_obs,
            "children_count": len(self.child_workers),
            "cost": self.spent,
            "budget": self.budget,
            "depth": self.depth,
            "metrics": self.metrics,
            "thinking_log": self.thinking_log,
            "findings": self.findings,
            "synthesis_role": getattr(self, '_synthesis_role', None),
        }
        return self._last_result


# === Utility functions (shared patterns with worker.py) ===

def _format_documents(docs: list[dict]) -> str:
    """Format documents for prompt context."""
    if not docs:
        return "(no data)"
    lines = []
    for doc in docs[:100]:
        parts = []
        for k, v in doc.items():
            if v is not None and v != "" and v != []:
                val_str = str(v)
                if len(val_str) > 200:
                    val_str = val_str[:200] + "..."
                parts.append(f"{k}: {val_str}")
        lines.append("{" + ", ".join(parts) + "}")
    return "\n".join(lines)


async def _call_llm(prompt: str) -> tuple[str, str, float, dict]:
    """Call Claude with extended thinking. Returns (thinking, output, cost, usage)."""
    client = anthropic.Anthropic()
    response = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=16000,
        thinking={"type": "enabled", "budget_tokens": 8000},
        messages=[{"role": "user", "content": prompt}],
    )

    thinking = ""
    output = ""
    for block in response.content:
        if block.type == "thinking":
            thinking = block.thinking
        elif block.type == "text":
            output = block.text

    usage = {
        "input_tokens": response.usage.input_tokens,
        "output_tokens": response.usage.output_tokens,
    }
    cost = (usage["input_tokens"] * 3 + usage["output_tokens"] * 15) / 1_000_000
    return thinking, output, cost, usage


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
    start = text.find("{")
    end = text.rfind("}") + 1
    if start >= 0 and end > start:
        return json.loads(text[start:end])
    raise ValueError("Could not extract JSON")


def _emit_thinking_chunks(node_id: str, turn: str, thinking: str, chunk_size: int = 500):
    """Emit thinking in chunks for visualizer streaming."""
    if not thinking:
        return
    for i in range(0, len(thinking), chunk_size):
        events.emit("thinking_chunk", {
            "node_id": node_id,
            "turn": turn,
            "chunk": thinking[i:i + chunk_size],
            "chunk_index": i // chunk_size,
        })
