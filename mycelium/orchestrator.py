"""Recursive Orchestrator — manages the exploration lifecycle.

Pipeline: Genesis → Planner → Explore → Deep-dive → Validate → Significance → Impact → Report

The orchestrator is pure plumbing:
- Runs genesis to survey corpus shape
- Runs planner to create budget-aware strategy
- Executes the plan with parallel siblings and incremental synthesis
- Runs deep-dives on the most interesting findings
- Validates Tier 3-5 findings
- Runs impact analysis on validated findings
- Tracks spending across a shared budget pool

ONE hardcoded safety check: chain circuit breaker (MAX_CHAIN_DEPTH=8).
"""

import asyncio
import json
import time
import uuid
from pathlib import Path

from .schemas import (
    Directive, Scope, ExplorationStats, NodeResult, SynthesisResult,
    BudgetPool, ValidationResult, ImpactResult,
)
from .genesis import run_genesis
from .planner import create_plan
from .survey import ProgrammaticSurvey
from .node import run_node
from .synthesizer import synthesize
from .validator import validate_finding
from .significance import assess_significance
from .impact import analyze_impact
from .prompts import DEEP_DIVE_SELECTION_PROMPT
from . import events
from .knowledge_graph import KnowledgeGraph

import anthropic

MAX_CHAIN_DEPTH = 8


class Orchestrator:
    def __init__(self, data_source, budget: float = None, output_dir: str = "output",
                 hints: list[str] = None, visualize: bool = False):
        self.data_source = data_source
        self._initial_budget = budget  # None = interactive (user picks in browser)
        self.budget = BudgetPool(total_budget=budget or 10.0)  # placeholder until user picks
        self.output_dir = output_dir
        self.hints = hints or []
        self.visualize = visualize

        self.stats = ExplorationStats()
        self.all_node_results: list[NodeResult] = []
        self.all_syntheses: list[SynthesisResult] = []
        self.all_validations: list[ValidationResult] = []
        self.all_significance_scores: list[dict] = []
        self.all_impacts: list[ImpactResult] = []
        self.genesis_result: dict = {}
        self.plan: dict = {}
        self.start_time: float = 0.0

        self._branch_counts: list[int] = []
        self._semaphore = asyncio.Semaphore(3)
        self._node_times: list[float] = []  # seconds per node for ETA
        self._planned_nodes: int = 0
        self._last_progress_time: float = 0.0

        self.run_id = str(uuid.uuid4())[:8]
        self.run_dir = Path(output_dir) / self.run_id
        self.run_dir.mkdir(parents=True, exist_ok=True)
        (self.run_dir / "nodes").mkdir(exist_ok=True)

        # Knowledge graph — persists across runs
        self.kg = KnowledgeGraph(str(Path(output_dir) / "knowledge.db"))

    # --- Logging ---

    def _log(self, position: str, message: str):
        depth = position.count(".") if position != "ROOT" else 0
        indent = "│ " * depth
        cost_str = f"${self.budget.spent:.2f}/{self.budget.total:.2f}"
        print(f"  {indent}[{position}] {message} ({cost_str})")

    def _log_progress(self):
        """Print live progress with ETA. Throttled to every 30s."""
        now = time.time()
        if now - self._last_progress_time < 30:
            return
        self._last_progress_time = now

        elapsed = now - self.start_time
        elapsed_min = int(elapsed / 60)
        elapsed_sec = int(elapsed % 60)

        avg_bf = (sum(self._branch_counts) / len(self._branch_counts)
                  if self._branch_counts else 0)
        self.stats.avg_branching_factor = avg_bf

        pct_used = 100 - self.budget.remaining_pct()
        bar_filled = int(pct_used / 6.67)  # 15 chars total
        bar = '█' * bar_filled + '░' * (15 - bar_filled)

        # ETA calculation
        eta_str = ""
        if len(self._node_times) >= 3 and self._planned_nodes > 0:
            avg_time = sum(self._node_times[-20:]) / len(self._node_times[-20:])
            remaining_nodes = max(0, self._planned_nodes - self.stats.nodes_spawned)
            est_remaining = remaining_nodes * avg_time + 120  # +2min for synthesis/validation
            eta_min = int(est_remaining / 60)
            throughput = len(self._node_times) / elapsed if elapsed > 0 else 0
            eta_str = f" | ETA: ~{eta_min}min | {throughput:.1f} nodes/min"

        print(f"\n  Nodes: {self.stats.nodes_spawned}"
              f"{'/' + str(self._planned_nodes) + ' planned' if self._planned_nodes else ''}"
              f" | Budget: {bar} ${self.budget.spent:.2f}/${self.budget.total:.2f} ({pct_used:.0f}%)"
              f" | {elapsed_min}m{elapsed_sec:02d}s{eta_str}"
              f" | depth {self.stats.max_depth_reached}, branch {avg_bf:.1f}\n")

    # --- Main pipeline ---

    async def explore(self) -> dict:
        self.start_time = time.time()

        # Start event recording and visualizer (if not already started by interactive mode)
        if self.visualize and events._server is None:
            await events.start_server(run_dir=str(self.run_dir))
            await asyncio.sleep(1)
        elif not self.visualize:
            events.start_recording(str(self.run_dir))

        source_name = getattr(self.data_source, 'source_name', self.data_source.__class__.__name__)
        print(f"\n╔{'═'*54}╗")
        print(f"║  MYCELIUM — Engineered Curiosity{' '*21}║")
        print(f"║  Source: {source_name[:44]}{' '*(44-len(source_name[:44]))}║")
        print(f"║  Budget: ${self.budget.total:.2f}{' '*(44-len(f'{self.budget.total:.2f}'))}║")
        print(f"╚{'═'*54}╝\n")
        events.emit("source_info", {"source_name": source_name})

        # --- Phase -1: Catalog (programmatic, free) ---
        print("  [CATALOG] Scanning all accessible records...")
        events.emit("phase_change", {"phase": "catalog"})

        catalog_stats = None
        if hasattr(self.data_source, 'fetch_bulk_metadata'):
            def _catalog_progress(p):
                fetched = p.get("fetched", 0)
                total = p.get("total_estimated", "?")
                print(f"\r  [CATALOG] {fetched}/{total} records scanned", end="", flush=True)
                events.emit("catalog_progress", {
                    "scanned": fetched,
                    "total": total,
                    "percent": round(fetched / total * 100) if isinstance(total, int) and total > 0 else 0,
                })

            bulk_records = await self.data_source.fetch_bulk_metadata(
                max_records=2000, progress_callback=_catalog_progress)
            print()  # newline after \r progress

            if bulk_records:
                survey_engine = ProgrammaticSurvey()
                catalog_stats = survey_engine.analyze(bulk_records)
                n_records = catalog_stats["record_count"]
                n_clusters = len(catalog_stats.get("anomaly_clusters", []))
                n_outliers = len(catalog_stats.get("outliers", []))
                n_concentrations = len(catalog_stats.get("concentrations", []))

                print(f"  [CATALOG] {n_records} records cataloged — "
                      f"{n_clusters} anomaly clusters, "
                      f"{n_outliers} outliers, "
                      f"{n_concentrations} concentrations")

                events.emit("catalog_complete", {
                    "total_records": n_records,
                    "anomaly_clusters": n_clusters,
                    "outliers": n_outliers,
                    "concentrations": n_concentrations,
                    "clusters": [
                        {"name": c["name"], "severity": c["severity"],
                         "evidence_count": c.get("evidence_count", 0),
                         "description": c.get("description", "")}
                        for c in catalog_stats.get("anomaly_clusters", [])
                    ],
                    "top_distributions": {
                        k: v for k, v in list(catalog_stats.get("distributions", {}).items())[:6]
                    },
                })

                # Translate raw stats into human-readable descriptions (one cheap LLM call)
                print("  [CATALOG] Translating findings into plain language...")
                catalog_stats = await self._translate_catalog(catalog_stats)

                for c in catalog_stats.get("anomaly_clusters", [])[:5]:
                    desc = c.get("plain_description", c["name"])
                    print(f"    [{c['severity'].upper()}] {desc}")
        else:
            print("  [CATALOG] Data source does not support bulk metadata — skipping")

        # --- Budget authorization (interactive if --budget not provided) ---
        if self._initial_budget is None and self.visualize:
            # Interactive mode: present tiers and wait for user choice
            tiers = self._compute_tiers(catalog_stats)
            events.emit("present_tiers", {
                "tiers": tiers,
                "catalog_records": catalog_stats.get("record_count", 0) if catalog_stats else 0,
            })
            print(f"\n  [WAITING] Budget tiers sent to visualizer — waiting for user selection...")

            choice = await events.wait_for_client_message(timeout=600)
            if not choice:
                print("  [ERROR] No budget selection received. Aborting.")
                return self._build_exploration_data()

            selected_budget = float(choice.get("budget", 10))
            print(f"  [AUTHORIZED] User selected ${selected_budget:.2f}")
            self.budget = BudgetPool(total_budget=selected_budget)
            events.emit("budget_authorized", {"budget": selected_budget})

        # --- Phase 0: Genesis (LLM reasons about the catalog statistics) ---
        print(f"\n  [GENESIS] Surveying corpus structure...")
        events.emit("phase_change", {"phase": "genesis"})
        self.genesis_result = await run_genesis(self.data_source, self.hints)
        self.budget.record("overhead", self.genesis_result.get("cost", 0))
        self._update_tokens(self.genesis_result.get("token_usage", {}))

        lenses = self.genesis_result.get("lenses", [])
        summary = self.genesis_result.get("corpus_summary", "")
        structure = self.genesis_result.get("natural_structure", {})

        print(f"  [GENESIS] {summary[:100]}...")
        print(f"  [GENESIS] Lenses: {', '.join(lenses[:8])}...")

        # Emit genesis reasoning so visualizer can show the thinking
        events.emit("genesis_reasoning", {
            "corpus_summary": summary,
            "lenses": lenses,
            "entry_points": [ep.get("area", "") for ep in self.genesis_result.get("suggested_entry_points", [])],
            "recommended_cut": structure.get("recommended_first_cut", ""),
            "division_axes": structure.get("division_axes", []),
        })

        if not lenses:
            print("  [GENESIS] ERROR: No lenses. Aborting.")
            return self._build_exploration_data()

        # --- Phase 1: Planner ---
        print(f"\n  [PLANNER] Creating exploration strategy...")
        self.plan = await create_plan(self.genesis_result, self.budget.total)
        self.budget.record("overhead", self.plan.get("cost", 0))
        self._update_tokens(self.plan.get("token_usage", {}))

        segments = self.plan.get("segments", [])
        seg_targets = {s["name"]: s.get("sub_budget", 0) for s in segments}
        self.budget.set_segment_targets(seg_targets)
        self._planned_nodes = self.plan.get("estimated_total_nodes", 0)

        print(f"  [PLANNER] Plan: {len(segments)} segments, "
              f"~{self._planned_nodes or '?'} nodes")
        for seg in segments:
            print(f"  ├── {seg['name']:25s} ${seg.get('sub_budget', 0):.2f} "
                  f"(~{seg.get('estimated_nodes', '?')} nodes)")
        dd_reserve = self.plan.get("deep_dive_reserve", 0)
        print(f"  └── Deep-dive reserve      ${dd_reserve:.2f}")

        # Emit planner reasoning so visualizer shows the strategy
        events.emit("planner_reasoning", {
            "segment_count": len(segments),
            "estimated_nodes": self._planned_nodes,
            "segments": [
                {"name": s["name"], "budget": s.get("sub_budget", 0),
                 "nodes": s.get("estimated_nodes", 0),
                 "reasoning": s.get("reasoning", "")}
                for s in segments
            ],
            "deep_dive_reserve": dd_reserve,
            "deep_dive_strategy": self.plan.get("deep_dive_strategy", ""),
        })

        # --- Phase 2: Exploration ---
        print(f"\n  {'─'*54}")
        print(f"  PHASE 1: EXPLORATION")
        events.emit("phase_change", {"phase": "exploration"})
        print(f"  {'─'*54}\n")

        # Create root that decomposes into plan segments
        root_directives = []
        for i, seg in enumerate(segments, 1):
            root_directives.append(Directive(
                scope=Scope(
                    source=self.data_source.__class__.__name__,
                    filters=seg.get("filters", {"keyword": seg["name"]}),
                    description=seg.get("scope_description", seg["name"]),
                ),
                lenses=lenses,
                parent_context=f"Planner assigned this segment: {seg.get('reasoning', '')}",
                tree_position=str(i),
                segment_id=seg["name"],
            ))

        # Run all segments in parallel
        segment_tasks = [self._explore_node(d) for d in root_directives]
        segment_results = list(await asyncio.gather(*segment_tasks))

        # Root-level synthesis across all segments
        if len(segment_results) > 1 and self.budget.can_spend():
            print(f"\n  [ROOT] SYNTHESIZING all {len(segment_results)} segments...")
            # Build a virtual root result for synthesis
            virtual_root = NodeResult(
                node_id="root", parent_id=None,
                scope_description="Root synthesis across all segments",
                survey="", observations=[], child_directives=[],
                unresolved=[], raw_reasoning="",
            )
            root_synthesis = await synthesize(virtual_root, segment_results, lenses)
            self.all_syntheses.append(root_synthesis)
            self.budget.record("synthesis", root_synthesis.cost)
            self._update_tokens(root_synthesis.token_usage)

        self._log_progress()

        # --- Phase 3: Deep-dives ---
        await self._run_deep_dives(lenses)

        # --- Phase 4: Validation ---
        await self._run_validation()

        # --- Phase 5: Significance gate ---
        await self._run_significance_gate()

        # --- Phase 6: Impact (only for headline/significant findings) ---
        await self._run_impact_analysis()

        # --- Done ---
        elapsed = time.time() - self.start_time
        minutes, seconds = int(elapsed // 60), int(elapsed % 60)

        print(f"\n  {'─'*54}")
        print(f"  COMPLETE — {self.stats.nodes_spawned} nodes, "
              f"{self.stats.observations_collected} obs, "
              f"depth {self.stats.max_depth_reached}")
        print(f"  Cost: ${self.budget.spent:.2f}/{self.budget.total:.2f} "
              f"({100-self.budget.remaining_pct():.0f}% used) — {minutes}m {seconds}s")
        print(f"  Validated: {self.stats.findings_confirmed}/{self.stats.findings_validated} confirmed"
              f"  Deep-dives: {self.stats.deep_dives_executed}")
        print(f"  Phases: explore=${self.budget.phase_spent.get('exploration',0):.2f} "
              f"synth=${self.budget.phase_spent.get('synthesis',0):.2f} "
              f"dive=${self.budget.phase_spent.get('deep_dive',0):.2f} "
              f"valid=${self.budget.phase_spent.get('validation',0):.2f} "
              f"impact=${self.budget.phase_spent.get('impact',0):.2f} "
              f"overhead=${self.budget.phase_spent.get('overhead',0):.2f}")
        print(f"  {'─'*54}")

        exploration_data = self._build_exploration_data()
        self._save_tree(exploration_data)

        # Save KG export alongside tree
        kg_stats = self.kg.stats()
        kg_export_path = self.run_dir / "knowledge_graph.json"
        with open(kg_export_path, "w") as f:
            json.dump(self.kg.export_json(), f, indent=2, default=str)
        print(f"\n  Knowledge graph: {kg_stats['entities']} entities, "
              f"{kg_stats['relationships']} relationships, "
              f"{kg_stats['contradictions']} contradictions")

        events.emit("exploration_complete", {
            "total_nodes": self.stats.nodes_spawned,
            "total_observations": self.stats.observations_collected,
            "max_depth": self.stats.max_depth_reached,
            "total_cost": self.budget.spent,
            "elapsed_time": time.time() - self.start_time,
        })

        if self.visualize:
            await asyncio.sleep(2)  # let final events reach the browser
            await events.stop_server()

        return exploration_data

    # --- Recursive exploration ---

    async def _explore_node(self, directive: Directive) -> NodeResult:
        """Recursively explore. Siblings in parallel. Synthesis on completion.

        Uses atomic budget reservation: reserve before LLM call, commit after.
        Exploration phase has a hard limit — when exhausted, nodes return without work.
        """
        pos = directive.tree_position
        seg_id = directive.segment_id
        depth = pos.count(".") + 1 if pos != "ROOT" else 0

        # Atomic budget reservation — prevents race condition
        estimated_cost = self._avg_node_cost()
        reserved = await self.budget.reserve(estimated_cost, phase="exploration")
        if not reserved:
            self._log(pos, "BUDGET EXHAUSTED — skipping")
            return NodeResult(
                node_id=directive.node_id, parent_id=directive.parent_id,
                scope_description=directive.scope.description,
                survey="Exploration budget exhausted.",
                observations=[], child_directives=[],
                unresolved=[f"Scope '{directive.scope.description[:80]}' — budget exhausted"],
                raw_reasoning="", token_usage={}, cost=0.0,
            )

        # Build context strings for the node prompt
        seg_ctx = ""
        if seg_id:
            status = self.budget.segment_status(seg_id)
            seg_ctx = (
                f"- Segment '{seg_id}' target: ${status['target']:.2f}, "
                f"spent: ${status['spent']:.2f}, "
                f"remaining: ${status['remaining_target']:.2f}"
                f"{' (OVER TARGET — be selective)' if status['over_target'] else ''}\n"
            )

        capacity_ctx = self._capacity_context()

        self.stats.nodes_spawned += 1
        self.stats.max_depth_reached = max(self.stats.max_depth_reached, depth)

        self._log(pos, f"Exploring: {directive.scope.description[:60]}...")
        events.emit("node_spawned", {
            "node_id": directive.node_id, "parent_id": directive.parent_id,
            "tree_position": pos, "scope_summary": directive.scope.description[:100],
            "segment_id": seg_id,
        })

        # Semaphore only wraps the LLM call, NOT recursive children.
        node_start = time.time()
        try:
            async with self._semaphore:
                node_result = await run_node(
                    directive, self.data_source,
                    self.budget.remaining(), self.budget.total,
                    segment_context=seg_ctx,
                    capacity_context=capacity_ctx,
                )
            self._node_times.append(time.time() - node_start)
            await self.budget.commit(estimated_cost, node_result.cost, "exploration", seg_id)
        except Exception:
            await self.budget.release(estimated_cost)
            raise

        self.all_node_results.append(node_result)
        self.stats.observations_collected += len(node_result.observations)
        self._update_tokens(node_result.token_usage)
        self._save_node(node_result)
        self._populate_kg(node_result)
        self._emit_reasoning(directive.node_id, node_result.raw_reasoning)

        n_obs = len(node_result.observations)
        n_children = len(node_result.child_directives)

        events.emit("budget_update", {
            "total_spent": self.budget.spent, "total_remaining": self.budget.remaining(),
            "percent_used": 100 - self.budget.remaining_pct(),
        })

        if n_children > 0:
            self._branch_counts.append(n_children)
            self._log(pos, f"{n_obs} observations + decomposing → {n_children} areas")
            events.emit("node_decomposing", {
                "node_id": directive.node_id, "tree_position": pos,
                "children_count": n_children, "observations_count": n_obs,
            })
        else:
            self.stats.nodes_resolved += 1
            self._log(pos, f"RESOLVED: {n_obs} observations")
            top_obs = node_result.observations[0].what_i_saw[:80] if node_result.observations else ""
            events.emit("node_resolved", {
                "node_id": directive.node_id, "tree_position": pos,
                "observations_count": n_obs, "cost_spent": node_result.cost,
                "top_observation": top_obs,
            })

        if not node_result.child_directives:
            return node_result

        # Check exploration hard gate before spawning children
        if self.budget.exploration_exhausted:
            self._log(pos, "Exploration phase limit reached — not spawning children")
            return node_result

        # Propagate segment_id to children
        for child in node_result.child_directives:
            if not child.segment_id:
                child.segment_id = seg_id

        # Run children in parallel — each child reserves its own budget atomically
        tasks = [self._explore_node(child) for child in node_result.child_directives]
        children_results = list(await asyncio.gather(*tasks)) if tasks else []

        # Incremental synthesis
        if children_results and self.budget.can_spend():
            self._log(pos, f"SYNTHESIZING {len(children_results)} branches...")
            events.emit("node_status", {"node_id": directive.node_id, "tree_position": pos, "status": "synthesizing"})
            # Light synthesis for leaf parents (all children resolved directly),
            # full synthesis for branch parents (children had their own children)
            all_leaves = all(len(c.child_directives) == 0 for c in children_results)
            syn = await synthesize(node_result, children_results, directive.lenses, light=all_leaves)
            self.all_syntheses.append(syn)
            self.budget.record("synthesis", syn.cost, seg_id)
            self._update_tokens(syn.token_usage)
            for c in syn.contradictions:
                events.emit("finding_discovered", {"node_id": directive.node_id, "summary": str(c.get("what_conflicts", ""))[:80], "type": "contradiction"})
            for p in syn.cross_cutting:
                events.emit("finding_discovered", {"node_id": directive.node_id, "summary": str(p.get("pattern", ""))[:80], "type": "cross_cutting"})

            for child in children_results:
                node_result.observations.extend(child.observations)
        elif children_results:
            for child in children_results:
                node_result.observations.extend(child.observations)

        self._log_progress()

        return node_result

    # --- Deep-dive phase ---

    async def _run_deep_dives(self, lenses: list[str]):
        available = self.budget.deep_dive_available()
        if available < 0.05:
            print("\n  ⚠ DEEP-DIVE SKIPPED — insufficient budget remaining")
            return

        # Collect findings for selection
        findings_summary = self._summarize_findings()
        observations_summary = self._summarize_top_observations()

        if not findings_summary and not observations_summary:
            print("\n  ⚠ DEEP-DIVE SKIPPED — no findings to investigate")
            return

        print(f"\n  {'─'*54}")
        print(f"  PHASE 2: DEEP DIVES (${available:.2f} available)")
        events.emit("phase_change", {"phase": "deep_dive"})
        print(f"  {'─'*54}\n")

        # Ask LLM which findings to investigate
        prompt = DEEP_DIVE_SELECTION_PROMPT.format(
            findings_summary=findings_summary or "(no synthesis findings yet)",
            observations_summary=observations_summary,
            deep_dive_budget=available,
        )

        client = anthropic.Anthropic()
        response = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=1500,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = response.content[0].text
        usage = {"input_tokens": response.usage.input_tokens, "output_tokens": response.usage.output_tokens}
        cost = (usage["input_tokens"] * 3 + usage["output_tokens"] * 15) / 1_000_000
        self.budget.record("deep_dive", cost)
        self._update_tokens(usage)

        try:
            selection = _parse_json(raw)
        except (json.JSONDecodeError, ValueError):
            print("  Deep-dive selection failed to parse. Skipping.")
            return

        targets = selection.get("targets", [])
        if not targets:
            print("  No deep-dive targets selected.")
            return

        for i, target in enumerate(targets, 1):
            if not self.budget.can_spend():
                break

            desc = target.get("investigation_directive", target.get("finding_summary", ""))
            filters = target.get("search_filters", {})
            print(f"  [DEEP-DIVE {i}] {desc[:70]}...")

            directive = Directive(
                scope=Scope(
                    source=self.data_source.__class__.__name__,
                    filters=filters,
                    description=desc,
                ),
                lenses=lenses,
                parent_context=f"DEEP DIVE: {target.get('why_this_one', '')}. "
                               f"This is targeted investigation — trace this finding to its full extent.",
                tree_position=f"DD.{i}",
                segment_id="deep_dive",
            )

            result = await run_node(
                directive, self.data_source,
                self.budget.remaining(), self.budget.total,
            )
            self.all_node_results.append(result)
            self.stats.observations_collected += len(result.observations)
            self.stats.deep_dives_executed += 1
            self.budget.record("deep_dive", result.cost)
            self._update_tokens(result.token_usage)
            self._save_node(result)

            n_obs = len(result.observations)
            print(f"  [DEEP-DIVE {i}] RESOLVED: {n_obs} observations")

    # --- Validation ---

    async def _run_validation(self):
        findings = []
        for syn in self.all_syntheses:
            for c in syn.contradictions:
                findings.append(("contradiction", c))
            for p in syn.cross_cutting:
                findings.append(("cross_cutting_pattern", p))

        if not findings:
            print("\n  ⚠ VALIDATION SKIPPED — no Tier 3-5 findings to validate")
            return

        print(f"\n  {'─'*54}")
        print(f"  PHASE 3: VALIDATION ({len(findings)} findings)")
        events.emit("phase_change", {"phase": "validation"})
        print(f"  {'─'*54}\n")

        for i, (ftype, finding) in enumerate(findings, 1):
            if not self.budget.can_spend():
                print(f"  ⚠ VALIDATION TRUNCATED — budget exhausted after {i-1}/{len(findings)} validations")
                break

            desc = str(finding.get("what_conflicts", finding.get("pattern", "")))[:60]
            print(f"  [VALIDATE {i}/{len(findings)}] {ftype}: {desc}...")

            result = await validate_finding(f"{ftype}_{i}", ftype, finding)
            self.all_validations.append(result)
            self.budget.record("validation", result.cost)
            self._update_tokens(result.token_usage)
            self.stats.findings_validated += 1
            if result.verdict == "confirmed":
                self.stats.findings_confirmed += 1
            print(f"    → {result.verdict} (confidence: {result.adjusted_confidence:.2f})")
            events.emit("validation_result", {
                "finding_summary": desc,
                "verdict": result.verdict,
                "confidence": result.adjusted_confidence,
            })

    # --- Significance gate ---

    async def _run_significance_gate(self):
        """Filter validated findings by novelty and actionability before impact analysis."""
        confirmed = [v for v in self.all_validations if v.verdict in ("confirmed", "weakened", "needs_verification")]
        if not confirmed:
            return

        print(f"\n  {'─'*54}")
        print(f"  PHASE 5: SIGNIFICANCE GATE ({len(confirmed)} findings)")
        events.emit("phase_change", {"phase": "significance"})
        print(f"  {'─'*54}\n")

        self.all_significance_scores = []
        for i, v in enumerate(confirmed, 1):
            if not self.budget.can_spend():
                print(f"  Budget exhausted after {i-1}/{len(confirmed)} significance checks")
                break

            desc = str(v.revised_finding or v.original_finding.get("what_conflicts", "")
                       or v.original_finding.get("pattern", ""))[:60]
            print(f"  [SIGNIFICANCE {i}/{len(confirmed)}] {desc}...")

            result = await assess_significance(
                v.finding_id, v.original_finding,
                {"verdict": v.verdict, "revised_finding": v.revised_finding,
                 "adjusted_confidence": v.adjusted_confidence})
            self.all_significance_scores.append(result)
            self.budget.record("validation", result.get("cost", 0))
            self._update_tokens(result.get("token_usage", {}))

            score = result.get("composite_score", 0)
            tier = result.get("tier_assignment", "noted")
            headline = result.get("headline", "")[:50]
            print(f"    → {tier.upper()} (score: {score:.1f}) — {headline}")
            events.emit("significance_scored", {
                "finding_summary": desc,
                "composite": score,
                "tier": tier,
                "novelty": result.get("novelty", 0),
                "actionability": result.get("actionability", 0),
            })

        # Summary
        headlines = sum(1 for s in self.all_significance_scores if s.get("tier_assignment") == "headline")
        significant = sum(1 for s in self.all_significance_scores if s.get("tier_assignment") == "significant")
        noted = sum(1 for s in self.all_significance_scores if s.get("tier_assignment") == "noted")
        print(f"\n  Gate results: {headlines} headlines, {significant} significant, {noted} noted")

    # --- Impact (only for headline/significant findings) ---

    async def _run_impact_analysis(self):
        # Only run impact on findings that passed significance gate (3.0+)
        to_impact = [s for s in getattr(self, 'all_significance_scores', [])
                     if s.get("recommendation") == "proceed_to_impact"
                     or s.get("tier_assignment") in ("headline", "significant")]

        # Sort by score — highest first so we get the best findings if budget runs out
        to_impact.sort(key=lambda s: s.get("composite_score", 0), reverse=True)

        if not to_impact:
            print("\n  ⚠ IMPACT SKIPPED — no findings scored high enough for impact analysis")
            return

        # Match significance scores back to validation results
        validated_map = {v.finding_id: v for v in self.all_validations}

        print(f"\n  {'─'*54}")
        print(f"  PHASE 6: IMPACT ANALYSIS ({len(to_impact)} findings, ${self.budget.remaining():.2f} available)")
        events.emit("phase_change", {"phase": "impact"})
        print(f"  {'─'*54}\n")

        for i, sig in enumerate(to_impact, 1):
            if not self.budget.can_spend(0.01):  # lower threshold — impact is critical
                print(f"  ⚠ Budget exhausted after {i-1}/{len(to_impact)} impact analyses")
                break
            fid = sig.get("finding_id", "")
            v = validated_map.get(fid)
            if not v:
                print(f"  ⚠ Finding {fid} not found in validation map — skipping")
                continue

            desc = sig.get("headline", str(v.revised_finding or v.original_finding.get("pattern", "")))
            print(f"  [IMPACT {i}/{len(to_impact)}] {str(desc)[:60]}...")

            result = await analyze_impact(
                fid, str(desc), v.original_finding, v.adjusted_confidence)
            self.all_impacts.append(result)
            self.budget.record("impact", result.cost)
            self._update_tokens(result.token_usage)
            print(f"    → Urgency: {result.urgency}")
            events.emit("impact_result", {
                "finding_summary": str(desc)[:100],
                "urgency": result.urgency,
                "who_affected": ", ".join(result.affected_parties[:3]),
                "action": result.actionability[:100] if result.actionability else "",
            })

    # --- Budget & context helpers ---

    def _avg_node_cost(self) -> float:
        """Running average cost per node. Used for budget reservations."""
        if len(self.all_node_results) >= 5:
            recent = [r.cost for r in self.all_node_results[-20:] if r.cost > 0]
            if recent:
                return sum(recent) / len(recent)
        return 0.05  # default estimate before we have data

    async def _translate_catalog(self, catalog_stats: dict) -> dict:
        """One cheap LLM call to translate raw statistical cluster names into plain language."""
        clusters = catalog_stats.get("anomaly_clusters", [])
        if not clusters:
            return catalog_stats

        cluster_summaries = json.dumps([
            {"name": c.get("name", ""), "severity": c.get("severity", ""),
             "description": c.get("description", ""), "evidence_count": c.get("evidence_count", 0)}
            for c in clusters[:10]
        ], indent=2)

        client = anthropic.Anthropic()
        try:
            response = client.messages.create(
                model="claude-sonnet-4-20250514",
                max_tokens=800,
                messages=[{"role": "user", "content": f"""These are anomaly clusters found by a statistical scan of a dataset.
The names are raw field names and statistical descriptions. Translate each into a clear,
one-sentence plain-language description that a non-technical person would understand.

CLUSTERS:
{cluster_summaries}

Return JSON array — one plain_description per cluster, same order:
[
    "plain language description 1",
    "plain language description 2",
    ...
]

Respond ONLY with a JSON array."""}],
            )
            raw = response.content[0].text
            cost = (response.usage.input_tokens * 3 + response.usage.output_tokens * 15) / 1_000_000
            self.budget.record("overhead", cost)

            # Parse
            start = raw.find("[")
            end = raw.rfind("]") + 1
            if start >= 0 and end > start:
                descriptions = json.loads(raw[start:end])
                for i, desc in enumerate(descriptions):
                    if i < len(clusters):
                        clusters[i]["plain_description"] = desc
        except Exception:
            pass  # if translation fails, raw names are used as fallback

        return catalog_stats

    def _compute_tiers(self, catalog_stats: dict = None) -> list[dict]:
        """Compute investigation tiers from actual catalog data.

        Estimates are based on:
        - Number of anomaly clusters found
        - Number of records cataloged
        - Estimated $0.04/node, ~20 nodes per dollar
        - Estimated ~2 nodes/minute with parallelism
        """
        clusters = catalog_stats.get("anomaly_clusters", []) if catalog_stats else []
        n_clusters = len(clusters)
        n_records = catalog_stats.get("record_count", 0) if catalog_stats else 0

        # Use plain language descriptions if available, fall back to raw names
        cluster_descs = [c.get("plain_description", c.get("name", "unnamed")) for c in clusters]

        def _tier_clusters(n):
            """Top N cluster descriptions."""
            top = cluster_descs[:n]
            if not top:
                return "General exploration"
            return "; ".join(c[:60] for c in top)

        # Estimate nodes and time per budget
        # ~20 nodes per dollar, ~2 nodes per minute
        def _estimate(budget):
            nodes = int(budget * 20)
            minutes = int(nodes / 2)
            if minutes < 60:
                time_str = f"~{minutes} min"
            else:
                time_str = f"~{minutes // 60}h {minutes % 60}m"
            return nodes, time_str

        scout_nodes, scout_time = _estimate(5)
        focused_nodes, focused_time = _estimate(15)
        balanced_nodes, balanced_time = _estimate(50)
        deep_nodes, deep_time = _estimate(200)

        return [
            {"name": "Scout", "budget": 5, "time_estimate": scout_time,
             "estimated_nodes": scout_nodes,
             "clusters_covered": min(3, n_clusters),
             "depth": f"Top {min(3, n_clusters)} anomalies: {_tier_clusters(3)}"},
            {"name": "Focused", "budget": 15, "time_estimate": focused_time,
             "estimated_nodes": focused_nodes,
             "clusters_covered": min(5, n_clusters),
             "depth": f"Top {min(5, n_clusters)} anomalies: {_tier_clusters(5)}"},
            {"name": "Balanced", "budget": 50, "time_estimate": balanced_time,
             "estimated_nodes": balanced_nodes,
             "clusters_covered": n_clusters,
             "depth": f"All {n_clusters} anomaly clusters, thorough analysis"},
            {"name": "Deep", "budget": 200, "time_estimate": deep_time,
             "estimated_nodes": deep_nodes,
             "clusters_covered": n_clusters,
             "depth": f"All {n_clusters} clusters + full cross-referencing of {n_records} records"},
        ]

    def _capacity_context(self) -> str:
        """How many more exploration nodes can we afford, based on actuals."""
        if self.stats.nodes_spawned < 10:
            return ""
        avg = self._avg_node_cost()
        if avg <= 0:
            return ""
        explore_remaining = self.budget.exploration_remaining()
        est_nodes = int(explore_remaining / avg)
        return (
            f"Based on actual costs (avg ${avg:.3f}/node), approximately "
            f"{est_nodes} more exploration nodes can be created. "
            f"Plan your decomposition accordingly."
        )

    def _emit_reasoning(self, node_id: str, raw_reasoning: str):
        """Parse reasoning steps from the LLM's raw text output.

        The node prompt asks the LLM to think through STEP 1-5 before outputting JSON.
        We look for STEP markers in the text to extract summaries of each thinking step.
        """
        if not raw_reasoning:
            return

        text = raw_reasoning
        for step_name in ("SURVEY", "ORIENT", "HYPOTHESIZE", "ASSESS"):
            # Find "STEP N — STEPNAME" or just the step name
            idx = text.upper().find(step_name)
            if idx < 0:
                continue
            # Extract content after the marker
            start = idx + len(step_name)
            while start < len(text) and text[start] in " *\n—-:#_`1234567890.":
                start += 1
            # Find the end — next step or JSON block
            end = len(text)
            for marker in ("STEP", "ORIENT", "HYPOTHESIZE", "ASSESS", "OUTPUT", "```"):
                mi = text.upper().find(marker, start + 20)
                if mi > start and mi < end:
                    end = mi
            snippet = text[start:min(start + 300, end)].strip()
            # Take first 2-3 sentences
            sentences = snippet.replace('\n', ' ').split('. ')
            summary = '. '.join(s.strip() for s in sentences[:3] if s.strip())
            if len(summary) > 220:
                summary = summary[:220] + '...'
            if summary:
                events.emit("node_reasoning", {
                    "node_id": node_id,
                    "step": step_name.lower(),
                    "summary": summary,
                })

    # --- Helpers ---

    def _populate_kg(self, node_result: NodeResult):
        """Extract entities and relationships from observations and add to knowledge graph."""
        for obs in node_result.observations:
            # Add the primary entity (the source item)
            entity_name = obs.source.title or obs.source.doc_id
            if not entity_name:
                continue
            entity_type = "item"
            # Try to infer type from observation_type
            if obs.observation_type in ("dependency_risk", "single_point_of_failure"):
                entity_type = "risk_entity"

            self.kg.add_observation(
                entity_name=entity_name,
                claim=obs.what_i_saw,
                source_node_id=obs.node_id,
                source_run_id=self.run_id,
                confidence=max(obs.preliminary_relevance.values()) if obs.preliminary_relevance else 0.5,
                observation_type=obs.observation_type,
                entity_type=entity_type,
            )

            # Add the author/maintainer as a related entity if present
            if obs.source.agency and obs.source.agency != "unknown":
                self.kg.add_relationship(
                    from_name=entity_name,
                    to_name=obs.source.agency,
                    relationship_type="maintained_by",
                    confidence=0.8,
                    evidence=obs.what_i_saw[:200],
                    from_type=entity_type,
                    to_type="person_or_org",
                )

            # Add connections from potential_connections
            for conn in obs.potential_connections:
                self.kg.add_relationship(
                    from_name=entity_name,
                    to_name=conn,
                    relationship_type="related_to",
                    confidence=0.3,
                    evidence=obs.what_i_saw[:200],
                )

    def _update_tokens(self, usage: dict):
        self.stats.total_tokens += usage.get("input_tokens", 0) + usage.get("output_tokens", 0)
        self.stats.api_calls += 1

    def _summarize_findings(self) -> str:
        lines = []
        for syn in self.all_syntheses:
            for c in syn.contradictions:
                lines.append(f"- CONTRADICTION: {c.get('what_conflicts', '')}")
            for p in syn.cross_cutting:
                lines.append(f"- PATTERN: {p.get('pattern', '')}")
            for q in syn.discovered_questions:
                lines.append(f"- QUESTION: {q}")
        return "\n".join(lines) if lines else ""

    def _summarize_top_observations(self) -> str:
        # Pick the most interesting observations (those with reasoning mentioning risk/anomaly)
        all_obs = []
        for nr in self.all_node_results:
            for obs in nr.get("observations", []) if isinstance(nr, dict) else nr.observations:
                if isinstance(obs, dict):
                    all_obs.append(obs)
                else:
                    all_obs.append({
                        "what_i_saw": obs.what_i_saw,
                        "source": {"doc_id": obs.source.doc_id, "title": obs.source.title},
                        "observation_type": obs.observation_type,
                        "reasoning": obs.reasoning,
                    })

        # Take up to 15 most interesting
        lines = []
        for obs in all_obs[:15]:
            lines.append(f"- [{obs.get('observation_type', '?')}] {obs.get('what_i_saw', '')[:150]}")
        return "\n".join(lines) if lines else ""

    def _build_exploration_data(self) -> dict:
        elapsed = time.time() - self.start_time
        return {
            "run_id": self.run_id,
            "hints": self.hints,
            "plan": self.plan,
            "stats": {
                "nodes_spawned": self.stats.nodes_spawned,
                "nodes_resolved": self.stats.nodes_resolved,
                "observations_collected": self.stats.observations_collected,
                "max_depth_reached": self.stats.max_depth_reached,
                "total_tokens": self.stats.total_tokens,
                "total_cost": self.budget.spent,
                "budget": self.budget.total,
                "phase_costs": dict(self.budget.phase_spent),
                "segment_costs": dict(self.budget.segment_spent),
                "api_calls": self.stats.api_calls,
                "elapsed_seconds": elapsed,
                "avg_branching_factor": self.stats.avg_branching_factor,
                "chain_breaker_fired": self.stats.chain_breaker_fired,
                "findings_validated": self.stats.findings_validated,
                "findings_confirmed": self.stats.findings_confirmed,
                "deep_dives_executed": self.stats.deep_dives_executed,
            },
            "genesis": self.genesis_result,
            "node_results": [_result_to_dict(r) for r in self.all_node_results],
            "syntheses": [_synthesis_to_dict(s) for s in self.all_syntheses],
            "validations": [_validation_to_dict(v) for v in self.all_validations],
            "significance_scores": self.all_significance_scores,
            "impacts": [_impact_to_dict(im) for im in self.all_impacts],
        }

    def _save_node(self, result: NodeResult):
        node_file = self.run_dir / "nodes" / f"{result.node_id[:8]}.json"
        with open(node_file, "w") as f:
            json.dump(_result_to_dict(result), f, indent=2, default=str)

    def _save_tree(self, data: dict):
        tree_file = self.run_dir / "tree.json"
        with open(tree_file, "w") as f:
            json.dump(data, f, indent=2, default=str)
        print(f"\n  Tree saved to {tree_file}")


# --- Serialization ---

def _result_to_dict(r: NodeResult) -> dict:
    return {
        "node_id": r.node_id, "parent_id": r.parent_id,
        "scope_description": r.scope_description, "survey": r.survey,
        "observations": [
            {"what_i_saw": o.what_i_saw,
             "source": {"doc_id": o.source.doc_id, "title": o.source.title,
                        "agency": o.source.agency, "date": o.source.date, "url": o.source.url},
             "observation_type": o.observation_type,
             "preliminary_relevance": o.preliminary_relevance,
             "reasoning": o.reasoning,
             "potential_connections": o.potential_connections}
            for o in r.observations
        ],
        "child_directives_count": len(r.child_directives),
        "unresolved": r.unresolved,
        "raw_reasoning": r.raw_reasoning,
        "token_usage": r.token_usage, "cost": r.cost,
    }

def _synthesis_to_dict(s: SynthesisResult) -> dict:
    return {
        "node_id": s.node_id, "reinforced": s.reinforced,
        "contradictions": s.contradictions, "cross_cutting": s.cross_cutting,
        "discovered_questions": s.discovered_questions,
        "unresolved_threads": s.unresolved_threads,
        "raw_reasoning": s.raw_reasoning, "token_usage": s.token_usage, "cost": s.cost,
    }

def _validation_to_dict(v: ValidationResult) -> dict:
    return {
        "finding_id": v.finding_id, "original_finding": v.original_finding,
        "verdict": v.verdict, "reasoning": v.reasoning,
        "adjusted_confidence": v.adjusted_confidence, "adjusted_tier": v.adjusted_tier,
        "verification_action": v.verification_action, "revised_finding": v.revised_finding,
        "cost": v.cost,
    }

def _impact_to_dict(im: ImpactResult) -> dict:
    return {
        "finding_id": im.finding_id, "affected_parties": im.affected_parties,
        "estimated_scale": im.estimated_scale, "financial_exposure": im.financial_exposure,
        "risk_scenario": im.risk_scenario, "who_needs_to_know": im.who_needs_to_know,
        "urgency": im.urgency, "actionability": im.actionability,
        "reasoning": im.reasoning, "cost": im.cost,
    }

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
