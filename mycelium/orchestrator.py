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
    BudgetPool, ValidationResult, ImpactResult, Observation, Source,
)
from .genesis import run_genesis
from .planner import create_plan
from .survey import ProgrammaticSurvey
from .node import run_node

# Minimum envelope for a productive leaf node. Roughly one Turn 1 with reduced
# thinking budget at current Sonnet pricing ($3/M in, $15/M out). Update if
# model pricing changes significantly.
LEAF_VIABLE_ENVELOPE = 0.12
from .worker import WorkerNode
from .synthesizer import synthesize
from .validator import validate_finding
from .significance import assess_significance
from .impact import analyze_impact
from .prompts import DEEP_DIVE_SELECTION_PROMPT, ANOMALY_ROUTING_PROMPT, ANOMALY_AGGREGATION_PROMPT
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
        bulk_records = None
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

                # Store anomaly data for passing to exploration nodes
                self._survey_anomalies = {
                    "outliers": catalog_stats.get("outliers", []),
                    "unusual_combinations": catalog_stats.get("unusual_combinations", []),
                    "concentrations": catalog_stats.get("concentrations", []),
                    "content_anomalies": catalog_stats.get("content_anomalies", []),
                    "entity_anomalies": catalog_stats.get("entity_anomalies", []),
                    "graph_anomalies": catalog_stats.get("graph_anomalies", []),
                    "similarity_anomalies": catalog_stats.get("similarity_anomalies", []),
                    "velocity_anomalies": catalog_stats.get("velocity_anomalies", []),
                    "anomalies_by_technique": catalog_stats.get("anomalies_by_technique", {}),
                }

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
        # Pass catalog records so genesis can sample from the broader set
        # (2000 records) instead of fetching a fresh ~200 via survey().
        # These are lightweight (headers + descriptions), not full metadata.
        self.genesis_result = await run_genesis(
            self.data_source, self.hints,
            catalog_records=bulk_records,
            survey_results=catalog_stats,
        )
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

        # --- Read planner's exploration envelope (v2) or use static default ---
        envelope_info = self.plan.get("exploration_envelope", {})
        if envelope_info and envelope_info.get("percentage"):
            raw_pct = envelope_info["percentage"]
            clamped_pct = max(0.40, min(0.75, raw_pct))
            if clamped_pct != raw_pct:
                print(f"  [PLANNER] WARNING: exploration envelope {raw_pct:.0%} clamped to {clamped_pct:.0%}")
            self.budget.phase_limits["exploration"] = clamped_pct
            self._planner_envelope = {
                "exploration_envelope_pct": round(clamped_pct, 3),
                "exploration_envelope_dollars": round(self.budget.total * clamped_pct, 2),
                "reasoning": envelope_info.get("reasoning", ""),
                "raw_pct": round(raw_pct, 3),
                "clamped": clamped_pct != raw_pct,
            }
            print(f"  [PLANNER] Exploration envelope: {clamped_pct:.0%} = "
                  f"${self.budget.total * clamped_pct:.2f}")
        else:
            self._planner_envelope = {
                "exploration_envelope_pct": self.budget.phase_limits["exploration"],
                "exploration_envelope_dollars": round(
                    self.budget.total * self.budget.phase_limits["exploration"], 2),
                "reasoning": "default (planner did not specify)",
                "raw_pct": self.budget.phase_limits["exploration"],
                "clamped": False,
            }

        # --- Read planner's max decomposition depth (v2) ---
        depth_info = self.plan.get("max_decomposition_depth", {})
        if isinstance(depth_info, dict) and depth_info.get("depth"):
            raw_depth = depth_info["depth"]
            self._max_depth = max(2, min(6, raw_depth))
            if self._max_depth != raw_depth:
                print(f"  [PLANNER] WARNING: max depth {raw_depth} clamped to {self._max_depth}")
            self._planner_envelope["max_decomposition_depth"] = self._max_depth
            self._planner_envelope["depth_reasoning"] = depth_info.get("reasoning", "")
            self._planner_envelope["leaf_viable_envelope"] = LEAF_VIABLE_ENVELOPE
            print(f"  [PLANNER] Max decomposition depth: {self._max_depth} "
                  f"(leaf viable: ${LEAF_VIABLE_ENVELOPE:.2f})")
        elif isinstance(depth_info, (int, float)):
            self._max_depth = max(2, min(6, int(depth_info)))
            self._planner_envelope["max_decomposition_depth"] = self._max_depth
            self._planner_envelope["leaf_viable_envelope"] = LEAF_VIABLE_ENVELOPE
        else:
            self._max_depth = 3  # sensible default
            self._planner_envelope["max_decomposition_depth"] = self._max_depth
            self._planner_envelope["leaf_viable_envelope"] = LEAF_VIABLE_ENVELOPE

        # --- Phase 2: Exploration ---
        print(f"\n  {'─'*54}")
        print(f"  PHASE 1: EXPLORATION")
        events.emit("phase_change", {"phase": "exploration"})
        print(f"  {'─'*54}\n")

        # Create WorkerNode agents for each segment
        exploration_budget = self.budget.total * self.budget.phase_limits["exploration"]
        segment_budget_each = exploration_budget / max(1, len(segments))

        segment_workers = []
        self._segment_workers = segment_workers  # save ref for metrics
        all_anomalies = getattr(self, '_survey_anomalies', {})

        # Phase 1: Aggregate anomalies into pattern summary (one LLM call)
        # Phase 2: Route patterns to segments (one cheap LLM call per segment)
        print(f"\n  Aggregating anomalies into patterns...")
        flat_anomalies, pattern_summary = await self._aggregate_anomalies(all_anomalies)

        print(f"  Routing patterns to {len(segments)} segments (LLM reasoning)...")
        routing_tasks = [
            self._route_patterns_to_segment(pattern_summary, flat_anomalies, seg)
            for seg in segments
        ]
        routed_results = await asyncio.gather(*routing_tasks, return_exceptions=True)

        anomaly_type_counts = {}
        for i, (seg, routed) in enumerate(zip(segments, routed_results), 1):
            # Handle routing failures
            if isinstance(routed, Exception):
                print(f"    ⚠ Segment {i} routing error: {routed}")
                seg_anomalies = []
            else:
                seg_anomalies = routed

            seg_name = seg.get("name", f"seg_{i}")[:30]
            print(f"    Segment {i} ({seg_name}): {len(seg_anomalies)} targets routed")

            for a in seg_anomalies:
                t = a.get("type", "unknown")
                anomaly_type_counts[t] = anomaly_type_counts.get(t, 0) + 1

            anomaly_summary = ""
            if seg_anomalies:
                anomaly_summary = f" Survey flagged {len(seg_anomalies)} anomalies in this scope."
            purpose = (
                f"{seg.get('reasoning', 'Investigate this segment.')}"
                f"{anomaly_summary}"
            )

            directive = Directive(
                scope=Scope(
                    source=self.data_source.__class__.__name__,
                    filters=seg.get("filters", {"keyword": seg["name"]}),
                    description=seg.get("scope_description", seg["name"]),
                ),
                lenses=lenses,
                parent_context=f"Planner assigned this segment: {seg.get('reasoning', '')}",
                purpose=purpose,
                tree_position=str(i),
                segment_id=seg["name"],
                survey_anomalies=seg_anomalies,
            )
            worker = WorkerNode(
                directive=directive,
                data_source=self.data_source,
                budget=seg.get("sub_budget", segment_budget_each),
                total_budget=self.budget.total,
                lenses=lenses,
                semaphore=self._semaphore,
                budget_pool=self.budget,
                parent_pool_available=self.budget.exploration_remaining(),
                depth=0,
                max_depth=self._max_depth,
                leaf_viable_envelope=LEAF_VIABLE_ENVELOPE,
            )
            segment_workers.append(worker)

        # Print anomaly flow diagnostic
        if anomaly_type_counts:
            print(f"\n  Anomalies passed to segments:")
            for t, c in sorted(anomaly_type_counts.items(), key=lambda x: -x[1]):
                has_ev = sum(1 for w in segment_workers
                             for a in w.directive.survey_anomalies
                             if a.get("type") == t and a.get("evidence"))
                print(f"    {t}: {c} ({has_ev} with evidence)")

        # Run all segment workers in parallel
        segment_results = list(await asyncio.gather(
            *[w.run() for w in segment_workers],
            return_exceptions=True,
        ))
        segment_results = [r for r in segment_results if isinstance(r, dict)]

        # Workers record spending directly to the budget pool now.
        # No post-hoc recording needed.

        # Collect stats, diagnostics, and populate all_node_results from worker tree
        self._collect_worker_stats(segment_workers)
        self._collect_worker_node_results(segment_workers)
        self._write_diagnostics(segment_workers)

        # Deduplication check — how many observations cover the same pattern?
        obs_fingerprints = {}
        for nr in self.all_node_results:
            for obs in nr.observations:
                # Fingerprint by first 50 chars of evidence + source doc_id
                fp = (obs.raw_evidence[:50].lower(), obs.source.doc_id)
                if fp not in obs_fingerprints:
                    obs_fingerprints[fp] = []
                obs_fingerprints[fp].append(nr.node_id)
        dupes = {fp: nodes for fp, nodes in obs_fingerprints.items() if len(nodes) > 1}
        if dupes:
            print(f"\n  Duplicate observations: {len(dupes)} patterns found by multiple nodes")

        # Root-level synthesis across all segments
        if len(segment_results) > 1 and self.budget.can_spend():
            print(f"\n  [ROOT] SYNTHESIZING all {len(segment_results)} segments...")
            virtual_root = NodeResult(
                node_id="root", parent_id=None,
                scope_description="Root synthesis across all segments",
                survey="", observations=[], child_directives=[],
                unresolved=[], raw_reasoning="",
            )
            # Convert worker results to NodeResult for synthesis compatibility
            synth_inputs = [self._worker_result_to_node_result(r) for r in segment_results]
            root_synthesis = await synthesize(virtual_root, synth_inputs, lenses)
            self.all_syntheses.append(root_synthesis)
            self.budget.record("synthesis", root_synthesis.cost)
            self._update_tokens(root_synthesis.token_usage)

            # Also collect any findings from worker Turn 2 reviews
            for r in segment_results:
                for f in r.get("findings", []):
                    events.emit("finding_discovered", {
                        "node_id": r.get("node_id", ""),
                        "summary": str(f.get("summary", ""))[:80],
                        "type": f.get("type", "cross_cutting"),
                    })

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
              f"review=${self.budget.phase_spent.get('review',0):.2f} "
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

        # Compute and save run metrics
        self._write_run_metrics()

        # Generate transcripts and dashboard
        try:
            import subprocess
            subprocess.run(
                ["python3", "build_transcripts.py", self.run_id],
                capture_output=True, timeout=30,
            )
            print(f"  Transcripts: output/{self.run_id}/transcripts/")
        except Exception:
            pass  # transcript generation is optional

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
        self._emit_reasoning(directive.node_id, node_result.thinking)

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
                purpose=target.get("investigation_directive", target.get("why_this_one", "")),
                tree_position=f"DD.{i}",
                segment_id="deep_dive",
            )

            # Use WorkerNode so thinking events stream to the visualizer
            worker = WorkerNode(
                directive=directive,
                data_source=self.data_source,
                budget=min(available / len(targets), self.budget.remaining()),
                total_budget=self.budget.total,
                lenses=lenses,
                semaphore=self._semaphore,
                budget_pool=self.budget,
                parent_pool_available=self.budget.deep_dive_available(),
                depth=0,
                max_depth=self._max_depth,
                leaf_viable_envelope=LEAF_VIABLE_ENVELOPE,
            )
            worker_result = await worker.run()

            # Convert to NodeResult for downstream pipeline
            result = self._worker_result_to_node_result(worker_result)
            self.all_node_results.append(result)
            self.stats.observations_collected += len(result.observations)
            self.stats.deep_dives_executed += 1
            # Worker already recorded to budget pool, just track the cost
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

    def _collect_worker_stats(self, workers: list):
        """Collect stats from WorkerNode tree for reporting."""
        def _walk(worker):
            self.stats.nodes_spawned += 1
            self.stats.observations_collected += len(worker.observations)
            depth = worker.pos.count(".") + 1 if worker.pos != "ROOT" else 0
            self.stats.max_depth_reached = max(self.stats.max_depth_reached, depth)
            if not worker.child_workers:
                self.stats.nodes_resolved += 1
            for child in worker.child_workers:
                _walk(child)

        for w in workers:
            _walk(w)

        # Save worker nodes to disk
        self._save_worker_tree(workers)

    def _collect_worker_node_results(self, workers: list):
        """Walk the worker tree and populate all_node_results for downstream pipeline."""
        def _walk(worker):
            self.all_node_results.append(self._worker_result_to_node_result(worker._result()))
            self._populate_kg(self.all_node_results[-1])
            for child in worker.child_workers:
                _walk(child)

        for w in workers:
            _walk(w)

    def _save_worker_tree(self, workers: list):
        """Save all worker nodes to disk for the report."""
        def _save(worker):
            node_data = {
                "node_id": worker.node_id,
                "parent_id": worker.directive.parent_id,
                "scope_description": worker.directive.scope.description,
                "tree_position": worker.pos,
                "survey": "",
                "observations": worker.observations,
                "child_directives_count": len(worker.child_workers),
                "unresolved": [],
                "raw_reasoning": "",
                "thinking_log": worker.thinking_log,  # preserve turn-level structure
                "thinking": "\n\n".join(t.get("thinking", "") for t in worker.thinking_log),  # backward compat
                "turn2_review": getattr(worker, '_turn2_output', None),  # structured Turn 2 output
                "metrics": worker.metrics,  # self-assessment: signal strength, follow-up threads, etc.
                "token_usage": {},
                "cost": worker.spent,
            }
            node_file = self.run_dir / "nodes" / f"{worker.node_id[:8]}.json"
            with open(node_file, "w") as f:
                json.dump(node_data, f, indent=2, default=str)
            for child in worker.child_workers:
                _save(child)

        for w in workers:
            _save(w)

    def _write_diagnostics(self, workers: list):
        """Write per-node diagnostic logs and print summary."""
        diag_dir = self.run_dir / "diagnostics"
        diag_dir.mkdir(exist_ok=True)

        all_diags = []

        def _collect(worker):
            diag = worker._build_diagnostic()
            all_diags.append(diag)
            # Write individual diagnostic file
            diag_file = diag_dir / f"{worker.node_id[:8]}.json"
            with open(diag_file, "w") as f:
                json.dump(diag, f, indent=2, default=str)
            for child in worker.child_workers:
                _collect(child)

        for w in workers:
            _collect(w)

        if not all_diags:
            return

        # Compute summary stats
        total = len(all_diags)
        zero_obs = sum(1 for d in all_diags if d["output"]["observations_count"] == 0)
        with_targets = sum(1 for d in all_diags if d.get("anomaly_targets_received", {}).get("count", 0) > 0)
        targets_with_evidence = sum(
            1 for d in all_diags
            if any(t.get("has_evidence") for t in d["anomaly_targets_received"].get("targets", []))
        )
        decomposed = sum(1 for d in all_diags if d["decision"] == "decomposed")
        gaps_flagged = sum(
            1 for d in all_diags
            if not d["self_evaluation"].get("purpose_addressed", True)
        )
        evidence_cited = sum(d["output"].get("evidence_cited", 0) for d in all_diags)
        total_obs = sum(d["output"]["observations_count"] for d in all_diags)

        # Find best and worst nodes
        best = max(all_diags, key=lambda d: d["output"]["observations_count"])
        worst = [d for d in all_diags if d["output"]["observations_count"] == 0]

        print(f"\n  {'═'*54}")
        print(f"  NODE DIAGNOSTIC SUMMARY")
        print(f"  {'─'*54}")
        print(f"  Total nodes: {total}")
        print(f"  Nodes with 0 observations: {zero_obs} ({zero_obs*100//max(1,total)}%)")
        print(f"  Nodes that received anomaly targets: {with_targets} ({with_targets*100//max(1,total)}%)")
        print(f"  Nodes whose targets included evidence: {targets_with_evidence} ({targets_with_evidence*100//max(1,total)}%)")
        print(f"  Nodes that decomposed: {decomposed} ({decomposed*100//max(1,total)}%)")
        print(f"  Nodes where self-eval flagged gaps: {gaps_flagged} ({gaps_flagged*100//max(1,total)}%)")
        print(f"  Observations citing evidence: {evidence_cited}/{total_obs}")
        print(f"  {'─'*54}")
        print(f"  SAMPLE NODE (highest obs count = {best['output']['observations_count']}):")
        print(f"    Position: {best['tree_position']}")
        print(f"    Purpose: {best['purpose'][:100]}")
        print(f"    Data: {best['data_received'].get('record_count', 0)} records")
        print(f"    Targets: {best['anomaly_targets_received']['count']} anomalies")
        print(f"    Decision: {best['decision']}")
        if best["output"].get("sample_observation"):
            sample = best["output"]["sample_observation"]
            print(f"    Sample obs: {str(sample.get('raw_evidence', ''))[:150]}")
        if worst:
            w = worst[0]
            print(f"  SAMPLE NODE (0 observations):")
            print(f"    Position: {w['tree_position']}")
            print(f"    Purpose: {w['purpose'][:100]}")
            print(f"    Data: {w['data_received'].get('record_count', 0)} records")
            print(f"    Targets: {w.get('anomaly_targets_received', {}).get('count', 0)} anomalies")
        print(f"  {'═'*54}")

        # Write full_diagnostic.txt with every node's input/output
        full_path = self.run_dir / "full_diagnostic.txt"
        with open(full_path, "w") as f:
            f.write(f"{'='*70}\n")
            f.write(f"RUN {self.run_id} — FULL NODE DIAGNOSTIC\n")
            f.write(f"{'='*70}\n")
            f.write(f"Nodes: {total} | Zero-obs: {zero_obs} | With evidence: {targets_with_evidence}\n")
            f.write(f"Decomposed: {decomposed} | Self-eval gaps: {gaps_flagged}\n")
            f.write(f"Observations: {total_obs} ({evidence_cited} citing evidence)\n\n")

            for d in all_diags:
                f.write(f"{'─'*70}\n")
                f.write(f"NODE {d['tree_position']} [{d['decision'].upper()}] ${d['budget']['spent']:.3f}\n")
                f.write(f"{'─'*70}\n")
                f.write(f"SCOPE: {d['scope']}\n")
                f.write(f"PURPOSE: {d['purpose']}\n")
                f.write(f"DATA: {d.get('data_received', {}).get('record_count', 0)} records\n")
                tgts = d.get('anomaly_targets_received', {})
                tc = tgts.get('count', 0)
                te = sum(1 for t in tgts.get('targets', []) if t.get('has_evidence'))
                f.write(f"TARGETS: {tc} ({te} with evidence)\n")
                for t in tgts.get('targets', [])[:5]:
                    ev = " [+evidence]" if t.get("has_evidence") else ""
                    f.write(f"  [{t.get('type','?')}] {t.get('description','')[:120]}{ev}\n")
                if tc > 5:
                    f.write(f"  ... and {tc - 5} more\n")

                se = d.get('self_evaluation') or {}
                f.write(f"SELF-EVAL: addressed={se.get('purpose_addressed')} | "
                        f"quality={se.get('evidence_quality')} | "
                        f"gap={str(se.get('purpose_gap',''))[:120]}\n")

                # Load observations from node file
                nid = d['node_id']
                node_file = self.run_dir / "nodes" / f"{nid[:8]}.json"
                try:
                    with open(node_file) as nf:
                        node_data = json.load(nf)
                    obs_list = node_data.get("observations", [])
                    f.write(f"OUTPUT: {len(obs_list)} observations, {d['output']['children_spawned']} children\n")
                    for i, obs in enumerate(obs_list, 1):
                        raw = obs.get("raw_evidence", obs.get("what_i_saw", ""))
                        grounding = obs.get("statistical_grounding", "")
                        hypothesis = obs.get("local_hypothesis", obs.get("reasoning", ""))
                        src = obs.get("source", {})
                        src_id = src.get("doc_id", src.get("title", "?")) if isinstance(src, dict) else str(src)[:30]
                        f.write(f"  [{i}] {obs.get('observation_type', '?')} | src: {src_id}\n")
                        f.write(f"      EVIDENCE: {str(raw)[:250]}\n")
                        if grounding:
                            f.write(f"      GROUNDING: {str(grounding)[:200]}\n")
                        if hypothesis:
                            f.write(f"      HYPOTHESIS: {str(hypothesis)[:200]}\n")
                except FileNotFoundError:
                    f.write(f"  (no node file)\n")
                f.write("\n")

    def _worker_result_to_node_result(self, result: dict) -> NodeResult:
        """Convert a WorkerNode result dict to a NodeResult for synthesis."""
        observations = []
        for obs in result.get("observations", []):
            src = obs.get("source", {})
            observations.append(Observation(
                node_id=result.get("node_id", ""),
                raw_evidence=obs.get("raw_evidence", obs.get("what_i_saw", "")),
                source=Source(
                    doc_id=src.get("doc_id", ""),
                    title=src.get("title", ""),
                    agency=src.get("agency", ""),
                    date=src.get("date", ""),
                    url=src.get("url", ""),
                ),
                observation_type=obs.get("observation_type", "pattern"),
                statistical_grounding=obs.get("statistical_grounding", ""),
                local_hypothesis=obs.get("local_hypothesis", obs.get("reasoning", "")),
                confidence=obs.get("confidence", 0.5),
                surprising_because=obs.get("surprising_because", ""),
                escalated_adjacency=obs.get("escalated_adjacency", False),
                unaddressed_adjacency=obs.get("unaddressed_adjacency", False),
            ))
        return NodeResult(
            node_id=result.get("node_id", ""),
            parent_id=result.get("parent_id"),
            scope_description=result.get("scope_description", ""),
            survey="",
            observations=observations,
            child_directives=[],
            unresolved=[],
            raw_reasoning="",
            thinking="\n".join(t.get("thinking", "") for t in result.get("thinking_log", [])),
        )

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

    def _emit_reasoning(self, node_id: str, thinking: str):
        """Emit the extended thinking block as events for the visualizer.

        The thinking is genuine chain-of-thought from the LLM. We split it
        into displayable chunks and emit each as a node_thinking event.
        """
        if not thinking:
            return

        # Split into paragraphs
        paragraphs = [p.strip() for p in thinking.split("\n\n") if p.strip()]

        # Merge short paragraphs, cap ~300 chars per chunk
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
                "turn": "initial",
                "chunk_index": i,
                "total_chunks": len(chunks),
                "text": chunk[:300],
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
                        "raw_evidence": obs.raw_evidence,
                        "source": {"doc_id": obs.source.doc_id, "title": obs.source.title},
                        "observation_type": obs.observation_type,
                        "local_hypothesis": obs.local_hypothesis,
                    })

        # Take up to 15 most interesting
        lines = []
        for obs in all_obs[:15]:
            lines.append(f"- [{obs.get('observation_type', '?')}] {obs.get('raw_evidence', obs.get('what_i_saw', ''))[:150]}")
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
            "unaddressed_adjacencies": self._collect_adjacencies(),
        }

    def _collect_adjacencies(self) -> list[dict]:
        """Collect unaddressed and escalated adjacency observations for the report."""
        adjacencies = []
        for nr in self.all_node_results:
            for obs in nr.observations:
                if getattr(obs, 'unaddressed_adjacency', False) or \
                   getattr(obs, 'escalated_adjacency', False):
                    adjacencies.append({
                        "raw_evidence": obs.raw_evidence,
                        "observation_type": obs.observation_type,
                        "local_hypothesis": obs.local_hypothesis,
                        "source_node": nr.node_id,
                        "escalated": getattr(obs, 'escalated_adjacency', False),
                        "unaddressed": getattr(obs, 'unaddressed_adjacency', False),
                    })
        return adjacencies

    def _save_node(self, result: NodeResult):
        node_file = self.run_dir / "nodes" / f"{result.node_id[:8]}.json"
        with open(node_file, "w") as f:
            json.dump(_result_to_dict(result), f, indent=2, default=str)

    def _save_tree(self, data: dict):
        tree_file = self.run_dir / "tree.json"
        with open(tree_file, "w") as f:
            json.dump(data, f, indent=2, default=str)
        print(f"\n  Tree saved to {tree_file}")

    def _flatten_anomalies(self, all_anomalies: dict) -> list[dict]:
        """Flatten survey anomaly dict into a list of uniform entries with evidence."""
        flat = []

        def _build_evidence(a: dict) -> dict:
            if "evidence" in a:
                return a["evidence"]
            skip = {"type", "description", "record", "entity", "flagged_by", "indices"}
            return {k: v for k, v in a.items() if k not in skip and v is not None}

        for outlier in all_anomalies.get("outliers", []):
            flat.append({
                "type": "outlier",
                "record": outlier.get("record_id", "?"),
                "description": f"{outlier.get('record_id', '?')}: {outlier.get('field', '?')}="
                               f"{outlier.get('value', '?')} (z-score {outlier.get('z_score', '?')})",
                "flagged_by": ["basic_statistics"],
                "evidence": {"field": outlier.get("field"), "value": outlier.get("value"),
                             "z_score": outlier.get("z_score"), "direction": outlier.get("direction"),
                             "record_summary": outlier.get("record_summary", "")},
            })
        for combo in all_anomalies.get("unusual_combinations", []):
            flat.append({"type": "unusual_combination", "description": combo.get("description", ""),
                         "flagged_by": ["keyword_signals"],
                         "evidence": {"description": combo.get("description", ""),
                                      "overrepresentation": combo.get("overrepresentation")}})
        for conc in all_anomalies.get("concentrations", []):
            flat.append({"type": "concentration",
                         "description": f"{conc.get('concentration_pct', '?')}% in {conc.get('field', '?')}",
                         "flagged_by": ["entity_concentration"],
                         "evidence": {"field": conc.get("field"), "concentration_pct": conc.get("concentration_pct"),
                                      "dominant_value": conc.get("dominant_value", "")}})
        for key in ("content_anomalies", "entity_anomalies", "graph_anomalies",
                    "similarity_anomalies", "velocity_anomalies"):
            for a in all_anomalies.get(key, []):
                flat.append({"type": a.get("type", "anomaly"), "description": a.get("description", ""),
                             "record": a.get("entity", a.get("record", "")),
                             "flagged_by": [key.replace("_anomalies", "")],
                             "evidence": _build_evidence(a)})
        for tech_key, tech_data in all_anomalies.get("anomalies_by_technique", {}).items():
            if not isinstance(tech_data, dict):
                continue
            for a in tech_data.get("anomalies", []):
                if not isinstance(a, dict):
                    continue
                flat.append({"type": a.get("type", tech_key), "description": a.get("description", ""),
                             "record": a.get("entity", a.get("record", "")),
                             "flagged_by": [tech_key], "evidence": _build_evidence(a)})
        return flat

    async def _aggregate_anomalies(self, all_anomalies: dict) -> tuple[list[dict], str]:
        """Aggregate anomalies into pattern clusters via one LLM call.

        Returns (flat_anomaly_list, pattern_summary_text).
        The pattern summary is what gets sent to per-segment routing calls.
        """
        flat = self._flatten_anomalies(all_anomalies)
        if not flat:
            return [], "No anomalies found."

        # Format numbered list — context-derived cap
        sample_str = json.dumps(flat[0], default=str) if flat else "{}"
        tokens_per = max(1, len(sample_str) // 3)
        max_anomalies = max(50, 180_000 // tokens_per)
        to_send = flat[:max_anomalies]

        lines = []
        for i, a in enumerate(to_send):
            lines.append(f"[{i}] [{a.get('type', '?')}] {a.get('record', '')}: {a.get('description', '')[:150]}")

        prompt = ANOMALY_AGGREGATION_PROMPT.format(anomaly_list="\n".join(lines))

        try:
            client = anthropic.Anthropic()
            response = client.messages.create(
                model="claude-sonnet-4-20250514",
                max_tokens=2000,
                messages=[{"role": "user", "content": prompt}],
            )
            cost = (response.usage.input_tokens * 3 + response.usage.output_tokens * 15) / 1_000_000
            self.budget.record("overhead", cost)

            output = response.content[0].text
            result = json.loads(output) if output.strip().startswith("{") else \
                json.loads(output[output.find("{"):output.rfind("}") + 1])

            patterns = result.get("patterns", [])
            # Build human-readable summary for routing
            summary_lines = []
            for i, p in enumerate(patterns):
                reps = p.get("representative_indices", [])
                rep_descs = [to_send[j].get("description", "")[:80] for j in reps if 0 <= j < len(to_send)]
                summary_lines.append(
                    f"[{i}] {p.get('name', '?')} ({p.get('anomaly_count', '?')} anomalies): "
                    f"{p.get('description', '')}\n    Examples: {'; '.join(rep_descs[:3])}"
                )

            pattern_summary = "\n".join(summary_lines)
            print(f"  Aggregated {len(flat)} anomalies into {len(patterns)} patterns (${cost:.3f})")

            # Store patterns with their representative anomalies for later extraction
            self._aggregated_patterns = patterns
            self._flat_anomalies = to_send
            return flat, pattern_summary

        except Exception as e:
            print(f"  ⚠ Anomaly aggregation failed: {e}")
            # Return raw summary as fallback
            summary = f"{len(flat)} anomalies found across {len(set(a.get('type') for a in flat))} types."
            return flat, summary

    async def _route_patterns_to_segment(self, pattern_summary: str,
                                          flat_anomalies: list[dict],
                                          segment: dict) -> list[dict]:
        """Route aggregated patterns to a segment via one cheap LLM call.

        Receives the pre-computed pattern summary (not raw anomalies).
        On failure: returns empty list.
        """
        prompt = ANOMALY_ROUTING_PROMPT.format(
            segment_name=segment.get("name", ""),
            segment_scope=segment.get("scope_description", segment.get("name", "")),
            segment_reasoning=segment.get("reasoning", ""),
            pattern_summary=pattern_summary,
        )

        try:
            client = anthropic.Anthropic()
            response = client.messages.create(
                model="claude-sonnet-4-20250514",
                max_tokens=500,
                messages=[{"role": "user", "content": prompt}],
            )
            cost = (response.usage.input_tokens * 3 + response.usage.output_tokens * 15) / 1_000_000
            self.budget.record("overhead", cost)

            output = response.content[0].text
            result = json.loads(output) if output.strip().startswith("{") else \
                json.loads(output[output.find("{"):output.rfind("}") + 1])

            # Map pattern indices back to representative anomalies
            pattern_indices = result.get("relevant_pattern_indices", [])
            patterns = getattr(self, '_aggregated_patterns', [])
            selected = []
            for pi in pattern_indices:
                if 0 <= pi < len(patterns):
                    for ai in patterns[pi].get("representative_indices", []):
                        if 0 <= ai < len(flat_anomalies):
                            selected.append(flat_anomalies[ai])
            return selected

        except Exception as e:
            print(f"    ⚠ Pattern routing failed for '{segment.get('name', '?')}': {e}")
            return []

    def _write_run_metrics(self):
        """Compute and write run metrics from existing data. No LLM calls."""
        elapsed = time.time() - self.start_time

        # Collect token usage from worker tree
        total_tokens = {"input_tokens": 0, "output_tokens": 0}
        all_workers = []

        def _walk_workers(worker):
            all_workers.append(worker)
            total_tokens["input_tokens"] += worker.token_usage.get("input_tokens", 0)
            total_tokens["output_tokens"] += worker.token_usage.get("output_tokens", 0)
            for child in worker.child_workers:
                _walk_workers(child)

        for w in getattr(self, '_segment_workers', []):
            _walk_workers(w)

        # Count zero-obs nodes and self-eval gaps
        zero_obs_nodes = sum(1 for w in all_workers if not w.observations)
        zero_obs_cost = sum(w.spent for w in all_workers if not w.observations)
        self_eval_gaps = sum(1 for w in all_workers
                            if not w.metrics.get("purpose_addressed", True))

        # Count spawn rejections
        depth_cap_rejections = 0
        envelope_floor_rejections = 0
        for w in all_workers:
            for rej in getattr(w, '_spawn_rejections', []):
                if rej.get("reason") == "depth_cap":
                    depth_cap_rejections += 1
                elif rej.get("reason") == "envelope_floor":
                    envelope_floor_rejections += 1

        # Count validated findings
        confirmed = sum(1 for v in self.all_validations if v.verdict == "confirmed")
        weakened = sum(1 for v in self.all_validations if v.verdict == "weakened")
        refuted = sum(1 for v in self.all_validations if v.verdict == "refuted")
        total_validated = len(self.all_validations)

        total_obs = self.stats.observations_collected
        total_nodes = len(all_workers) if all_workers else self.stats.nodes_spawned
        total_cost = self.budget.spent

        # Enrichment data
        enriched_count = len(getattr(self.data_source, '_enriched_filings', []))
        source_name = self.data_source.__class__.__name__

        metrics = {
            "run_id": self.run_id,
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "source": source_name,
            "cost": {
                "total": round(total_cost, 3),
                "by_phase": {k: round(v, 3) for k, v in self.budget.phase_spent.items()},
                "per_node": round(total_cost / max(1, total_nodes), 3),
                "per_observation": round(total_cost / max(1, total_obs), 3),
                "per_validated_finding": round(total_cost / max(1, confirmed), 3) if confirmed else None,
                "budget_authorized": self.budget.total,
                "budget_utilization": round(total_cost / max(0.01, self.budget.total), 3),
                "wasted_on_zero_obs": round(zero_obs_cost, 3),
            },
            "quality": {
                "total_observations": total_obs,
                "findings_submitted": total_validated,
                "findings_confirmed": confirmed,
                "findings_weakened": weakened,
                "findings_refuted": refuted,
                "validation_rate": round(confirmed / max(1, total_validated), 3),
                "observations_per_node": round(total_obs / max(1, total_nodes), 2),
                "observations_per_dollar": round(total_obs / max(0.01, total_cost), 1),
                "zero_obs_nodes": zero_obs_nodes,
                "zero_obs_pct": round(zero_obs_nodes / max(1, total_nodes), 3),
                "self_eval_gaps": self_eval_gaps,
                "self_eval_gap_pct": round(self_eval_gaps / max(1, total_nodes), 3),
            },
            "efficiency": {
                "wall_clock_seconds": round(elapsed),
                "nodes_spawned": total_nodes,
                "nodes_resolved": self.stats.nodes_resolved,
                "nodes_decomposed": self.stats.nodes_spawned - self.stats.nodes_resolved,
                "max_depth": self.stats.max_depth_reached,
                "avg_branching_factor": round(self.stats.avg_branching_factor, 2),
                "depth_cap_rejections": depth_cap_rejections,
                "envelope_floor_rejections": envelope_floor_rejections,
                "deep_dives": self.stats.deep_dives_executed,
            },
            "tokens": {
                "input_tokens": total_tokens["input_tokens"],
                "output_tokens": total_tokens["output_tokens"],
                "total_tokens": total_tokens["input_tokens"] + total_tokens["output_tokens"],
                "avg_tokens_per_node": round(
                    (total_tokens["input_tokens"] + total_tokens["output_tokens"]) / max(1, total_nodes)),
            },
            "data_coverage": {
                "source": source_name,
                "records_enriched": enriched_count,
                "records_analyzed_by_survey": enriched_count,
            },
            "planner_decisions": getattr(self, '_planner_envelope', {}),
        }

        # Write metrics.json
        metrics_path = self.run_dir / "metrics.json"
        with open(metrics_path, "w") as f:
            json.dump(metrics, f, indent=2, default=str)

        # Append to run history
        from pathlib import Path
        history_path = Path("catalog/run_history.jsonl")
        history_path.parent.mkdir(parents=True, exist_ok=True)
        history_line = {
            "run_id": self.run_id,
            "timestamp": metrics["timestamp"],
            "source": source_name,
            "budget": self.budget.total,
            "cost": round(total_cost, 2),
            "nodes": total_nodes,
            "observations": total_obs,
            "confirmed_findings": confirmed,
            "cost_per_valid_finding": metrics["cost"]["per_validated_finding"],
            "zero_obs_pct": metrics["quality"]["zero_obs_pct"],
            "wall_clock_seconds": round(elapsed),
        }
        with open(history_path, "a") as f:
            f.write(json.dumps(history_line, default=str) + "\n")

        # Print summary
        valid_cost = f"${metrics['cost']['per_validated_finding']:.2f}" if confirmed else "N/A"
        print(f"\n  RUN METRICS: ${total_cost:.2f}/{self.budget.total:.2f} | "
              f"{total_obs} obs, {confirmed} confirmed findings | "
              f"{valid_cost}/valid finding | "
              f"{zero_obs_nodes}/{total_nodes} zero-obs nodes | "
              f"{int(elapsed//60)}m{int(elapsed%60)}s | "
              f"{enriched_count} records")


# --- Serialization ---

def _result_to_dict(r: NodeResult) -> dict:
    return {
        "node_id": r.node_id, "parent_id": r.parent_id,
        "scope_description": r.scope_description, "survey": r.survey,
        "observations": [
            {"raw_evidence": o.raw_evidence,
             "statistical_grounding": o.statistical_grounding,
             "local_hypothesis": o.local_hypothesis,
             "source": {"doc_id": o.source.doc_id, "title": o.source.title,
                        "agency": o.source.agency, "date": o.source.date, "url": o.source.url},
             "observation_type": o.observation_type,
             "confidence": o.confidence,
             "surprising_because": o.surprising_because,
             "escalated_adjacency": getattr(o, 'escalated_adjacency', False),
             "unaddressed_adjacency": getattr(o, 'unaddressed_adjacency', False)}
            for o in r.observations
        ],
        "child_directives_count": len(r.child_directives),
        "unresolved": r.unresolved,
        "raw_reasoning": r.raw_reasoning,
        "thinking": r.thinking,
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


    # _filter_anomalies DELETED — replaced by Orchestrator._route_anomalies_to_segment()
    # which uses LLM reasoning instead of keyword matching.
