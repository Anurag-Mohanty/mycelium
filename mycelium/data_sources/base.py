"""Base interface for data sources.

Every data source must implement two capabilities:
1. survey() — return structural metadata (what's here, how much, what shape)
2. fetch() — return actual content for a given scope
"""

from abc import ABC, abstractmethod


class DataSource(ABC):
    """Interface that all data source connectors implement."""

    @abstractmethod
    async def survey(self, filters: dict) -> dict:
        """Get structural metadata: counts, categories, date ranges, entities.

        This is the "shelf labels" scan — tells the node what's in its scope
        without fetching full content. Should be cheap and fast.
        """
        ...

    @abstractmethod
    async def fetch(self, filters: dict, max_results: int = 50) -> list[dict]:
        """Fetch actual documents/records matching the filters.

        Returns list of document dicts with at minimum:
        - id, title, agency, date, type, abstract/summary, url
        Full content fetching happens only when a node decides it needs depth.
        """
        ...

    @abstractmethod
    async def fetch_document(self, doc_id: str) -> dict:
        """Fetch a single document's full content by ID."""
        ...

    def filter_schema(self) -> dict:
        """Describe what filter parameters this data source accepts.

        Returns a structured contract so the LLM knows what queries will work.
        Each parameter is described with type, description, example, and required flag.
        Shape:
            {
                "parameter_name": {
                    "type": "string" | "list[string]" | "integer" | ...,
                    "description": "what this filter does",
                    "example": <concrete example value>,
                    "required": False
                }
            }

        A new connector for any corpus (sensors, wikis, databases) fills in
        the same structure. Pipeline code never inspects parameter names.
        """
        return {}
