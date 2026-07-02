import sys
from pathlib import Path
from dotenv import load_dotenv

_ROOT = Path(__file__).resolve().parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

load_dotenv(_ROOT / ".env")

from agents.orchestrator.crew import games_intel_crew
from agents.synthesis import agent as synthesis_agent
from agents.workers.financial_overlay import worker as financial_worker
from agents.workers.market_player import worker as market_worker
from agents.workers.patch_notes import worker as patch_notes_worker
from agents.workers.studio_intel import worker as studio_intel_worker
from agents.workers.sentiment import worker as sentiment_worker

if __name__ == "__main__":
    print("=" * 60)
    print("=== Market & Player Data Collection ===")
    print("=" * 60)
    market_result = market_worker.run()
    print(f"\nTop 10 by CCU:")
    for g in market_result["top_10_by_ccu"]:
        print(f"  {g['title']}: {g['ccu']:,} CCU  |  review score: {g['review_score']}%")

    print("\n" + "=" * 60)
    print("=== Financial Overlay (Equity Snapshots) ===")
    print("=" * 60)
    financial_worker.run()

    print("\n" + "=" * 60)
    print("=== Studio Intel (EDGAR 8-K Signals) ===")
    print("=" * 60)
    studio_intel_worker.run()

    print("\n" + "=" * 60)
    print("=== Patch Notes & Update Cadence ===")
    print("=" * 60)
    patch_result = patch_notes_worker.run()
    print(f"Patch notes: {patch_result['events_written']} events written | {patch_result['error_count']} errors")

    print("\n" + "=" * 60)
    print("=== Sentiment Analysis (Reddit + Steam) ===")
    print("=" * 60)
    sentiment_result = sentiment_worker.run()
    print(f"Sentiment: {sentiment_result['games_processed']} games written | {sentiment_result['error_count']} errors")

    print("\n" + "=" * 60)
    print("=== Synthesis & Weekly Briefing ===")
    print("=" * 60)
    synthesis_result = synthesis_agent.run()
    print(
        f"Synthesis: {synthesis_result['divergence_count']} divergences | "
        f"{synthesis_result['risk_count']} risks"
    )

    print("\n" + "=" * 60)
    print("=== Weekly CrewAI Pipeline ===")
    print("=" * 60)
    result = games_intel_crew.kickoff()
    print("\nPipeline complete.")
    print(str(result).encode(sys.stdout.encoding, errors="replace").decode(sys.stdout.encoding))
