# ADK A2A Settlement

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://python.org)
[![CI](https://github.com/a2a-settlement/adk-a2a-settlement/actions/workflows/ci.yml/badge.svg)](https://github.com/a2a-settlement/adk-a2a-settlement/actions/workflows/ci.yml)
[![codecov](https://codecov.io/gh/a2a-settlement/adk-a2a-settlement/graph/badge.svg)](https://codecov.io/gh/a2a-settlement/adk-a2a-settlement)

**A2A Settlement Extension integration for Google's Agent Development Kit (ADK).** Adds escrow-based payment settlement to ADK agents — both for exposing your agents as paid services and for consuming other agents' paid services.

```
┌─────────────────────┐                    ┌─────────────────────┐
│   Requester Agent   │    A2A Protocol    │   Provider Agent    │
│  (ADK + Settlement) │◄──────────────────►│  (ADK + Settlement) │
│                     │                    │                     │
│  SettledRemoteAgent │    escrow.held     │  to_settled_a2a()   │
│  auto-creates       │───────────────────►│  verify_escrow()    │
│  escrow             │                    │  before executing   │
│                     │   task.completed   │                     │
│  auto-releases      │◄───────────────────│  returns result     │
│  on success         │                    │                     │
└─────────────────────┘                    └─────────────────────┘
         │                                          │
         └──────────────┬───────────────────────────┘
                        │
               ┌────────▼────────┐
               │    Settlement   │
               │    Exchange     │
               │  (a2a-settlement│
               │     /exchange)  │
               └─────────────────┘
```

## Install

```bash
pip install adk-a2a-settlement
```

Or from source:

```bash
git clone https://github.com/a2a-settlement/adk-a2a-settlement
cd adk-a2a-settlement
pip install -e ".[dev]"
```

## Quickstart — Provider (exposing a settled agent)

```python
from google.adk.agents import Agent
from adk_a2a_settlement import SettlementConfig, to_settled_a2a

# 1. Define your ADK agent
analyst = Agent(
    name="sentiment_analyst",
    model="gemini-2.5-flash",
    instruction="Analyze the sentiment of the given text.",
    description="Sentiment analysis agent",
)

# 2. Expose it with settlement
app = to_settled_a2a(
    agent=analyst,
    config=SettlementConfig(),
    pricing={"sentiment-analysis": {"baseTokens": 100, "model": "per-request"}},
)

# 3. Run: uvicorn provider:app --port 8001
```

The provider's AgentCard will advertise the settlement extension so requesters know to escrow tokens before sending tasks.

## Quickstart — Requester (consuming a settled agent)

```python
from google.adk.agents import Agent
from adk_a2a_settlement import SettledRemoteAgent, SettlementConfig

# 1. Create a settled remote agent (reads settlement info from agent card)
analyst = SettledRemoteAgent(
    name="analyst",
    description="Remote sentiment analysis agent",
    agent_card="http://localhost:8001/.well-known/agent.json",
    config=SettlementConfig(),
)

# 2. Create escrow before sending task
escrow = analyst.create_escrow(task_id="task-001", task_type="sentiment-analysis")

# 3. Use the underlying RemoteA2aAgent in your ADK agent
orchestrator = Agent(
    name="orchestrator",
    model="gemini-2.5-flash",
    instruction="You coordinate analysis tasks.",
    sub_agents=[analyst.remote_agent],
)

# 4. After task completes, settle
analyst.release(task_id="task-001")  # or analyst.refund("task-001", reason="...")
```

## Settlement Tools for Agents

Give your ADK agent direct settlement capabilities:

```python
from google.adk.agents import Agent
from adk_a2a_settlement import SettlementConfig
from adk_a2a_settlement.tools import create_settlement_tools

agent = Agent(
    name="settlement_manager",
    model="gemini-2.5-flash",
    instruction="""You manage payments between agents. You can:
    - Check your balance
    - Create escrows for tasks
    - Release escrows when tasks complete
    - Refund escrows when tasks fail
    - Dispute unsatisfactory deliverables
    - Look up agents by skill""",
    tools=create_settlement_tools(SettlementConfig()),
)
```

Available tools: `check_balance`, `create_escrow`, `release_escrow`, `refund_escrow`, `dispute_escrow`, `lookup_agent`, `get_escrow_status`.

## Configuration

All settings come from environment variables:

| Env var | Default | Description |
|---------|---------|-------------|
| `A2ASE_EXCHANGE_URL` | `https://sandbox.a2a-settlement.org` | Exchange base URL (no `/v1`) |
| `A2ASE_API_KEY` | (required) | Your exchange API key |
| `A2ASE_NETWORK` | `sandbox` | Network: sandbox, mainnet, devnet |
| `A2ASE_TIMEOUT` | `30` | HTTP timeout in seconds |
| `A2ASE_AUTO_ESCROW` | `true` | Auto-create escrow on remote calls |
| `A2ASE_AUTO_SETTLE` | `true` | Auto-release/refund on completion |
| `A2ASE_DEFAULT_TTL` | `60` | Default escrow TTL in minutes |

Or pass a `SettlementConfig(...)` object directly.

## Architecture

```
adk_a2a_settlement/
  config.py      — Environment-driven settings (Pydantic, same pattern as litellm-a2a-settlement)
  provider.py    — to_settled_a2a() + verify_escrow() for the exposing side
  requester.py   — SettledRemoteAgent + discover_settlement() for the consuming side
  tools.py       — ADK function tools (check_balance, create_escrow, etc.)
  callbacks.py   — before/after model hooks for lifecycle management
```

### How it relates to A2A-SE

This repo wraps the [a2a-settlement](https://github.com/a2a-settlement/a2a-settlement) SDK for the ADK framework, same as:

| Integration | Framework | Pattern |
|------------|-----------|---------|
| `langgraph-a2a-settlement` | LangGraph | Graph nodes + create_settlement_graph |
| `crewai-a2a-settlement` | CrewAI | SettledCrew / SettledTask wrappers |
| `litellm-a2a-settlement` | LiteLLM | CustomLogger hooks (pre/post call) |
| **`adk-a2a-settlement`** | **Google ADK** | **to_settled_a2a() + SettledRemoteAgent + tools** |

**Related:** [a2a-settlement-mcp](https://github.com/a2a-settlement/a2a-settlement-mcp) (MCP tools), [a2a-settlement-auth](https://github.com/a2a-settlement/a2a-settlement-auth) (OAuth), [a2a-settlement-mediator](https://github.com/a2a-settlement/a2a-settlement-mediator) (disputes), [settlebridge-ai](https://github.com/a2a-settlement/settlebridge-ai) (gateway), [mcp-trust-gateway](https://github.com/a2a-settlement/mcp-trust-gateway) (MCP trust), [otel-agent-provenance](https://github.com/a2a-settlement/otel-agent-provenance) (OTel provenance), [a2a-federation-rfc](https://github.com/a2a-settlement/a2a-federation-rfc) (federation spec).

### Provider verification flow

```
Requester                    Exchange                     Provider
    │                           │                            │
    ├── create_escrow ─────────►│                            │
    │◄── escrow_id ────────────┤                            │
    │                           │                            │
    ├── A2A message ────────────┼───────────────────────────►│
    │   (metadata.a2a-se.       │                            │
    │    escrowId=...)          │                            │
    │                           │     verify_escrow() ──────►│
    │                           │◄── escrow status ─────────┤
    │                           │                            │
    │                           │         [do work]          │
    │◄── task result ───────────┼────────────────────────────┤
    │                           │                            │
    ├── release_escrow ────────►│                            │
    │                           ├── tokens transferred ─────►│
    │                           │                            │
```

## License

MIT. See `LICENSE`.
