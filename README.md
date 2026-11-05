# Knowledge Assistant: agentic KM pilot

This is a working build of the *Agentic Knowledge Management Platform* pilot proposal. You ask a question in a chat window. The system searches Confluence and Jira, checks that **you** are allowed to see every page it found, writes an answer, and links each fact to its source.

Everything is open source and runs on your own machine. Nothing is sent to an outside AI service.

> **Just want to run it?** Go to **[SETUP.md](SETUP.md)**. It's written for a 2019 MacBook Pro and assumes no technical background.

---

## The idea in one picture

Think of a company library with a very careful librarian.

```
 You ──► Chat website ──► Head librarian (orchestrator agent)
                               │  "Is this a Jira question, a Confluence question, or both?"
                ┌──────────────┴──────────────┐
                ▼                             ▼
      Confluence specialist            Jira specialist          ◄─ domain agents
                │                             │
                └──────────► Atlassian connector (MCP server) ◄─┘
                               │
              1. FIND    search the card catalogue (Qdrant), which
                         only shows cards you're allowed to see
              2. CONFIRM re-open each page in Confluence/Jira *as you*;
                         if Confluence says no, it's dropped
              3. RULES   rulebook (OPA) checks the page's secrecy label
                               │
                               ▼
      Head librarian writes the answer using only those pages,
      a fact-checker removes any sentence that cites no source,
      and every step is written into a ledger that can't be edited (audit log).
```

## What each part is, in plain words

| Part | Everyday analogy | What it really is | Where |
|---|---|---|---|
| **Chat website** | The library's front desk | Next.js + assistant-ui web app with sign-in, streaming answers, sources, 👍/👎 | `web/` |
| **Keycloak** | The badge office | Single sign-on. It knows who you are and which groups you're in, and swaps your badge for a Confluence/Jira "visitor pass" in your name (RFC 8693 token exchange) | `keycloak/` |
| **Orchestrator agent** | Head librarian | LangGraph graph: check the user, classify the question, plan, ask the specialists in parallel, rank the evidence, write, fact-check, answer or say "I don't know" | `kms/agents/orchestrator.py` |
| **Domain agents** | Specialist librarians | One LangGraph sub-graph per source (Confluence, Jira; ServiceNow is ready for Phase 2) | `kms/agents/domains.py` |
| **MCP server** | The door to the archive | Model Context Protocol server with read-only tools (`search_pages`, `get_page`, `search_issues`, `get_issue`, `get_linked_issues`, `list_recent_changes`) | `kms/mcp_servers/` |
| **Qdrant** | Card catalogue | Search index with "meaning" search and keyword search. Every passage carries who may read it, and the filter runs *inside* the search | `kms/index/` |
| **OPA** | The rulebook | Open Policy Agent rules for secrecy labels (public / internal / confidential / highly confidential / restricted) | `policies/kms.rego` |
| **LiteLLM** | Switchboard | Model gateway. The app only asks for "km-synth" or "km-embed", and the gateway decides which model answers. Swapping models is a config change | `litellm/` |
| **Models** | The writer | Demo mode needs no download. Laptop mode runs small Qwen3 + bge-m3 models via Ollama. Production uses gpt-oss / Qwen3 on vLLM | `litellm/*.yaml` |
| **Ingestion** | Keeping the catalogue up to date | Webhook mail slot → queue (Valkey) → workers re-index changed pages within seconds. Permissions re-synced every 15 min (pauses if >5% would vanish). Nightly full comparison | `kms/ingest/` |
| **Audit log** | A ledger in ink | PostgreSQL table that rejects edits and deletes. Every question, every allow/deny decision, every source used | `db/init.sql` |
| **Guardrails** | Security guard | Blocks "ignore your instructions" attacks, drops pages containing hidden instructions, masks personal data in answers | `kms/guardrails.py` |
| **Pretend Atlassian** | A practice library | A fake Confluence + Jira with sample pages, tickets and 5 users, enforcing permissions like the real thing | `kms/mock_atlassian/` |

## The sample users

| User | Role | Can see |
|---|---|---|
| **alice** | Engineer, on-call SRE | Engineering docs, SRE-only database pages, the confidential architecture page |
| **bob** | Engineer | Engineering docs, but **not** SRE pages or confidential ones |
| **carol** | HR | HR space and HR tickets, but **not** the highly-confidential salary bands |
| **dave** | Security engineer | Engineering docs + the security-only ticket about card numbers in logs |
| **erin** | Contractor | Company handbook + the Ops project only |

All passwords are `password`. Ask each of them *"How do I get production database access?"* and compare.

## What has been tested

These checks ran in a full Docker run of all 14 services:

- **Access control suite** (`tests/test_access_control_e2e.py`): 5 users × 16 searches each. Every returned document was on that user's allowed list, so there were **no leaks**. A page with hidden instructions is never returned. When access is revoked in the source, the very next question respects it, before any re-sync.
- **Golden-set quality gate** (`eval/run_eval.py`, 12 questions): 0 access leaks, 100% expected-source hit rate, 100% honest "I can't answer" when the user lacks access.
- **Unit tests** and **OPA policy tests**.
- **Browser test**: signing in through Keycloak as alice/bob/carol, asking questions, getting clickable sources.
- **Live updates**: editing a page fires a webhook, a worker re-indexes it, and the next answer uses the new text.

What has **not** been tested yet: real AI models (the build environment couldn't download them, so tests used the demo model), and a real Confluence/Jira site. See [docs/DECISIONS.md](docs/DECISIONS.md) for the details.

## Folder map

```
kms/                 Python backend (one image runs every service)
  agents/            orchestrator + domain agents (LangGraph)
  mcp_servers/       Atlassian MCP server; ServiceNow template (Phase 2)
  sources/           Confluence + Jira API clients
  index/             Qdrant index, chunking, keyword vectors
  ingest/            webhooks, queue worker, sync jobs, scheduler
  api/               HTTP API for the website (streaming)
  mock_atlassian/    pretend Confluence + Jira with sample data
  devtools/          offline demo model
web/                 chat website (Next.js + assistant-ui)
keycloak/            sign-in setup: users, groups, token exchange
policies/            OPA rulebook + its tests
db/                  audit ledger schema + example compliance queries
litellm/             model gateway configs: demo / ollama (laptop) / vllm (production)
eval/                golden questions, quality gate, red-team prompts
tests/               unit tests + access-control suite
airflow/dags/        production schedules (15-min ACL sync, nightly reconcile, ...)
deploy/              Kubernetes (Helm chart, Argo CD, GPU inference)
observability/       optional Langfuse + Prometheus + Grafana
loadtest/            k6 load test
docker-compose.yml   the whole system on one laptop
```
