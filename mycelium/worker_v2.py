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
import time
import uuid
from pathlib import Path
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
                 leaf_viable_envelope: float = 0.12,
                 bulletin_board=None, partition_gate: str = "off"):
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

        self._bulletin_board = bulletin_board
        self._partition_gate = partition_gate
        self._board_pull_timestamp = 0.0  # track last pull time for delta pulls

        self.observations = []
        self.child_workers = []
        self.child_results = []
        self.findings = []
        self.thinking_log = []
        self.metrics = {}
        self.token_usage = {"input_tokens": 0, "output_tokens": 0}
        self.status = "created"
        self._diagnostics = {}
        self._broadcast_post_ids = []  # post_ids this node broadcast

    # === Bulletin board helpers ===

    def _pull_board(self, since: float = 0.0) -> str:
        """Pull posts from the bulletin board and format for prompt.
        Returns formatted string of posts, or empty string if no board."""
        if not self._bulletin_board:
            return ""
        if since > 0:
            posts = self._bulletin_board.get_posts_since(since, exclude_author=self.node_id)
        else:
            posts = self._bulletin_board.get_posts(exclude_author=self.node_id)
        if not posts:
            return ""
        # Record pull events (influence determined later by reasoning trace)
        for p in posts:
            self._bulletin_board.pull(self.node_id, p["post_id"])
        self._board_pull_timestamp = time.time()
        return self._bulletin_board.format_for_prompt(posts)

    def _broadcast_observations(self, broadcasts: list[dict]):
        """Post selected observations to the bulletin board."""
        if not self._bulletin_board or not broadcasts:
            return
        for b in broadcasts:
            post_type = b.get("post_type", "OBSERVATION")
            if post_type not in ("OBSERVATION", "HYPOTHESIS", "DEAD_END"):
                post_type = "OBSERVATION"
            content = b.get("content", "")
            if not content:
                continue
            references = b.get("references", [])
            post_id = self._bulletin_board.post(
                author_node_id=self.node_id,
                author_role_name=self._role.name,
                post_type=post_type,
                content=content,
                references=references if isinstance(references, list) else [],
            )
            self._broadcast_post_ids.append(post_id)
        if self._broadcast_post_ids:
            events.emit("bb_post", {
                "node_id": self.node_id,
                "post_count": len(self._broadcast_post_ids),
            })

    def _update_pull_influence(self):
        """Scan reasoning trace for semantic references to pulled posts."""
        if not self._bulletin_board:
            return
        # Collect all reasoning text
        reasoning_text = ""
        for entry in self.thinking_log:
            reasoning_text += entry.get("thinking", "") + "\n"
        if hasattr(self, '_last_result') and isinstance(self._last_result, dict):
            reasoning_text += json.dumps(self._last_result, default=str)

        if not reasoning_text:
            return

        reasoning_lower = reasoning_text.lower()

        for pull in self._bulletin_board.pulls:
            if pull["pulling_node_id"] != self.node_id:
                continue
            if pull["influence"]:
                continue

            # Level 1: literal post_id match
            if pull["post_id"] in reasoning_text:
                pull["influence"] = True
                continue

            # Level 2: semantic content match — check if key phrases from the
            # post appear in the reasoning trace
            post = next((p for p in self._bulletin_board.posts
                        if p["post_id"] == pull["post_id"]), None)
            if not post:
                continue

            post_content = post.get("content", "")
            if not post_content or len(post_content) < 20:
                continue

            # Extract distinctive phrases (3+ word sequences) from post
            # and check if any appear in reasoning
            words = post_content.split()
            matched = False
            for i in range(min(len(words) - 4, 20)):  # Check first 20 windows
                phrase = " ".join(words[i:i+4]).lower()
                # Skip generic phrases
                if any(g in phrase for g in ["the ", "this ", "that ", "with ", "from "]):
                    continue
                if phrase in reasoning_lower:
                    pull["influence"] = True
                    matched = True
                    break

            if not matched:
                # Check for bulletin board generic references near post-specific content
                if "bulletin board" in reasoning_lower or "board shows" in reasoning_lower:
                    # Worker referenced the board — check if post author or key entity appears
                    author = post.get("author_role_name", "").lower()
                    if author and len(author) > 5 and author in reasoning_lower:
                        pull["influence"] = True

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
            return await self._handle_investigation(result)

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
                mission=role_data.get("mission", ""),
                success_bar=role_data.get("success_bar", ""),
                heuristic=role_data.get("heuristic", ""),
            )

            # Build child directive — partition (preferred) or data_filter (legacy)
            child_partition = hd.get("partition", "")
            child_filters = hd.get("data_filter", {})
            if not child_partition and (not child_filters or not isinstance(child_filters, dict)):
                # No partition or data_filter authored — fall back to parent's
                child_filters = self.directive.data_filter or self.directive.scope.filters
                child_partition = self.directive.partition
                if not child_partition:
                    self._log(f"  Hire '{role.name}': no partition authored, inheriting parent scope")

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
                partition=child_partition,
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
                bulletin_board=self._bulletin_board,
                partition_gate=self._partition_gate,
            )
            self.child_workers.append(child)

        # --- MECE Partition Gate ---
        if self.child_workers and self._partition_gate in ("on", "off"):
            from .partition_gate import check_mece
            child_parts = [
                {"role_name": c._role.name, "partition_desc": c.directive.partition,
                 "tree_position": c.pos}
                for c in self.child_workers
            ]
            try:
                gate_result = await check_mece(
                    parent_partition=self.directive.partition or None,
                    child_partitions=child_parts,
                    data_source=self.data_source,
                    run_dir=str(Path(self._workspace_path).parent) if self._workspace_path else None,
                    parent_node_id=self.node_id,
                    parent_tree_pos=self.pos,
                )
                self.spent += gate_result.get("cost", 0)
                events.emit("partition_gate", {
                    "node_id": self.node_id, "tree_position": self.pos,
                    "verdict": gate_result["verdict"],
                    "completeness_pct": gate_result["completeness"]["coverage_pct"],
                    "exclusivity_overlaps": len(gate_result["exclusivity"].get("overlapping_pairs", [])),
                    "shape_failures": len(gate_result["shape"]["failures"]),
                })
                if self._partition_gate == "on" and gate_result["verdict"] == "FAIL":
                    self._log(f"PARTITION GATE HALTED: {gate_result['failure_reasons']}")
                    self.status = "resolved"
                    self.child_workers = []
                    events.emit("node_resolved", {
                        "node_id": self.node_id, "tree_position": self.pos,
                        "observations_count": len(self.observations),
                        "cost_spent": self.spent,
                        "top_observation": "PARTITION GATE HALTED",
                    })
                    return self._result()
            except Exception as e:
                self._log(f"Partition gate error (continuing): {e}")

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

        # Check if pulled posts influenced reasoning
        self._update_pull_influence()

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

            partition_info = ""
            if child.directive.partition:
                interp = getattr(child, '_translation_interpretation', '')
                partition_info = (
                    f"PARTITION: {child.directive.partition[:200]}\n"
                    f"TRANSLATION: {interp[:200]}\n"
                )

            hire_reports_parts.append(
                f"--- HIRE: {role.name} ---\n"
                f"AUTHORED MISSION: {role.mission}\n"
                f"AUTHORED BAR: {role.success_bar}\n"
                f"AUTHORED HEURISTIC: {role.heuristic}\n"
                f"SCOPE: {child.directive.scope.description[:200]}\n"
                f"{partition_info}"
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

        # Board context for cascade detection
        board_text = self._pull_board()
        board_context = ""
        if board_text:
            board_context = (
                f"BULLETIN BOARD ({len(self._bulletin_board.posts)} posts):\n"
                f"{board_text}"
            )

        import datetime
        prompt = _prompts.MANAGER_TURN2_PROMPT_V2.format(
            budget_remaining=remaining,
            role_name=self._role.name,
            role_mission=self._role.mission or "Produce the most insightful findings this engagement can yield.",
            role_bar=self._role.success_bar,
            scope_description=self.directive.scope.description[:500],
            workspace_context=workspace_context,
            board_context=board_context,
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

        # Store full Turn 2 structured output for diagnostics
        self._turn2_result = result

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
                mission=role_data.get("mission", ""),
                success_bar=role_data.get("success_bar", ""),
                heuristic=role_data.get("heuristic", ""),
            )

            child_partition = cd.get("partition", "")
            child_filters = cd.get("data_filter", {})
            if not child_partition and (not child_filters or not isinstance(child_filters, dict)):
                child_filters = self.directive.data_filter or self.directive.scope.filters
                child_partition = self.directive.partition

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
                partition=child_partition,
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
                bulletin_board=self._bulletin_board,
                partition_gate=self._partition_gate,
            )

            self._log(f"Spawning continuation: {role.name} (${cont_budget:.2f})")
            cont_result = await child.run()
            self.child_workers.append(child)
            if isinstance(cont_result, dict):
                self.child_results.append(cont_result)

    async def _handle_investigation(self, result: dict) -> dict:
        """Process investigation results with mid-investigation reassessment."""
        self.observations = self._parse_observations(result.get("observations", []))
        self.metrics = result.get("self_evaluation", {})
        self._formation_summary = result.get("formation_assessment", {})

        # Broadcast selected observations to the bulletin board
        self._broadcast_observations(result.get("broadcasts", []))

        n_obs = len(self.observations)
        remaining = max(0, self.budget - self.spent)

        # Re-assessment: re-run floor/ceiling tests with new information
        if n_obs > 0 and remaining >= 0.03:
            reassessment = await self._reassess()
            if reassessment:
                decision = reassessment.get("decision", "RESOLVE")
                post_reassess_remaining = max(0, self.budget - self.spent)

                if decision == "INVESTIGATE_FURTHER" and post_reassess_remaining >= 0.03:
                    self._log(f"INVESTIGATING FURTHER ({n_obs} initial obs)")
                    await self._extend_investigation(reassessment)

                elif decision == "HIRE" and post_reassess_remaining >= self.leaf_viable_envelope * 2:
                    self._log(f"HIRING from investigation ({n_obs} initial obs, ${post_reassess_remaining:.2f} remaining)")
                    return await self._become_manager(reassessment, result)

        # Check if pulled posts influenced reasoning
        self._update_pull_influence()

        # Resolve with current observations
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

    async def _reassess(self) -> dict | None:
        """Mid-investigation reassessment: should I continue, extend, or become manager?"""
        # Format initial observations for the reassessment prompt
        obs_lines = []
        for i, obs in enumerate(self.observations, 1):
            raw = obs.get("raw_evidence", "")[:200] if isinstance(obs, dict) else str(obs)[:200]
            sig = obs.get("signal_strength", "?") if isinstance(obs, dict) else "?"
            obs_lines.append(f"  {i}. [{sig}] {raw}")
        observations_summary = "\n".join(obs_lines)

        formation = getattr(self, '_formation_summary', {})
        formation_summary = (
            f"Decision: {formation.get('decision', '?')}\n"
            f"Scope: {formation.get('scope_size', '?')}\n"
            f"Bar depth: {formation.get('bar_depth', '?')}\n"
            f"Capacity: {formation.get('capacity_estimate', '?')}\n"
            f"Reasoning: {formation.get('reasoning', '?')[:200]}"
        )

        remaining = max(0, self.budget - self.spent)
        min_hire_budget = self.leaf_viable_envelope * 2

        # Pull new posts since formation
        board_text = self._pull_board(since=self._board_pull_timestamp)
        board_context = ""
        if board_text:
            board_context = (
                f"NEW BULLETIN BOARD POSTS (since your formation):\n"
                f"{board_text}"
            )

        prompt = _prompts.WORKER_REASSESSMENT_PROMPT_V2.format(
            role_name=self._role.name,
            role_mission=self._role.mission or "Produce the most insightful findings this data can yield.",
            role_bar=self._role.success_bar,
            formation_summary=formation_summary,
            observation_count=len(self.observations),
            observations_summary=observations_summary,
            budget_allocated=self.budget,
            budget_spent=self.spent,
            budget_remaining=remaining,
            leaf_viable_envelope=self.leaf_viable_envelope,
            min_hire_budget=min_hire_budget,
            board_context=board_context,
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
            self._budget_pool.record("exploration", cost)
        self.thinking_log.append({"turn": "reassessment", "thinking": thinking})
        _emit_thinking_chunks(self.node_id, "reassessment", thinking)

        try:
            result = _parse_json(output)
        except (json.JSONDecodeError, ValueError):
            return None

        decision = result.get("decision", "CONTINUE_INVESTIGATING")
        self._log(f"  Reassessment: {decision}")
        return result

    async def _extend_investigation(self, reassessment: dict):
        """Second reasoning turn to push initial observations deeper."""
        # Format initial observations
        obs_text = ""
        for i, obs in enumerate(self.observations, 1):
            raw = obs.get("raw_evidence", "")[:300] if isinstance(obs, dict) else str(obs)[:300]
            hyp = obs.get("local_hypothesis", "")[:150] if isinstance(obs, dict) else ""
            obs_text += f"  {i}. {raw}\n     Hypothesis: {hyp}\n"

        # Format threads to push deeper
        threads = reassessment.get("reassessment", {}).get("threads", [])
        thread_text = ""
        for t in threads:
            if isinstance(t, dict) and t.get("substantive"):
                thread_text += f"  - {t.get('thread', '?')}: {t.get('reasoning', '')[:100]}\n"
        if not thread_text:
            thread_text = "  Push deeper on all initial observations."

        reasoning = reassessment.get("decision_reasoning", "")

        # Re-fetch data (same filters, worker sees same records for deeper analysis)
        filters = dict(self.directive.data_filter or self.directive.scope.filters)
        if "slice" in filters:
            slice_desc = filters.pop("slice")
            if isinstance(slice_desc, str) and slice_desc.strip():
                translated = await self._translate_slice(slice_desc)
                filters.update(translated)
        if hasattr(self.data_source, 'valid_filter_params'):
            valid_params = self.data_source.valid_filter_params()
            filters = {k: v for k, v in filters.items() if k in valid_params}
        documents = await self.data_source.fetch(filters, max_results=100)
        fetched_data = _format_documents(documents) if documents else "(no data)"

        prompt = _prompts.WORKER_EXTENSION_PROMPT_V2.format(
            role_name=self._role.name,
            role_mission=self._role.mission or "Produce the most insightful findings this data can yield.",
            role_bar=self._role.success_bar,
            initial_observations=obs_text,
            reassessment_reasoning=reasoning,
            extension_threads=thread_text,
            doc_count=len(documents) if documents else 0,
            fetched_data=fetched_data,
        )

        # Budget gate
        if self.envelope_exhausted:
            return
        if self._budget_pool and self._budget_pool.exploration_exhausted:
            return

        async with self._semaphore:
            thinking, output, cost, usage = await _call_llm(prompt)

        self.spent += cost
        self.token_usage["input_tokens"] += usage["input_tokens"]
        self.token_usage["output_tokens"] += usage["output_tokens"]
        if self._budget_pool:
            self._budget_pool.record("exploration", cost)
        self.thinking_log.append({"turn": "extension", "thinking": thinking})
        _emit_thinking_chunks(self.node_id, "extension", thinking)

        try:
            result = _parse_json(output)
        except (json.JSONDecodeError, ValueError):
            return

        # Merge extended observations with initial ones
        extended_obs = self._parse_observations(result.get("extended_observations", []))
        self.observations.extend(extended_obs)
        self._log(f"  Extended: +{len(extended_obs)} observations (total {len(self.observations)})")

        # Broadcast from extension
        self._broadcast_observations(result.get("broadcasts", []))

        # Check if pulled posts influenced reasoning
        self._update_pull_influence()

        # Update metrics from extension self-eval
        ext_eval = result.get("self_evaluation", {})
        if ext_eval:
            self.metrics = ext_eval

    async def _become_manager(self, reassessment: dict, initial_result: dict) -> dict:
        """Worker transitions to manager role mid-investigation."""
        # Keep initial observations as manager's own findings
        # The worker has already produced observations — those stay

        # Build hire directives from reassessment threads
        threads = reassessment.get("reassessment", {}).get("threads", [])
        hire_threads = [t for t in threads
                       if isinstance(t, dict) and not t.get("same_cognition", True)]

        if len(hire_threads) < 2:
            # Not enough distinct-cognition threads — extend instead
            self._log(f"  Not enough distinct threads for hiring, extending instead")
            await self._extend_investigation(reassessment)
            return self._resolve_with_current()

        # Need a formation call to design the team properly — the worker
        # reasons about what hires to create from its observations
        remaining = max(0, self.budget - self.spent)

        # Build context from reassessment for the hiring decision
        obs_summary = "\n".join(
            f"  - {obs.get('raw_evidence', '')[:150]}"
            for obs in self.observations[:10]
        )

        # Re-run formation with hire decision forced, passing current observations as context
        self.directive.parent_context = (
            f"MID-INVESTIGATION PIVOT: You initially investigated and found:\n"
            f"{obs_summary}\n\n"
            f"Your reassessment identified threads requiring different cognition. "
            f"Design a team to pursue those threads. Budget: ${remaining:.2f}.\n\n"
            f"Original context: {self.directive.parent_context or ''}"
        )

        # Call the standard formation prompt but with updated context
        hire_result = await self._reason()
        if hire_result is None:
            return self._resolve_with_current()

        # Force the hire path — we already decided to become manager
        formation = hire_result.get("formation_assessment", {})
        formation["decision"] = "hire"
        hire_directives = hire_result.get("hire_directives", [])

        if len(hire_directives) < 2:
            # LLM didn't produce enough hires — resolve with what we have
            self._log(f"  Become-manager: insufficient hires authored, resolving")
            return self._resolve_with_current()

        self._log(f"  Became manager: {len(hire_directives)} hires")
        self._became_manager = True
        return await self._handle_hiring(hire_result)

    def _resolve_with_current(self) -> dict:
        """Resolve with current observations — helper for fallback paths."""
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

    # === Scope-fit check ===

    async def _check_scope_fit(self, documents: list) -> str:
        """Check if fetched records match the partition intent.

        Returns: 'MATCH', 'PARTIAL', or 'MISMATCH'.
        """
        # Sample a few records for the check
        sample = documents[:5]
        sample_text = ""
        for i, doc in enumerate(sample, 1):
            if isinstance(doc, dict):
                sample_text += f"  Record {i}: {json.dumps(doc, default=str)[:200]}\n"

        prompt = (
            f"Do these records match the intended data partition?\n\n"
            f"SCOPE: {self.directive.scope.description[:200]}\n"
            f"PARTITION: {self.directive.partition}\n"
            f"TRANSLATOR INTERPRETATION: {self._translation_interpretation}\n\n"
            f"SAMPLE RECORDS ({len(sample)} of {len(documents)}):\n{sample_text}\n"
            f"Verdict: MATCH (records fit the partition intent), "
            f"PARTIAL (some fit, some don't), or "
            f"MISMATCH (records clearly don't match the partition).\n\n"
            f"Return JSON: {{\"verdict\": \"MATCH|PARTIAL|MISMATCH\", \"reasoning\": \"one sentence\"}}"
        )

        try:
            async with self._semaphore:
                client = anthropic.AsyncAnthropic()
                response = await client.messages.create(
                    model="claude-sonnet-4-20250514",
                    max_tokens=200,
                    messages=[{"role": "user", "content": prompt}],
                )
            cost = (response.usage.input_tokens * 3 / 1_000_000 +
                    response.usage.output_tokens * 15 / 1_000_000)
            self.spent += cost
            if self._budget_pool:
                self._budget_pool.record("overhead", cost)

            raw = response.content[0].text.strip()
            parsed = json.loads(raw) if raw.startswith("{") else json.loads(
                raw[raw.find("{"):raw.rfind("}") + 1])
            verdict = parsed.get("verdict", "MATCH")
            self._log(f"  Scope-fit: {verdict} — {parsed.get('reasoning', '')[:80]}")
            return verdict
        except Exception as e:
            self._log(f"  Scope-fit check failed: {e}, assuming MATCH")
            return "MATCH"

    # === Slice translation ===

    async def _translate_slice(self, slice_desc: str) -> dict:
        """Translate a natural-language slice description to catalog query fields.

        Uses a small LLM call. Returns a dict of catalog field conditions.
        On failure, returns {"keyword": <first few words of slice>} as fallback.
        """
        schema = self.data_source.filter_schema() if hasattr(self.data_source, 'filter_schema') else {}
        # Find the catalog fields description
        cat_info = schema.get("catalog_fields", {})
        cat_desc = cat_info.get("description", "") if cat_info else ""

        if not cat_desc:
            # No catalog fields available — fall back to keyword
            return {"keyword": " ".join(slice_desc.split()[:3])}

        prompt = (
            f"Translate this natural-language data slice description into catalog query fields.\n\n"
            f"Slice description: {slice_desc}\n\n"
            f"Available catalog fields and operators:\n{cat_desc}\n\n"
            f"Return ONLY a JSON object with field names as keys and values/operators as values.\n"
            f"Example: {{\"monthly_downloads\": {{\"gt\": 1000000}}, \"maintainer_count\": 1}}\n"
            f"Use only field names from the list above. Return valid JSON, nothing else."
        )

        try:
            client = anthropic.AsyncAnthropic()
            response = await client.messages.create(
                model="claude-sonnet-4-20250514",
                max_tokens=500,
                messages=[{"role": "user", "content": prompt}],
            )
            raw = response.content[0].text.strip()
            slice_cost = (response.usage.input_tokens * 3 / 1_000_000 +
                          response.usage.output_tokens * 15 / 1_000_000)
            self.spent += slice_cost
            if self._budget_pool:
                self._budget_pool.record("overhead", slice_cost)

            # Parse JSON from response
            result = json.loads(raw) if raw.startswith("{") else json.loads(
                raw[raw.find("{"):raw.rfind("}") + 1])
            self._log(f"Slice translated: '{slice_desc[:50]}' → {result}")
            return result
        except Exception as e:
            self._log(f"Slice translation failed: {e}. Falling back to keyword.")
            return {"keyword": " ".join(slice_desc.split()[:3])}

    # === LLM Call ===

    async def _reason(self) -> dict | None:
        """Single LLM call with formation-time assessment."""
        # Engagement lead (depth 0) gets corpus metadata, not sample records.
        # This prevents scope confusion where it reasons about 100 records
        # as if they were the full corpus.
        if self.depth == 0 and not self.directive.partition:
            cat_meta = self.data_source.catalog_metadata() if hasattr(self.data_source, 'catalog_metadata') else {}
            total = cat_meta.get("total_records", 0)
            fields = cat_meta.get("fields", [])
            field_summary = ", ".join(f["name"] for f in fields[:10])
            documents = [{
                "_corpus_metadata": True,
                "total_records": total,
                "fields": field_summary,
                "note": (f"This is corpus metadata, not sample records. "
                         f"The corpus contains {total:,} records with fields: {field_summary}. "
                         f"You are the engagement lead — your job is to design the team, "
                         f"not analyze records directly. Hire specialists and partition "
                         f"the corpus across them."),
            }]
            # Skip partition/filter paths — go straight to prompt construction
            self._translation_interpretation = ""
            # Jump past the data-fetching block
            fetched_data = _format_documents(documents)
            return await self._reason_with_data(documents, fetched_data)

        # Fetch data — partition path or legacy filter path
        documents = None
        self._translation_interpretation = ""

        if self.directive.partition:
            # Partition path: translate natural-language description via EQUIP translator
            from .translator import translate_partition
            run_dir = str(Path(self._workspace_path).parent) if self._workspace_path else None
            translation = await translate_partition(
                partition=self.directive.partition,
                data_source=self.data_source,
                max_records=100,
                run_dir=run_dir,
                hire_id=self.node_id[:8],
            )
            self.spent += translation.cost
            if self._budget_pool:
                self._budget_pool.record("overhead", translation.cost)

            if translation.success and translation.records:
                documents = translation.records
                self._translation_interpretation = translation.interpretation
                self._log(f"Partition translated: '{self.directive.partition[:50]}' → "
                          f"{translation.record_count} records (${translation.cost:.3f})")
            else:
                self._log(f"Partition translation failed: {translation.error}. "
                          f"Falling back to filter path.")
                # Fall through to legacy filter path

        if documents is None:
            # Legacy filter path (data_filter or scope.filters)
            filters = dict(self.directive.data_filter or self.directive.scope.filters)

            # Slice translation: if filters contain a "slice" key, translate to catalog fields
            if "slice" in filters:
                slice_desc = filters.pop("slice")
                if isinstance(slice_desc, str) and slice_desc.strip():
                    translated = await self._translate_slice(slice_desc)
                    filters.update(translated)

            if hasattr(self.data_source, 'valid_filter_params'):
                valid_params = self.data_source.valid_filter_params()
                unknown = [k for k in filters if k not in valid_params]
                if unknown:
                    self._log(f"Filter validation: unknown params {unknown}. "
                              f"Valid: keyword, packages, or catalog fields: "
                              f"{sorted(self.data_source.CATALOG_FIELDS) if hasattr(self.data_source, 'CATALOG_FIELDS') else sorted(valid_params)}")
                    filters = {k: v for k, v in filters.items() if k in valid_params}

            documents = await self.data_source.fetch(filters, max_results=100)

        if not documents:
            return None

        # Scope-fit check: do the records match the partition intent?
        if self.directive.partition and self._translation_interpretation and not self.envelope_exhausted:
            scope_fit = await self._check_scope_fit(documents)
            if scope_fit == "MISMATCH":
                self._log(f"Scope-fit MISMATCH: records don't match partition intent")
                # Return a result that surfaces the mismatch to the parent
                return {
                    "observations": [],
                    "hire_directives": [],
                    "formation_assessment": {
                        "decision": "investigate",
                        "reasoning": f"SCOPE_FIT_MISMATCH: partition '{self.directive.partition[:80]}' "
                                     f"translated as '{self._translation_interpretation}' but "
                                     f"records don't match scope '{self.directive.scope.description[:100]}'"
                    },
                    "self_evaluation": {"bar_met": False, "purpose_addressed": False,
                                        "purpose_gap": "Records did not match partition intent"},
                }

        # Format data and build prompt
        fetched_data = _format_documents(documents)
        return await self._reason_with_data(documents, fetched_data)

    async def _reason_with_data(self, documents: list, fetched_data: str) -> dict | None:
        """Build prompt and make LLM call. Shared by engagement lead and worker paths."""
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
        charter_exclusions = ""
        if self._workspace_path:
            from .workspace import OrgWorkspace
            ws = OrgWorkspace(self._workspace_path)
            charter = ws.read_charter()
            rules = ws.read_rules()
            if charter:
                workspace_context += f"## ORGANIZATIONAL CHARTER\n\n{charter}\n\n"
                # Extract EXCLUSIONS section for inline use at decision points
                charter_exclusions = _extract_charter_section(charter, "EXCLUSIONS")
            if rules:
                workspace_context += f"## RULES OF ENGAGEMENT\n\n{rules}\n\n"

        # Filter schema
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

        # Bulletin board — pull at formation
        board_text = self._pull_board()
        board_context = ""
        if board_text:
            board_context = (
                f"BULLETIN BOARD ({len(self._bulletin_board.posts)} posts from other nodes):\n"
                f"Posts are observations, hypotheses, or dead ends shared by other nodes in this engagement.\n"
                f"You must produce independent observations to meet your bar. Board content is context, not a substitute.\n\n"
                f"{board_text}"
            )

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
            role_mission=self._role.mission or "Produce the most insightful findings this data can yield.",
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
            board_context=board_context,
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
            "decision": "became_manager" if (self.child_workers and hasattr(self, '_became_manager')) else ("hired" if self.child_workers else "investigated"),
            "decision_reasoning": "",
            "turn2_result": getattr(self, '_turn2_result', None),
            "reassessment_turns": len([t for t in self.thinking_log if t.get("turn") in ("reassessment", "extension")]),
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
            "turn2_result": getattr(self, '_turn2_result', None),
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


def _extract_charter_section(charter: str, section_name: str) -> str:
    """Extract a named section from the four-section charter format.

    Looks for ## SECTION_NAME and returns content up to the next ## header.
    Returns empty string if section not found.
    """
    marker = f"## {section_name.upper()}"
    idx = charter.find(marker)
    if idx < 0:
        # Try without ##
        marker = section_name.upper()
        idx = charter.find(marker)
        if idx < 0:
            return ""

    # Find the end — next ## header or end of text
    content_start = charter.find("\n", idx) + 1
    next_section = charter.find("\n## ", content_start)
    if next_section < 0:
        section_text = charter[content_start:]
    else:
        section_text = charter[content_start:next_section]
    return section_text.strip()


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
