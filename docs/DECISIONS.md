# Decisions, defaults and gaps

This file records where the proposal left a choice open, what was picked, and what isn't built or tested yet.

## Defaults picked where the proposal was open

| Topic | Choice | Why |
|---|---|---|
| Atlassian MCP server | Purpose-built FastMCP server (`kms/mcp_servers/atlassian_server.py`) with the exact tools listed in §5.4, instead of a fork of `mcp-atlassian` | Find-then-confirm, OPA and audit need to sit *inside* every tool call. A small server we own is easier to harden and audit than a fork. It can be swapped for a hardened fork later because the tool names are the same |
| Interim models (Weeks 1–2) | **Demo model** (no download) plus **Ollama** with `qwen3:4b`, `qwen3:1.7b` and `bge-m3` | The proposal names llama.cpp. Ollama wraps llama.cpp and is the easiest install on a Mac. LiteLLM can point at a plain llama.cpp server instead with no code change |
| Keyword half of hybrid search | Hashed BM25-style sparse vectors with Qdrant's IDF modifier | bge-m3's own sparse output isn't exposed by Ollama or TEI's OpenAI endpoint. Dense search still uses bge-m3. The two are combined with RRF |
| Domain agent control flow | Fixed graph steps (search → expand linked issues) rather than letting the model pick tools freely | Explicit, testable control flow, as §4.4 asks. Small models are also unreliable at tool calling |
| "Confirm as the user" | Two modes. `exchange` uses a Keycloak RFC 8693 token exchange and calls the source with that token (Data Center behind your IdP, and the demo). `permission_check` has the service account ask Confluence/Jira "can account X read Y?" (Atlassian Cloud) | Atlassian Cloud doesn't accept tokens from your Keycloak. Its permission-check APIs give the same "source decides" guarantee |
| Nested page restrictions | Up to 4 levels of permission stored per passage (space + page + 2 ancestors). Deeper nesting fails *closed*: the page is never found | Safe default |
| Unreachable rulebook (OPA) | Fail closed: the user sees public content only | Safe default |
| Audit write failure | The answer still goes out, and the full event is written to the service log (shipped to Loki/SIEM) | Availability during the pilot. Change it to fail closed if compliance requires |
| Scheduler on a laptop | A small built-in scheduler runs the same jobs as the Airflow DAGs | Airflow needs about 2 GB of RAM. The DAGs are in `airflow/dags/` for production |
| Mock data | 11 Confluence pages, 6 Jira issues, 5 personas covering each permission type | Lets the access-control suite prove every rule |

## Built, but light compared to the proposal (production hardening needed)

- **Guardrails:** pattern-based injection and PII checks run by default. Presidio switches on with `KMS_GUARD_PRESIDIO=1` plus the `presidio` extra. NeMo Guardrails, LLM Guard and Granite Guardian aren't wired in yet, but the hook points are the three functions in `kms/guardrails.py`.
- **Groundedness check:** word-overlap check per sentence plus strict-citation stripping. An LLM-judge check is the natural upgrade once GPUs arrive (Week 5).
- **Human-in-the-loop:** feedback lands in a `feedback` table with `triage = pending` for SME review before entering the eval set. A LangGraph interrupt for sensitive answers isn't wired in.
- **OpenMetadata catalog, confidential-computing nodes, Harbor/OpenBao/Rook-Ceph:** platform-team items. Only referenced in `deploy/platform/README.md`.
- **Helm chart:** covers the application layer and renders cleanly. Platform services come from their upstream charts.

## Not tested yet (needs your environment)

- **Real AI models.** The build environment couldn't download models, so every test used the demo model. The LiteLLM Ollama config was checked to load and route correctly, and the first real run happens on your Mac (SETUP.md, Step 7).
- **A real Confluence/Jira site.** The connectors follow Atlassian's Cloud REST API and were tested against the pretend Atlassian, which mimics those endpoints. Data Center needs a small path adapter (REST v1).
- **Kubernetes deployment, Airflow DAGs, Langfuse stack, k6 and promptfoo runs:** written, and the Helm chart renders, but they haven't been run against a cluster.
