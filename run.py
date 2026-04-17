"""Mycelium — Recursive Knowledge Discovery Framework

Release an autonomous explorer into an information space with a budget.
It decides everything else.

Usage:
    python3 run.py --source npm --budget 10
    python3 run.py --source npm --estimate
    python3 run.py --source npm --estimate --auto-proceed balanced
    python3 run.py --source npm --budget 10 --hint "security focus"
"""

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

from mycelium.data_sources.federal_register import FederalRegisterSource
from mycelium.data_sources.npm_registry import NpmRegistrySource
from mycelium.genesis import run_genesis
from mycelium.orchestrator import Orchestrator
from mycelium.reporter import generate_report
from mycelium.prompts import ESTIMATE_PROMPT

import anthropic


def parse_args():
    parser = argparse.ArgumentParser(
        description="Mycelium — Recursive Knowledge Discovery",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python3 run.py --source npm --budget 10
  python3 run.py --source npm --budget 10 --visualize
  python3 run.py --source npm --estimate
  python3 run.py --playback output/{run_id}/events.jsonl
        """,
    )
    parser.add_argument("--source", default=None,
                        help="Data source (npm, federal_register). Omit with --visualize for browser selection.")
    parser.add_argument("--budget", type=float, default=None,
                        help="Max cost in dollars")
    parser.add_argument("--hint", action="append", default=[],
                        help="Optional context hints (repeatable)")
    parser.add_argument("--estimate", action="store_true",
                        help="Survey and show cost estimates before exploring")
    parser.add_argument("--auto-proceed", choices=["thorough", "balanced", "focused", "scout"],
                        help="Auto-select a tier when using --estimate")
    parser.add_argument("--output-dir", default="output",
                        help="Output directory (default: output)")
    parser.add_argument("--visualize", action="store_true",
                        help="Launch real-time browser visualization")
    parser.add_argument("--query", type=str, default=None,
                        help="Query the knowledge graph from previous runs")
    parser.add_argument("--playback", type=str, default=None,
                        help="Replay a previous run from events.jsonl file")
    parser.add_argument("--speed", type=int, default=10,
                        help="Playback speed multiplier (default: 10)")
    return parser.parse_args()


async def _resolve_data_source(user_query: str) -> dict:
    """Use LLM to figure out which connector matches the user's description.

    For built-in connectors (npm, federal_register), returns connector name.
    For public APIs we don't have a connector for, returns API config so
    the GenericAPISource can connect dynamically.
    """
    client = anthropic.Anthropic()
    response = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=1500,
        messages=[{"role": "user", "content": f"""The user typed: "{user_query}"

You are a data source resolver for an exploration tool.

STEP 1: Is this a request to explore a DATA SOURCE or information space?
If someone asks "what is 2+2", "explain quantum physics", or any general question,
return {{"is_exploration": false, "message": "Mycelium explores data sources, not general questions. Describe an information space like 'npm registry', 'FDA adverse events', or 'EPA air quality data'."}}.

STEP 2: Does it match a built-in connector?
- "npm": npm package registry. Match ONLY for: npm, node packages, JavaScript/TypeScript packages
- "federal_register": US Federal Register. Match ONLY for: federal register, federal regulations, CFR
If yes, return {{"is_exploration": true, "connector": "npm_or_federal_register", "name": "...", "description": "..."}}.

STRICT: "FDA" is NOT the Federal Register. "SEC" is NOT the Federal Register. "GitHub" is NOT npm.

STEP 3: If no built-in connector, but a PUBLIC API exists for this data, return its configuration.
Think about what public REST API serves this data. Many government agencies, open data platforms,
and public services have free JSON APIs.

Return:
{{
    "is_exploration": true,
    "connector": null,
    "name": "human-readable name",
    "description": "what this data source contains",
    "api_config": {{
        "base_url": "https://api.example.gov",
        "search_endpoint": "/search",
        "search_param": "q",
        "limit_param": "limit",
        "max_per_request": 100,
        "records_path": "results",
        "total_path": "meta.total",
        "field_mapping": {{
            "id": "field_name_for_id",
            "title": "field_name_for_title",
            "date": "field_name_for_date",
            "description": "field_name_for_description"
        }},
        "search_terms": ["term1", "term2", "term3", "term4", "term5"],
        "rate_limit_ms": 500,
        "source_name": "Human-Readable Source Name"
    }}
}}

Examples of APIs you should know:
- FDA: api.fda.gov, endpoints like /drug/event.json, /food/recall.json
- SEC EDGAR: efts.sec.gov/LATEST/search-index, full-text search of filings
- EPA AQS: aqs.epa.gov/data/api
- GitHub: api.github.com, /search/repositories, /repos/owner/name/issues
- USGS Earthquakes: earthquake.usgs.gov/fdsnws/event/1/query
- Open Library: openlibrary.org/search.json
- PubMed: eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi

If you genuinely don't know a public API for this data source, return:
{{
    "is_exploration": true,
    "connector": null,
    "api_config": null,
    "name": "...",
    "message": "I don't know a public API for this data source. You might need to provide an API endpoint or upload data directly."
}}

Respond ONLY with valid JSON."""}],
    )
    try:
        text = response.content[0].text
        start = text.find("{")
        end = text.rfind("}") + 1
        if start >= 0 and end > start:
            return json.loads(text[start:end])
    except (json.JSONDecodeError, ValueError):
        pass
    return {"connector": None, "is_exploration": True, "message": "Could not determine data source. Try again."}


def create_data_source(name):
    if name == "federal_register":
        return FederalRegisterSource()
    elif name == "npm":
        return NpmRegistrySource()
    else:
        print(f"ERROR: Unknown data source '{name}'")
        print("Available: federal_register, npm")
        sys.exit(1)


async def run_estimate(data_source, hints):
    """Survey the corpus and show cost/time estimates for different tiers."""
    print(f"\n╔{'═'*54}╗")
    print(f"║  MYCELIUM — Exploration Estimate{' '*21}║")
    print(f"╚{'═'*54}╝\n")

    print("  Surveying corpus shape...")
    genesis_result = await run_genesis(data_source, hints)
    summary = genesis_result.get("corpus_summary", "")
    print(f"  {summary[:100]}...")

    # Estimate costs
    print("  Estimating exploration tiers...")
    genesis_json = json.dumps({
        "corpus_summary": summary,
        "lenses": genesis_result.get("lenses", []),
        "suggested_entry_points": genesis_result.get("suggested_entry_points", []),
        "natural_structure": genesis_result.get("natural_structure", {}),
    }, indent=2)

    prompt = ESTIMATE_PROMPT.format(genesis_output=genesis_json)
    client = anthropic.Anthropic()
    response = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=2000,
        messages=[{"role": "user", "content": prompt}],
    )

    try:
        estimate = json.loads(response.content[0].text)
    except json.JSONDecodeError:
        raw = response.content[0].text
        s = raw.find("{")
        e = raw.rfind("}") + 1
        estimate = json.loads(raw[s:e]) if s >= 0 else {}

    tiers = estimate.get("tiers", {})
    rec = estimate.get("recommendation", "balanced")

    print(f"\n  {'─'*54}")
    print(f"  ┌{'─'*10}┬{'─'*8}┬{'─'*10}┬{'─'*8}┬{'─'*14}┐")
    print(f"  │ {'Tier':<8} │ {'Budget':>6} │ {'Time':>8} │ {'Nodes':>6} │ {'Discovery':<12} │")
    print(f"  ├{'─'*10}┼{'─'*8}┼{'─'*10}┼{'─'*8}┼{'─'*14}┤")
    for name in ["thorough", "balanced", "focused", "scout"]:
        t = tiers.get(name, {})
        marker = " ◀" if name == rec else ""
        print(f"  │ {name:<8} │ ${t.get('budget', '?'):>5} │ {str(t.get('estimated_time_minutes', '?'))+' min':>8} │ {str(t.get('estimated_nodes', '?')):>6} │ {t.get('discovery_likelihood', '?'):<12} │{marker}")
    print(f"  └{'─'*10}┴{'─'*8}┴{'─'*10}┴{'─'*8}┴{'─'*14}┘")
    print(f"\n  Recommended: {rec}")
    if estimate.get("reasoning"):
        print(f"  Reasoning: {estimate['reasoning'][:100]}")

    return estimate, genesis_result


async def main():
    args = parse_args()

    # Playback mode — replay a previous run's events in the visualizer
    if args.playback:
        events_path = Path(args.playback)
        if not events_path.exists():
            print(f"ERROR: Events file not found: {events_path}")
            sys.exit(1)

        import http.server
        import threading
        import webbrowser

        viz_path = Path(__file__).parent / "visualizer.html"
        if not viz_path.exists():
            print("ERROR: visualizer.html not found")
            sys.exit(1)

        # Serve files locally so the visualizer can fetch the events file
        class Handler(http.server.SimpleHTTPRequestHandler):
            def __init__(self, *a, **kw):
                super().__init__(*a, directory=str(Path(__file__).parent), **kw)
            def log_message(self, *a): pass  # suppress logs

        server = http.server.HTTPServer(("localhost", 8766), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()

        # Open visualizer with events URL
        events_url = f"http://localhost:8766/{events_path.resolve().relative_to(Path(__file__).resolve().parent)}"
        viz_url = f"http://localhost:8766/visualizer.html?events={events_url}"
        print(f"\n  Playback: {events_path}")
        print(f"  Speed: {args.speed}x")
        print(f"  Opening visualizer...")
        webbrowser.open(viz_url)

        print(f"\n  Press Ctrl+C to stop.\n")
        try:
            await asyncio.Event().wait()  # wait forever
        except KeyboardInterrupt:
            server.shutdown()
        return

    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("ERROR: ANTHROPIC_API_KEY not set.")
        sys.exit(1)

    # Query mode — query existing knowledge graph, no exploration
    if args.query:
        from mycelium.knowledge_graph import KnowledgeGraph
        kg_path = Path(args.output_dir) / "knowledge.db"
        if not kg_path.exists():
            print(f"ERROR: No knowledge graph found at {kg_path}")
            print("Run an exploration first to build the knowledge graph.")
            sys.exit(1)

        kg = KnowledgeGraph(str(kg_path))
        stats = kg.stats()
        print(f"\nKnowledge graph: {stats['entities']} entities, "
              f"{stats['relationships']} relationships\n")

        # Find relevant entities
        entities = kg.find_entities(args.query)
        if not entities:
            print(f"No entities matching '{args.query}' found.")
            kg.close()
            return

        # Get context for top matches
        contexts = []
        for e in entities[:5]:
            ctx = kg.get_entity_context(e["name"], depth=2)
            if ctx.get("found"):
                contexts.append(ctx)

        # Send to LLM for natural language answer
        client = anthropic.Anthropic()
        context_str = json.dumps(contexts, indent=2, default=str)[:8000]
        response = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=2000,
            messages=[{"role": "user", "content":
                       f"Based on this knowledge graph data, answer the question: {args.query}\n\n"
                       f"KNOWLEDGE GRAPH CONTEXT:\n{context_str}\n\n"
                       f"Include specific data points. Note any contradictions or gaps. "
                       f"Mention related entities the user didn't ask about if relevant."}],
        )
        print(response.content[0].text)
        kg.close()
        return

    dynamic_source = None  # set by interactive mode if user picks a public API

    # Interactive mode: --visualize without --source opens browser for source selection
    if args.visualize and not args.source:
        from mycelium import events

        # Start server and open browser — user picks everything in the UI
        run_id = str(__import__('uuid').uuid4())[:8]
        run_dir = Path(args.output_dir) / run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        (run_dir / "nodes").mkdir(exist_ok=True)

        await events.start_server(run_dir=str(run_dir))

        # Wait for browser to connect before sending the connect event
        print(f"\n  Mycelium running — waiting for browser to connect...")
        for _ in range(30):  # wait up to 15 seconds
            await asyncio.sleep(0.5)
            if events._clients:
                break
        await asyncio.sleep(0.3)  # let the WebSocket handler settle

        events.emit("connect", {})
        print(f"  Browser connected — waiting for source selection...\n")

        # Wait for user to describe what they want to explore
        source_choice = await events.wait_for_client_message(timeout=600)
        if not source_choice or source_choice.get("type") != "select_source":
            print("  No source selected. Exiting.")
            await events.stop_server()
            return

        user_query = source_choice.get("query", "")
        print(f"  User wants to explore: {user_query}")

        # Use LLM to figure out which connector to use
        resolved = await _resolve_data_source(user_query)

        # Keep trying until we get a valid connector or user gives up
        dynamic_source = None
        while True:
            if not resolved.get("is_exploration", True):
                events.emit("source_response", {
                    "status": "not_exploration",
                    "message": resolved.get("message", "This is an exploration tool, not a chatbot."),
                })
                print(f"  Not an exploration request")
            elif resolved.get("connector"):
                # Built-in connector
                args.source = resolved["connector"]
                events.emit("source_response", {
                    "status": "connected",
                    "source_name": resolved.get("name", args.source),
                    "description": resolved.get("description", ""),
                })
                print(f"  Resolved to built-in connector: {args.source}")
                break
            elif resolved.get("api_config"):
                # Dynamic connector — LLM figured out the API
                from mycelium.data_sources.generic_api import GenericAPISource
                api_config = resolved["api_config"]
                dynamic_source = GenericAPISource(api_config)
                source_name = api_config.get("source_name", resolved.get("name", "API"))
                events.emit("source_response", {
                    "status": "connected",
                    "source_name": source_name,
                    "description": resolved.get("description", f"Connecting to {api_config.get('base_url', '')}"),
                })
                print(f"  Dynamic connector: {source_name} ({api_config.get('base_url', '')})")
                args.source = "__dynamic__"
                break
            else:
                events.emit("source_response", {
                    "status": "unavailable",
                    "message": resolved.get("message", "No public API found for this data source."),
                })
                print(f"  No connector or API found: {resolved.get('name', 'unknown')}")

            # Wait for user to try again
            source_choice = await events.wait_for_client_message(timeout=600)
            if not source_choice or source_choice.get("type") != "select_source":
                print("  User gave up. Exiting.")
                await events.stop_server()
                return
            user_query = source_choice.get("query", "")
            print(f"  User retry: {user_query}")
            resolved = await _resolve_data_source(user_query)

    if not args.source:
        print("ERROR: --source required (e.g., --source npm). Or use --visualize for browser selection.")
        sys.exit(1)

    if args.source == "__dynamic__" and dynamic_source:
        data_source = dynamic_source
    else:
        data_source = create_data_source(args.source)

    try:
        budget = args.budget

        # Estimation mode
        if args.estimate:
            estimate, genesis = await run_estimate(data_source, args.hint)
            tiers = estimate.get("tiers", {})

            if args.auto_proceed:
                tier = args.auto_proceed
                budget = tiers.get(tier, {}).get("budget", 10)
                print(f"\n  Auto-proceeding with '{tier}' tier: ${budget}")
            elif budget is None:
                print(f"\n  Proceed? (thorough/balanced/focused/scout/$amount or 'q' to quit): ", end="")
                choice = input().strip().lower()
                if choice == 'q':
                    return
                elif choice.startswith('$'):
                    budget = float(choice[1:])
                elif choice in tiers:
                    budget = tiers[choice].get("budget", 10)
                else:
                    budget = tiers.get("balanced", {}).get("budget", 10)
                    print(f"  Using balanced tier: ${budget}")

        # If --visualize without --budget, use interactive mode (user picks in browser)
        # If --budget provided, use that directly
        # If neither, default to $20
        if budget is None and not args.visualize:
            budget = 20.0

        # Run exploration
        orchestrator = Orchestrator(
            data_source=data_source,
            budget=budget,
            output_dir=args.output_dir,
            hints=args.hint,
            visualize=args.visualize,
        )

        exploration_data = await orchestrator.explore()

        report = await generate_report(exploration_data)

        report_path = Path(orchestrator.run_dir) / "report.md"
        with open(report_path, "w") as f:
            f.write(report)
        print(f"\n  Report saved to {report_path}")

        # Save comparison prompt
        comparison_path = Path(orchestrator.run_dir) / "comparison_prompt.md"
        with open(comparison_path, "w") as f:
            corpus = exploration_data.get("genesis", {}).get("corpus_summary", "")
            f.write(f"# Comparison Prompt\n\n"
                    f"Give this to Gemini Deep Research or Claude to test reproducibility:\n\n"
                    f"---\n\n"
                    f"Explore the following information space and tell me what interesting "
                    f"patterns, contradictions, gaps, or connections you find:\n\n"
                    f"{corpus}\n\n"
                    f"Tell me what you discover. Include specific references.\n")

        print(f"\n{'='*60}")
        print(report)

    finally:
        await data_source.close()


if __name__ == "__main__":
    asyncio.run(main())
