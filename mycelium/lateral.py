"""Lateral Communication — sibling nodes share budget and signal through parents.

STATUS: v2 DESIGN — skeleton only, not yet wired into orchestrator.

A node finishing under-budget reports surplus. A node that found something
interesting but is running low requests additional funds. The parent
evaluates competing requests and reallocates.

The shared BudgetPool in v1 achieves ~80% of this benefit — surplus is
automatically available in the pool. Lateral communication adds intelligent
routing: budget flows toward signal, not just toward whoever requests first.

Example flow:
    [3.1] COMPLETED: 4 obs, $0.30 surplus → reports to parent
    [3.2] REQUESTING: found single maintainer controlling 400 packages,
          needs $0.25 to trace blast radius
    [3] REALLOCATING: $0.25 from pool → [3.2]
        Reasoning: "3.2's thread has higher discovery potential"
"""


class BudgetBroker:
    """Evaluates competing budget requests from sibling nodes."""

    def __init__(self, parent_budget: float):
        self.available = parent_budget
        self.surplus_pool = 0.0
        self.requests: list[dict] = []

    def report_surplus(self, node_id: str, amount: float, reason: str):
        """A node finished under-budget and returns funds."""
        self.surplus_pool += amount
        self.available += amount

    def request_funds(self, node_id: str, amount: float,
                      finding_summary: str, signal_strength: float):
        """A node requests additional budget, citing a specific finding."""
        self.requests.append({
            "node_id": node_id,
            "amount": amount,
            "finding": finding_summary,
            "signal": signal_strength,
        })

    def allocate(self) -> list[dict]:
        """Evaluate requests and allocate from surplus pool.

        Priority: requests citing specific surprising findings get funded
        first. General "need more budget" requests get lower priority.
        """
        # Sort by signal strength (highest first)
        sorted_requests = sorted(self.requests, key=lambda r: r["signal"], reverse=True)

        allocations = []
        for req in sorted_requests:
            if self.available >= req["amount"]:
                self.available -= req["amount"]
                allocations.append({
                    "node_id": req["node_id"],
                    "amount": req["amount"],
                    "reason": f"Funded: {req['finding'][:50]}",
                })

        self.requests.clear()
        return allocations


class LateralEvent:
    """Event types for inter-node communication."""
    SURPLUS = "surplus"
    REQUEST = "request"
    ALLOCATION = "allocation"
    SIGNAL = "signal"  # "I found something related to your area"
