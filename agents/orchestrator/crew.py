from crewai import Agent, Task, Crew, Process

# ---------------------------------------------------------------------------
# Agents
# ---------------------------------------------------------------------------

market_player = Agent(
    role="Market & Player Data Analyst",
    goal="Collect and interpret quantitative engagement metrics for tracked games.",
    backstory="A games-industry analyst who reads player-count and review trends as product-health signals.",
    llm="anthropic/claude-sonnet-4-6",
    tools=[],
    verbose=False,
)

sentiment = Agent(
    role="Community Sentiment Analyst",
    goal="Measure player sentiment across Reddit, Steam reviews, and social platforms.",
    backstory="A community researcher who distinguishes genuine player frustration from vocal-minority noise.",
    llm="anthropic/claude-sonnet-4-6",
    tools=[],
    verbose=False,
)

patch_notes = Agent(
    role="Patch Cadence Analyst",
    goal="Track update frequency and classify patch types to infer studio health.",
    backstory="A live-ops veteran who knows that patch cadence predicts player retention before the metrics do.",
    llm="anthropic/claude-sonnet-4-6",
    tools=[],
    verbose=False,
)

studio_intel = Agent(
    role="Studio Intelligence Analyst",
    goal="Monitor job postings, press releases, and SEC filings for leading studio signals.",
    backstory="A talent-market researcher who treats hiring surges and exec departures as forward indicators.",
    llm="anthropic/claude-sonnet-4-6",
    tools=[],
    verbose=False,
)

financial_overlay = Agent(
    role="Financial Overlay Analyst",
    goal="Map game-level signals onto parent-company equity and financial data.",
    backstory="An equity analyst who bridges game-layer data and public-market fundamentals.",
    llm="anthropic/claude-sonnet-4-6",
    tools=[],
    verbose=False,
)

discovery = Agent(
    role="Discovery Agent",
    goal="Identify new watchlist candidates showing early investment-relevant signals.",
    backstory="A trend-spotter who surfaces breakout titles before they reach mainstream financial coverage.",
    llm="anthropic/claude-sonnet-4-6",
    tools=[],
    verbose=False,
)

orchestrator = Agent(
    role="Lead Orchestrator",
    goal="Coordinate all worker agents and synthesize their outputs into a coherent weekly briefing.",
    backstory="A senior investment analyst who knows which data layers matter and how to weigh conflicting signals.",
    llm="anthropic/claude-opus-4-8",
    tools=[],
    verbose=False,
)

# ---------------------------------------------------------------------------
# Placeholder tasks (Phase 1 — real logic added per phase)
# ---------------------------------------------------------------------------

task_market = Task(
    description=(
        "Player metric data (CCU, review scores, review velocity) has already been collected "
        "by the market_player worker module and written to the player_metrics table for today. "
        "Confirm that data collection completed successfully. "
        "(Signal interpretation and anomaly detection added in Phase 4 synthesis.)"
    ),
    expected_output="Market & player data collection complete.",
    agent=market_player,
)

task_sentiment = Task(
    description=(
        "Sentiment data (Reddit + Steam reviews) has already been collected by the "
        "sentiment worker module and written to the sentiment_snapshots table for today. "
        "Each game has a VADER-weighted score (1–10), top 3 aspect-themes from Claude Haiku ABSA, "
        "a divergence flag (text sentiment vs. review count signal), and a vocal-minority note. "
        "Confirm that sentiment collection completed successfully. "
        "(Signal interpretation and cross-game comparison added in Phase 4 synthesis.)"
    ),
    expected_output="Sentiment data collection complete.",
    agent=sentiment,
)

task_patch = Task(
    description="Return the string OK. (Placeholder — patch cadence analysis added in Phase 3.)",
    expected_output="OK",
    agent=patch_notes,
)

task_studio = Task(
    description="Return the string OK. (Placeholder — studio signal monitoring added in Phase 3.)",
    expected_output="OK",
    agent=studio_intel,
)

task_financial = Task(
    description="Return the string OK. (Placeholder — financial overlay added in Phase 3.)",
    expected_output="OK",
    agent=financial_overlay,
)

task_discovery = Task(
    description="Return the string OK. (Placeholder — discovery logic added in Phase 5.)",
    expected_output="OK",
    agent=discovery,
)

task_orchestrate = Task(
    description="Collect the outputs from all worker agents and return a summary. For now, confirm all workers returned OK.",
    expected_output="All worker agents returned OK. Pipeline is healthy.",
    agent=orchestrator,
)

# ---------------------------------------------------------------------------
# Crew
# ---------------------------------------------------------------------------

games_intel_crew = Crew(
    agents=[market_player, sentiment, patch_notes, studio_intel, financial_overlay, discovery, orchestrator],
    tasks=[task_market, task_sentiment, task_patch, task_studio, task_financial, task_discovery, task_orchestrate],
    process=Process.sequential,
    verbose=False,
)
