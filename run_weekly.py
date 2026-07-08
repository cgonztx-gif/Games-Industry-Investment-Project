import sys
from pathlib import Path
from dotenv import load_dotenv
from langsmith import trace

_ROOT = Path(__file__).resolve().parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

load_dotenv(_ROOT / ".env")

from agents.tracing import configure_tracing, traced_step
from agents.orchestrator.crew import games_intel_crew
from agents.synthesis import agent as synthesis_agent
from agents.workers.financial_overlay import worker as financial_worker
from agents.workers.market_player import worker as market_worker
from agents.workers.patch_notes import worker as patch_notes_worker
from agents.workers.studio_intel import worker as studio_intel_worker
from agents.workers.news import worker as news_worker
from agents.workers.sentiment import worker as sentiment_worker
from agents.workers.discovery import worker as discovery_worker
from agents.portfolio import manager as portfolio_manager
from agents.portfolio import execution_agent
from agents.portfolio import returns_tracker

if __name__ == "__main__":
    configure_tracing()
    with trace("weekly_intel_run", run_type="chain"):
        print("=" * 60)
        print("=== Market & Player Data Collection ===")
        print("=" * 60)
        market_result = traced_step("market_player_worker")(market_worker.run)()
        print(f"\nTop 10 by CCU:")
        for g in market_result["top_10_by_ccu"]:
            print(f"  {g['title']}: {g['ccu']:,} CCU  |  review score: {g['review_score']}%")

        print("\n" + "=" * 60)
        print("=== Financial Overlay (Equity Snapshots) ===")
        print("=" * 60)
        traced_step("financial_overlay_worker")(financial_worker.run)()

        print("\n" + "=" * 60)
        print("=== Studio Intel (EDGAR 8-K Signals) ===")
        print("=" * 60)
        traced_step("studio_intel_worker")(studio_intel_worker.run)()

        print("\n" + "=" * 60)
        print("=== Patch Notes & Update Cadence ===")
        print("=" * 60)
        patch_result = traced_step("patch_notes_worker")(patch_notes_worker.run)()
        print(f"Patch notes: {patch_result['events_written']} events written | {patch_result['error_count']} errors")

        print("\n" + "=" * 60)
        print("=== News Article Ingestion ===")
        print("=" * 60)
        news_result = traced_step("news_worker")(news_worker.run)()
        print(
            f"News: {news_result['items_written']} articles matched | "
            f"{news_result['articles_fetched']} fetched | "
            f"{news_result['entities_with_coverage']} entities with coverage"
        )

        print("\n" + "=" * 60)
        print("=== Sentiment Analysis (Reddit + Steam + News) ===")
        print("=" * 60)
        sentiment_result = traced_step("sentiment_worker")(sentiment_worker.run)()
        print(
            f"Sentiment: {sentiment_result['games_processed']} games written | "
            f"{sentiment_result['error_count']} errors | "
            f"reddit_blocked={sentiment_result['reddit_blocked_count']}"
        )

        print("\n" + "=" * 60)
        print("=== Discovery (New Watchlist Candidates) ===")
        print("=" * 60)
        discovery_result = traced_step("discovery_worker")(discovery_worker.run)()
        print(
            f"Discovery: {discovery_result['proposals_written']} proposals written "
            f"({discovery_result['game_level_count']} game-level, "
            f"{discovery_result['company_level_count']} company-level) | "
            f"{discovery_result['error_count']} errors"
        )
        print("Review with: select * from watchlist_proposals where status = 'pending';")

        print("\n" + "=" * 60)
        print("=== Synthesis & Weekly Briefing ===")
        print("=" * 60)
        synthesis_result = traced_step("synthesis_agent")(synthesis_agent.run)()
        print(
            f"Synthesis: {synthesis_result['divergence_count']} divergences | "
            f"{synthesis_result['risk_count']} risks"
        )

        print("\n" + "=" * 60)
        print("=== Portfolio Manager (Trade Plan) ===")
        print("=" * 60)
        plan_result = traced_step("portfolio_manager")(portfolio_manager.build_trade_plan)()
        if plan_result:
            print(
                f"Trade plan for week_of {plan_result['week_of']}: "
                f"{plan_result['order_count']} orders | "
                f"{plan_result['watch_count']} watch items"
            )
            print("New orders are pending. Review with: python scripts/review_trade_plans.py list")
        else:
            print("No trade plan produced this week.")

        print("\n" + "=" * 60)
        print("=== Execution Agent (Approved Orders) ===")
        print("=" * 60)
        execution_result = traced_step("execution_agent")(execution_agent.run)()
        print(
            f"Execution: {execution_result['orders_checked']} checked | "
            f"{execution_result['orders_placed']} placed | "
            f"{execution_result['error_count']} errors"
        )

        print("\n" + "=" * 60)
        print("=== Returns Tracker (Portfolio Snapshot) ===")
        print("=" * 60)
        returns_result = traced_step("returns_tracker")(returns_tracker.run)()
        if returns_result:
            print(
                f"Snapshot: total_value=${returns_result['total_value']} | "
                f"return={returns_result['total_return_pct']}% | "
                f"benchmark={returns_result['benchmark_return_pct']}%"
            )
        else:
            print("Snapshot skipped (account state unavailable).")

        print("\n" + "=" * 60)
        print("=== Weekly CrewAI Pipeline ===")
        print("=" * 60)
        try:
            result = traced_step("games_intel_crew_kickoff", run_type="chain")(games_intel_crew.kickoff)()
            print("\nPipeline complete.")
            print(str(result).encode(sys.stdout.encoding, errors="replace").decode(sys.stdout.encoding))
        except Exception as exc:
            # This crew is a placeholder confirmation pass with no durable output of
            # its own (see agents/orchestrator/crew.py) -- the weekly briefing above
            # has already been written, so a failure here must not mark the whole
            # run as failed.
            print(f"\n[run_weekly] CrewAI placeholder pipeline failed (non-fatal): {exc}")
