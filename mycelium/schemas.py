"""Data structures for the Mycelium exploration tree.

Core structures:
- Briefing: common knowledge baseline for novelty calibration
- Directive: what a node is told to explore
- Observation: what a node found (with citation)
- NodeResult: complete output from a single reasoning node
- SynthesisResult: cross-referenced patterns from sibling nodes
- ValidationResult: skeptical review of a Tier 3-5 finding
- ImpactResult: real-world impact assessment of a validated finding
- BudgetPool: shared budget with soft segment targets
"""

import asyncio
from dataclasses import dataclass, field
from typing import Optional
import uuid


# --- Briefing (common knowledge baseline) ---

@dataclass
class Briefing:
    """Common knowledge baseline for novelty calibration.

    Only common_knowledge is populated in Phase C. Other fields are
    schema reservations for future EQUIP layers (see MYCELIUM_VISION.md).
    """
    common_knowledge: str = ""
    relevant_data_sources: list[str] = field(default_factory=list)
    # Future fields — not populated yet
    task_statement: Optional[str] = None
    task_interpretation: Optional[str] = None
    available_tools: list[str] = field(default_factory=list)
    proposed_organization: Optional[dict] = None
    role_tool_allocation: Optional[dict] = None
    success_criteria: Optional[str] = None
    # Generation metadata
    cost: float = 0.0
    token_usage: dict = field(default_factory=dict)


# --- Core exploration structures ---

@dataclass
class RoleDefinition:
    """What a node is, not just what it does.

    Authored by the hiring manager at spawn time. Mission is the aspiration;
    bar is the floor. The worker reasons against mission, checks against bar.
    """
    name: str = ""              # what this role is called
    mission: str = ""           # what excellent work looks like — direction, not checkbox
    success_bar: str = ""       # minimum acceptable output — below this is failure
    heuristic: str = ""         # posture for ambiguous moments


@dataclass
class Scope:
    """Defines the boundaries of what a node should explore."""
    source: str
    filters: dict = field(default_factory=dict)
    description: str = ""


@dataclass
class Directive:
    """Instructions passed from parent to child node."""
    scope: Scope
    lenses: list[str]
    parent_context: Optional[str]
    purpose: str = ""  # WHY this node is being asked — what the parent needs from it
    data_filter: dict = field(default_factory=dict)  # structured filter matching data source schema
    node_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    parent_id: Optional[str] = None
    tree_position: str = "ROOT"
    chain_depth: int = 0
    segment_id: Optional[str] = None  # which planner segment this belongs to
    survey_anomalies: list = field(default_factory=list)  # statistical anomalies relevant to this scope
    workspace_path: Optional[str] = None  # path to org-level workspace directory
    scope_level: str = "ambiguous"  # manager | worker | ambiguous
    role: Optional[RoleDefinition] = None  # role-authoring path only


@dataclass
class Source:
    """Citation for a specific piece of evidence."""
    doc_id: str
    title: str
    agency: str = ""
    date: str = ""
    section: str = ""
    url: str = ""


@dataclass
class Observation:
    """An evidence packet — structured data, not prose."""
    node_id: str
    raw_evidence: str        # THE SPECIFIC DATA — actual values from records
    source: Source
    observation_type: str
    statistical_grounding: str = ""   # which survey techniques flagged this
    local_hypothesis: str = ""        # specific explanation of why this is surprising
    confidence: float = 0.5
    surprising_because: str = ""      # expected vs actual
    escalated_adjacency: bool = False  # flagged for grandparent consideration
    unaddressed_adjacency: bool = False  # noted but not investigated

    # Legacy compat — downstream code may still reference these
    @property
    def what_i_saw(self) -> str:
        return self.raw_evidence

    @property
    def reasoning(self) -> str:
        return self.local_hypothesis

    @property
    def preliminary_relevance(self) -> dict[str, float]:
        return {}

    @property
    def potential_connections(self) -> list[str]:
        return []


@dataclass
class NodeResult:
    """Complete output from a single reasoning node."""
    node_id: str
    parent_id: Optional[str]
    scope_description: str
    survey: str
    observations: list[Observation]
    child_directives: list[Directive]
    unresolved: list[str]
    raw_reasoning: str
    thinking: str = ""  # extended thinking chain-of-thought
    token_usage: dict = field(default_factory=dict)
    cost: float = 0.0


# --- Synthesis ---

@dataclass
class SynthesisResult:
    """Output from synthesizing children's observations."""
    node_id: str
    reinforced: list[dict]
    contradictions: list[dict]
    cross_cutting: list[dict]
    rescored_observations: list[Observation]
    discovered_questions: list[str]
    unresolved_threads: list[str]
    raw_reasoning: str
    token_usage: dict = field(default_factory=dict)
    cost: float = 0.0


# --- Validation ---

@dataclass
class ValidationResult:
    """Skeptical review of a Tier 3-5 finding."""
    finding_id: str
    original_finding: dict
    verdict: str          # confirmed, confirmed_with_caveats, weakened, refuted, needs_verification
    reasoning: str
    adjusted_confidence: float
    adjusted_tier: int
    verification_action: str
    revised_finding: Optional[str]
    raw_reasoning: str
    factual_assessment: dict = field(default_factory=dict)
    interpretive_assessment: dict = field(default_factory=dict)
    is_pipeline_issue: bool = False
    pipeline_issue_reasoning: str = ""
    token_usage: dict = field(default_factory=dict)
    cost: float = 0.0


# --- Impact Analysis ---

@dataclass
class ImpactResult:
    """Real-world impact assessment of a validated finding."""
    finding_id: str
    affected_parties: list[str]
    estimated_scale: str
    financial_exposure: str
    risk_scenario: str
    who_needs_to_know: list[str]
    urgency: str
    actionability: str
    reasoning: str
    raw_reasoning: str
    token_usage: dict = field(default_factory=dict)
    cost: float = 0.0


# --- Budget Pool (atomic reservation + phase hard limits) ---

class BudgetPool:
    """Shared budget pool with atomic reservation and phase hard limits.

    Budget flows like water, not envelopes. But exploration has a HARD
    cap (55% of total) to ensure downstream phases (synthesis, validation,
    impact) always have budget to work with.

    Parallel nodes use reserve/commit/release for atomic budget accounting.
    Sequential phases use record() directly.
    """

    def __init__(self, total_budget: float):
        self.total = total_budget
        self.spent = 0.0
        self.reserved = 0.0  # money committed but not yet spent
        self._lock = asyncio.Lock()

        # Phase allocations — exploration and review are HARD caps, others are soft
        self.phase_limits = {
            "exploration": 0.40,
            "review": 0.15,       # parent Turn 2 reasoning over completed children
            "synthesis": 0.13,
            "deep_dive": 0.08,
            "validation": 0.07,
            "impact": 0.10,
            "overhead": 0.07,
        }
        self.phase_spent: dict[str, float] = {k: 0.0 for k in self.phase_limits}

        # Segment targets from planner (advisory, not hard)
        self.segment_targets: dict[str, float] = {}
        self.segment_spent: dict[str, float] = {}

    # --- Atomic reservation (for parallel exploration) ---

    async def reserve(self, estimated_cost: float, phase: str = "exploration") -> bool:
        """Atomically reserve budget before starting work.
        Returns True if reserved, False if insufficient.
        """
        async with self._lock:
            total_available = self.total - self.spent - self.reserved
            if estimated_cost > total_available:
                return False
            # Hard limit for exploration phase (epsilon for float precision)
            if phase == "exploration":
                phase_limit = self.total * self.phase_limits["exploration"]
                phase_committed = self.phase_spent.get("exploration", 0) + self.reserved
                if estimated_cost > (phase_limit - phase_committed + 0.001):
                    return False
            self.reserved += estimated_cost
            return True

    async def commit(self, reserved_amount: float, actual_cost: float,
                     phase: str = "exploration", segment_id: str = None):
        """After work completes, commit actual cost and release unused reservation."""
        async with self._lock:
            self.reserved -= reserved_amount
            self.spent += actual_cost
            self.phase_spent[phase] = self.phase_spent.get(phase, 0) + actual_cost
            if segment_id:
                self.segment_spent[segment_id] = self.segment_spent.get(segment_id, 0) + actual_cost

    async def release(self, reserved_amount: float):
        """Cancel a reservation (work was skipped)."""
        async with self._lock:
            self.reserved -= reserved_amount

    # --- Sync methods (for sequential phases) ---

    def can_spend(self, estimated_cost: float = 0.05) -> bool:
        """Quick sync check for non-parallel contexts."""
        return (self.spent + self.reserved + estimated_cost) <= self.total

    def record(self, phase: str, cost: float, segment_id: str = None):
        """Record spending for sequential phases (sync)."""
        self.spent += cost
        self.phase_spent[phase] = self.phase_spent.get(phase, 0) + cost
        if segment_id:
            self.segment_spent[segment_id] = self.segment_spent.get(segment_id, 0) + cost

    # --- Budget queries ---

    def remaining(self) -> float:
        return max(0.0, self.total - self.spent - self.reserved)

    def remaining_pct(self) -> float:
        return (self.remaining() / self.total * 100) if self.total > 0 else 0

    @property
    def exploration_exhausted(self) -> bool:
        """True when exploration phase has hit its hard limit."""
        phase_limit = self.total * self.phase_limits["exploration"]
        return self.phase_spent.get("exploration", 0) >= phase_limit

    @property
    def review_exhausted(self) -> bool:
        """True when review phase has hit its hard limit."""
        phase_limit = self.total * self.phase_limits.get("review", 0.15)
        return self.phase_spent.get("review", 0) >= phase_limit

    def exploration_budget(self) -> float:
        return self.total * self.phase_limits["exploration"]

    def exploration_remaining(self) -> float:
        return max(0.0, self.exploration_budget() - self.phase_spent.get("exploration", 0))

    def deep_dive_available(self) -> float:
        """Deep-dive gets its own reserve plus any unspent exploration."""
        reserve = self.total * self.phase_limits["deep_dive"]
        exploration_savings = max(0.0,
            self.exploration_budget() - self.phase_spent.get("exploration", 0))
        return min(reserve + exploration_savings, self.remaining())

    def segment_status(self, segment_id: str) -> dict:
        """How is this segment doing vs its target?"""
        target = self.segment_targets.get(segment_id, 0)
        spent = self.segment_spent.get(segment_id, 0)
        return {
            "target": target,
            "spent": spent,
            "remaining_target": max(0, target - spent),
            "over_target": spent > target,
            "pool_remaining": self.remaining(),
        }

    def set_segment_targets(self, targets: dict[str, float]):
        """Set segment targets from planner output."""
        self.segment_targets = targets
        self.segment_spent = {k: 0.0 for k in targets}


# --- Exploration stats ---

@dataclass
class ExplorationStats:
    """Running totals for the exploration."""
    nodes_spawned: int = 0
    nodes_resolved: int = 0
    observations_collected: int = 0
    max_depth_reached: int = 0
    total_tokens: int = 0
    api_calls: int = 0
    chain_breaker_fired: int = 0
    findings_validated: int = 0
    findings_confirmed: int = 0
    deep_dives_executed: int = 0
    avg_branching_factor: float = 0.0
