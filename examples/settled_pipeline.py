"""
settled_pipeline.py — End-to-end ADK + settlement demo.

Demonstrates:
  1. Provider agent exposed with to_settled_a2a()
  2. Requester agent consuming it with SettledRemoteAgent
  3. Automatic escrow creation, task execution, and settlement

Prerequisites:
    - Exchange running at http://localhost:3000
    - A2ASE_API_KEY set in environment

Usage:
    # Terminal 1: Start the exchange
    cd ../a2a-settlement && python -m exchange

    # Terminal 2: Start the provider
    python examples/settled_pipeline.py --provider

    # Terminal 3: Run the requester
    python examples/settled_pipeline.py --requester
"""

from __future__ import annotations

import argparse
import sys


def run_provider() -> None:
    """Start a settlement-aware provider agent."""
    from google.adk.agents import Agent
    from adk_a2a_settlement import SettlementConfig, to_settled_a2a

    # Define the provider agent
    def analyze_sentiment(text: str) -> str:
        """Analyze sentiment of the given text and return a score."""
        # Simplified sentiment analysis
        positive_words = {"good", "great", "excellent", "happy", "profit", "up", "growth"}
        negative_words = {"bad", "terrible", "loss", "down", "decline", "poor"}

        words = set(text.lower().split())
        pos = len(words & positive_words)
        neg = len(words & negative_words)
        total = pos + neg or 1
        score = (pos - neg) / total

        if score > 0.3:
            sentiment = "positive"
        elif score < -0.3:
            sentiment = "negative"
        else:
            sentiment = "neutral"

        return f"Sentiment: {sentiment} (score: {score:.2f}, confidence: 0.85)"

    agent = Agent(
        name="sentiment_analyst",
        model="gemini-2.5-flash",
        instruction="You perform sentiment analysis on text. Use the analyze_sentiment tool.",
        description="Sentiment analysis agent with settlement",
        tools=[analyze_sentiment],
    )

    config = SettlementConfig(exchange_url="http://localhost:3000")

    app = to_settled_a2a(
        agent=agent,
        config=config,
        pricing={
            "sentiment-analysis": {
                "baseTokens": 100,
                "model": "per-request",
                "currency": "ATE",
            }
        },
        port=8001,
    )

    import uvicorn
    print("Starting settlement-aware provider on port 8001...")
    uvicorn.run(app, host="127.0.0.1", port=8001)


def run_requester() -> None:
    """Run a requester that consumes the settled provider."""
    from adk_a2a_settlement import SettledRemoteAgent, SettlementConfig

    config = SettlementConfig(exchange_url="http://localhost:3000")

    # Create settled remote agent (auto-discovers settlement from agent card)
    analyst = SettledRemoteAgent(
        name="analyst",
        description="Remote sentiment analysis agent",
        agent_card="http://localhost:8001/.well-known/agent.json",
        config=config,
    )

    print("Settlement info:", analyst.settlement_info)

    # Create escrow
    print("\n1. Creating escrow...")
    escrow = analyst.create_escrow(
        task_id="demo-task-001",
        task_type="sentiment-analysis",
        amount=100,
    )
    print(f"   Escrow created: {escrow['escrow_id']}")

    # In a real scenario, you'd send the task via the RemoteA2aAgent
    # and wait for the response. Here we simulate success.
    print("\n2. Task would execute via A2A protocol...")
    print("   (In production, use analyst.remote_agent as a sub_agent)")

    # Settle
    print("\n3. Releasing escrow (task succeeded)...")
    result = analyst.release(task_id="demo-task-001")
    print(f"   Released: {result}")

    print("\nDone!")


def main() -> int:
    parser = argparse.ArgumentParser(description="ADK + A2A Settlement demo")
    parser.add_argument(
        "--provider", action="store_true", help="Run as the provider agent"
    )
    parser.add_argument(
        "--requester", action="store_true", help="Run as the requester agent"
    )
    args = parser.parse_args()

    if args.provider:
        run_provider()
    elif args.requester:
        run_requester()
    else:
        parser.print_help()
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
