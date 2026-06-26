import sys
from dotenv import load_dotenv
load_dotenv()

from agents.orchestrator.crew import games_intel_crew

if __name__ == "__main__":
    print("Starting weekly games-intel pipeline...")
    result = games_intel_crew.kickoff()
    print("\nPipeline complete.")
    print(str(result).encode(sys.stdout.encoding, errors="replace").decode(sys.stdout.encoding))
