import argparse
import sys
from pathlib import Path
from dotenv import load_dotenv
from langsmith import trace

_ROOT = Path(__file__).resolve().parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

load_dotenv(_ROOT / ".env")

from agents.tracing import configure_tracing, traced_step
from agents.token_tracking import token_tracked_step
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


def _banner(title: str) -> None:
    print("\n" + "=" * 60)
    print(f"=== {title} ===")
    print("=" * 60)


def step_market_player():
    _banner("Market & Player Data Collection")
    market_result = traced_step("market_player_worker")(token_tracked_step("market_player_worker")(market_worker.run))()
    print(f"\nTop 10 by CCU:")
    for g in market_result["top_10_by_ccu"]:
        print(f"  {g['title']}: {g['ccu']:,} CCU  |  review score: {g['review_score']}%")


def step_financial_overlay():
    _banner("Financial Overlay (Equity Snapshots)")
    traced_step("financial_overlay_worker")(token_tracked_step("financial_overlay_worker")(financial_worker.run))()


def step_studio_intel():
    _banner("Studio Intel (EDGAR 8-K Signals)")
    traced_step("studio_intel_worker")(token_tracked_step("studio_intel_worker")(studio_intel_worker.run))()


def step_patch_notes():
    _banner("Patch Notes & Update Cadence")
    patch_result = traced_step("patch_notes_worker")(token_tracked_step("patch_notes_worker")(patch_notes_worker.run))()
    print(f"Patch notes: {patch_result['events_written']} events written | {patch_result['error_count']} errors")


def step_news():
    _banner("News Article Ingestion")
    news_result = traced_step("news_worker")(token_tracked_step("news_worker")(news_worker.run))()
    print(
        f"News: {news_result['items_written']} articles matched | "
        f"{news_result['articles_fetched']} fetched | "
        f"{news_result['entities_with_coverage']} entities with coverage"
    )


def step_sentiment():
    _banner("Sentiment Analysis (Reddit + Steam + News)")
    sentiment_result = traced_step("sentiment_worker")(token_tracked_step("sentiment_worker")(sentiment_worker.run))()
    print(
        f"Sentiment: {sentiment_result['games_processed']} games written | "
        f"{sentiment_result['error_count']} errors | "
        f"reddit_blocked={sentiment_result['reddit_blocked_count']}"
    )


def step_discovery():
    _banner("Discovery (New Watchlist Candidates)")
    discovery_result = traced_step("discovery_worker")(token_tracked_step("discovery_worker")(discovery_worker.run))()
    print(
        f"Discovery: {discovery_result['proposals_written']} proposals written "
        f"({discovery_result['game_level_count']} game-level, "
        f"{discovery_result['company_level_count']} company-level) | "
        f"{discovery_result['error_count']} errors"
    )
    print("Review with: select * from watchlist_proposals where status = 'pending';")


def step_synthesis():
    _banner("Synthesis & Weekly Briefing")
    synthesis_result = traced_step("synthesis_agent")(token_tracked_step("synthesis_agent")(synthesis_agent.run))()
    print(
        f"Synthesis: {synthesis_result['divergence_count']} divergences | "
        f"{synthesis_result['risk_count']} risks"
    )


def step_portfolio_manager():
    _banner("Portfolio Manager (Trade Plan)")
    plan_result = traced_step("portfolio_manager")(token_tracked_step("portfolio_manager")(portfolio_manager.build_trade_plan))()
    if plan_result:
        print(
            f"Trade plan for week_of {plan_result['week_of']}: "
            f"{plan_result['order_count']} orders | "
            f"{plan_result['watch_count']} watch items"
        )
        print("New orders are pending. Review with: python scripts/review_trade_plans.py list")
    else:
        print("No trade plan produced this week.")


def step_execution_agent():
    _banner("Execution Agent (Approved Orders)")
    execution_result = traced_step("execution_agent")(execution_agent.run)()
    print(
        f"Execution: {execution_result['orders_checked']} checked | "
        f"{execution_result['orders_placed']} placed | "
        f"{execution_result['error_count']} errors"
    )


def step_returns_tracker():
    _banner("Returns Tracker (Portfolio Snapshot)")
    returns_result = traced_step("returns_tracker")(returns_tracker.run)()
    if returns_result:
        print(
            f"Snapshot: total_value=${returns_result['total_value']} | "
            f"return={returns_result['total_return_pct']}% | "
            f"benchmark={returns_result['benchmark_return_pct']}%"
        )
    else:
        print("Snapshot skipped (account state unavailable).")


def step_crew():
    _banner("Weekly CrewAI Pipeline")
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


# Each phase maps to one GitHub Actions job in .github/workflows/weekly.yml --
# hosted runners hard-cap every job at 6 hours (timeout-minutes can only lower
# it), and a full-watchlist run no longer fits in one job (run 29438324984
# timed out at that cap). Cross-phase state flows entirely through Supabase,
# so phases are safe to run as separate processes in `needs:`-chained jobs.
PHASE_ORDER = ["collect", "news", "sentiment", "synthesize"]
PHASE_STEPS = {
    "collect": [step_market_player, step_financial_overlay, step_studio_intel, step_patch_notes],
    "news": [step_news],
    "sentiment": [step_sentiment],
    "synthesize": [
        step_discovery,
        step_synthesis,
        step_portfolio_manager,
        step_execution_agent,
        step_returns_tracker,
        step_crew,
    ],
}


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Run the weekly intel pipeline (all phases, or one phase for the multi-job CI layout)."
    )
    parser.add_argument(
        "--phase",
        choices=PHASE_ORDER,
        default=None,
        help="Run only this phase; omit to run the full pipeline.",
    )
    args = parser.parse_args(argv)

    configure_tracing()
    trace_name = f"weekly_intel_run:{args.phase}" if args.phase else "weekly_intel_run"
    phases = [args.phase] if args.phase else PHASE_ORDER
    with trace(trace_name, run_type="chain"):
        for phase in phases:
            for step in PHASE_STEPS[phase]:
                step()


if __name__ == "__main__":
    main()
