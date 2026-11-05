# Setup guide for a 2019 MacBook Pro

This guide assumes no technical background. Each step says **what** to do, **why**, and **how to tell it worked**.

You'll type commands into **Terminal**, the Mac's text-command app. Open it with ⌘ + Space, type `Terminal`, then press Enter. To run a command, copy the grey box, paste it into Terminal, and press Enter.

**Time:** about 30–45 minutes the first time, mostly waiting for downloads.
**Disk space:** about 6 GB for demo mode, plus about 5 GB if you add real AI models. Your 256 GB is plenty, but check you have 15 GB free (Apple menu  → About This Mac → More Info → Storage).

---

## Step 0: Check how much memory your Mac has

Apple menu  → **About This Mac** → look at **Memory**.

- **16 GB or more:** follow everything below as written.
- **8 GB:** it still works. Close other apps (especially Chrome) while using it, and in Step 7 use the smaller model, as noted there.

---

## Step 1: Install Docker (the "shipping container" app)

**What it is:** The system is 14 small programs: the website, the login service, the search index, and so on. Docker runs each one in its own sealed box ("container") so they don't interfere with your Mac. One command then starts all of them together.

1. Go to <https://www.docker.com/products/docker-desktop/> and download **Docker Desktop for Mac with Intel chip** (your 2019 Mac has an Intel chip, *not* Apple silicon).
2. Open the downloaded file and drag Docker to Applications. Open Docker from Applications and accept the terms. A whale icon 🐳 appears in the top menu bar.
3. Give Docker enough memory: whale icon → **Settings** → **Resources** → set **Memory** to **6 GB** (or 4 GB if your Mac has 8 GB) and **CPUs** to 4. Click **Apply & restart**.

**Check it worked:**
```bash
docker --version && docker compose version
```
You should see two version numbers.

> **If Docker Desktop won't install** (some older macOS versions aren't supported), use **Colima** instead. It's free and lighter. Install Homebrew from <https://brew.sh>, then run:
> ```bash
> brew install colima docker docker-compose
> colima start --cpu 4 --memory 6 --disk 40
> ```
> Every `docker` command in this guide then works the same.

---

## Step 2: Get the project onto your Mac

1. Download **kms-pilot.zip** from the project thread (it's attached to my message).
2. Double-click it in Downloads to unzip. You'll get a folder called `kms-pilot`.
3. In Terminal, go into that folder:
```bash
cd ~/Downloads/kms-pilot
```
**Check:** run `ls`. You should see files such as `README.md`, `SETUP.md` and `docker-compose.yml`.

---

## Step 3: Create your settings file

**What it is:** `.env` is the system's settings sheet: which AI models to use, where Confluence lives, and so on. The defaults use **demo mode**, with no AI download, so you can see everything working first.

```bash
cp .env.example .env
```

---

## Step 4: Start everything

**What happens:** Docker downloads the building blocks (around 4 GB, once only) and builds the two parts written for this project: the backend and the website. The first run takes 10–20 minutes. Later starts take about 30 seconds.

```bash
docker compose up -d --build
```

When it finishes, wait about one minute for the login service to wake up, then check:
```bash
docker compose ps
```
**Check:** about 14 lines, each saying `Up` or `running`. If one says `Restarting`, see Troubleshooting at the bottom.

---

## Step 5: Fill the search index

**What it is:** The librarian can't search anything until the card catalogue is filled. This command reads every page and ticket in the pretend Confluence and Jira. For each one it records the text and **who is allowed to read it**.

```bash
docker compose exec agent-api kms index-all
```
**Check:** the last lines show `"documents": 11` for confluence and `"documents": 6` for jira.

(Shortcut: if you have `make` installed, `make index` does the same thing. The Mac offers to install it the first time you type `make`.)

---

## Step 6: Try it 🎉

Open **<http://localhost:3000>** in Safari or Chrome.

1. Click **Sign in**. You'll see the login page from Keycloak (the badge office).
2. Sign in as **alice** with password **password**.
3. Click an example question, or type: *How do I get production database access?*
4. Watch the status line ("Checking who you are… Searching and confirming access… Fact-checking…"). Then you'll see:
   - the answer, with numbered links like [1] after each fact,
   - the sources underneath, with "Updated" dates. An "older than 90 days" warning appears where a source is old,
   - a **Confidence** badge (High / Medium / Low),
   - 👍 / 👎 buttons. Your feedback is saved for review.

**Now see the access control work.** Click **Sign out**, sign in as **bob**, and ask the same question. Bob isn't in the SRE group, so the system tells him it can't answer confidently. It never mentions the SRE pages exist.

More things to try:

| Sign in as | Ask | What you should notice |
|---|---|---|
| carol | How many weeks of parental leave do we get? | Answer from the HR space (marked *confidential*) |
| bob | How many weeks of parental leave do we get? | Bob can't see HR, so he gets no answer |
| carol | What are the salary bands for E3? | Even HR can't see it: it's *highly confidential*, above her clearance |
| dave | Are card numbers visible in the debug logs? | Answer from a security-only Jira ticket |
| erin | What caused the VPN outage? | Contractor sees the Ops ticket through a project role |
| alice | What is the team lunch rota? | That page hides an "ignore your instructions" attack, so the guard drops it |

**What "demo mode" means:** right now no real AI is writing the answers. A stand-in copies the best-matching sentences from the sources. That's why answers read like stitched quotes. Everything else is real: sign-in, permissions, search, fact-checking and the audit log. Step 7 switches on real AI.

### See the "ledger" (audit log)
```bash
docker compose exec postgres psql -U kms -d kms -c "SELECT ts, event_type, user_id, doc_id, decision, reason FROM audit.events ORDER BY id DESC LIMIT 20;"
```
Every question, every page checked, and every allow/deny decision with its reason is recorded here. To see that it can't be altered, try deleting a row:
```bash
docker compose exec postgres psql -U kms -d kms -c "DELETE FROM audit.events WHERE id = 1;"
```
→ `ERROR: audit.events is append-only (DELETE blocked)`.

### See live updates (optional)
Edit a page in the pretend Confluence. This sends a "page changed" notice, just like the real Confluence would:
```bash
curl -X POST localhost:9000/admin/confluence/pages/3002 -H 'content-type: application/json' \
  -d '{"body":"<p>The new VPN client is called TunnelBlick Pro. Download it from the self-service portal.</p>"}'
```
A few seconds later, ask (as anyone) *Which VPN client should I install?*. The answer uses the new text.

### See instant revocation (optional)
Remove alice from the SRE group **in Confluence only**:
```bash
curl -X POST localhost:9000/admin/members/alice -H 'content-type: application/json' -d '{"groups":["all-staff","engineering"]}'
```
Ask as alice about database access again. The search index and her login badge still say "SRE", but the live check with Confluence now says no, so she gets no answer. That's the proposal's "revoked at 10:00, enforced at 10:01". To give the access back:
```bash
curl -X POST localhost:9000/admin/members/alice -H 'content-type: application/json' -d '{"groups":["all-staff","engineering","sre"]}'
```

---

## Step 7: Switch on real AI models (laptop mode)

**What it is:** **Ollama** runs open-source AI models directly on your Mac. Nothing leaves your computer. It runs outside Docker because it's faster that way on a Mac.

1. Download Ollama from <https://ollama.com/download> (macOS), install it and open it. A llama icon appears in the menu bar.
2. Download three small models (about 5 GB in total):
```bash
ollama pull qwen3:4b      # writes the answers
ollama pull qwen3:1.7b    # sorts questions (Jira vs Confluence)
ollama pull bge-m3        # turns text into "meaning numbers" for search
```
   > **8 GB Mac:** run `ollama pull qwen3:1.7b` and `ollama pull bge-m3` only. Then open `litellm/config.ollama.yaml` in TextEdit and change `qwen3:4b` to `qwen3:1.7b`.

3. Tell the system to use them. Open `.env` in TextEdit (in Finder press ⌘⇧. to show hidden files) and change these three lines:
```
LITELLM_CONFIG=config.ollama.yaml
KMS_PROMPT_SUFFIX=/no_think
KMS_USE_LLM_ROUTER=true
```
4. Restart, then rebuild the index. The "meaning numbers" from the real model differ from demo mode, so the catalogue must be refilled:
```bash
docker compose up -d
curl -X DELETE localhost:6333/collections/kms_confluence; curl -X DELETE localhost:6333/collections/kms_jira
docker compose exec agent-api kms index-all
```
**What to expect on a 2019 Intel MacBook:** no graphics card is used, so answers take roughly **20–90 seconds** and the fans will spin. The data-centre version in the proposal uses GPUs and targets under 15 seconds. The design is the same; only the speed differs.

To go back to demo mode, undo the three `.env` lines and repeat step 4.

---

## Day-to-day commands

| What | Command |
|---|---|
| Start | `docker compose up -d` |
| Stop (keeps your data) | `docker compose down` |
| Status | `docker compose ps` |
| Watch what the agents are doing | `docker compose logs -f agent-api mcp-atlassian` |
| Ask from Terminal | `docker compose exec agent-api kms ask "How do I deploy?" --as alice --api http://localhost:8000` |
| Run the access-control tests | `make test-acl` |
| Run the quality check (12 golden questions) | `make eval` |
| Reset the pretend Confluence/Jira | `curl -X POST localhost:9000/admin/reset` |
| Delete everything and free disk space | `docker compose down -v --rmi local` then `docker system prune` |

Useful web pages while it runs:

- Chat: <http://localhost:3000>
- Keycloak admin (users, groups): <http://localhost:8080> (admin / admin)
- Search index dashboard: <http://localhost:6333/dashboard>
- Pretend Confluence page: <http://localhost:9000/wiki/spaces/ENG/pages/1001>
- API health: <http://localhost:8088/health>

---

## Optional extras (heavier, off by default)

- **Dashboards and AI tracing** (Langfuse, Prometheus, Grafana). This needs about 3 GB more memory, so only use it with 16 GB:
  `docker compose -f docker-compose.yml -f docker-compose.observability.yml up -d`
  Langfuse is at <http://localhost:3002>. Create an account and project there, copy the keys into `.env` (`KMS_LANGFUSE_HOST=http://langfuse-web:3000`, plus the public and secret keys), then run `docker compose up -d`. Grafana is at <http://localhost:3001>.
- **Better document parsing** (Docling) and **PII detection** (Presidio) are large downloads (several GB). Turn them on in production, not on the laptop.

---

## Connecting your real Confluence and Jira (when you're ready)

Do this only with approval from whoever owns those systems. You'll need a read-only **service account**.

1. **Atlassian Cloud** (`yourcompany.atlassian.net`): create an API token for the service account at <https://id.atlassian.com/manage-profile/security/api-tokens>. Then set in `.env`:
```
KMS_CONFLUENCE_URL=https://yourcompany.atlassian.net
KMS_JIRA_URL=https://yourcompany.atlassian.net
KMS_CONFLUENCE_PUBLIC_URL=https://yourcompany.atlassian.net
KMS_JIRA_PUBLIC_URL=https://yourcompany.atlassian.net
KMS_ATLASSIAN_DEPLOYMENT=cloud
KMS_ATLASSIAN_SERVICE_USER=service-account@yourcompany.com
KMS_ATLASSIAN_SERVICE_TOKEN=<the API token>
KMS_CONFIRM_MODE=permission_check
KMS_CONFLUENCE_SPACES=ENG,OPS        # start small: a few spaces
KMS_JIRA_PROJECTS=PAY,OPS
```
2. In Keycloak (<http://localhost:8080>, realm **kms**), create the pilot users and set an attribute on each: `atlassian_id` = that person's Atlassian account ID. Create groups with **exactly the same names** as your Atlassian groups and add people to them. In production, Keycloak gets this from your company directory automatically.
3. Remove the pretend data and index the real content:
```bash
docker compose up -d
curl -X DELETE localhost:6333/collections/kms_confluence; curl -X DELETE localhost:6333/collections/kms_jira
docker compose exec agent-api kms index-all
```
Atlassian Cloud can't send change notices to a laptop, so the scheduler checks for changes every 5 minutes instead.

> **Be aware:** the connectors follow Atlassian's published Cloud API, but they've only been tested against the pretend Confluence/Jira. Expect a small amount of adjustment on first connection, which is the "Week 1 permissions audit" in the proposal. Confluence/Jira **Data Center** uses older API paths, so it needs a small adapter.

---

## Troubleshooting

| Symptom | Fix |
|---|---|
| `Cannot connect to the Docker daemon` | Docker Desktop isn't running. Open it and wait for the whale icon to stop animating. |
| Sign-in page doesn't load | Keycloak takes 30–60 s to start. Wait, then reload. Check with `docker compose logs keycloak`. |
| After sign-in: "The assistant is unavailable (401)" | Sign out and in again. If it persists, run `docker compose restart agent-api mcp-atlassian`. |
| Every answer says "I cannot answer this confidently" | The index is probably empty. Run Step 5 again. If you switched modes, run Step 7.4. |
| A service shows `Restarting` | Look at its logs: `docker compose logs <name>`. Most often Docker needs more memory (Step 1.3). |
| Port already in use (3000, 8080, 5432…) | Another app uses that port. Quit it, or change the left-hand number of that port in `docker-compose.yml`. |
| Real models are very slow | Expected on an Intel Mac. Use `qwen3:1.7b` for answers, or stay in demo mode for showing the flow. |
| Running out of disk | `docker system prune -a` removes unused images. `ollama rm <model>` removes a model. |

---

## Glossary

- **Agent:** a program that uses an AI model to decide what to do next, here which source to search and how to answer.
- **LangGraph:** the toolkit used to write the agents as a clear flowchart of steps.
- **MCP (Model Context Protocol):** a standard plug shape for connecting AI agents to tools and data. One plug per source system.
- **Embedding / vector:** a list of numbers capturing the *meaning* of a passage, so "deploy" can match "release to production".
- **Index (Qdrant):** the searchable catalogue of all passages and who may read them.
- **Token / token exchange:** a digital badge proving who you are. Exchange swaps your general badge for a source-specific one that's still in your name.
- **OPA policy:** rules written as code, such as "confidential pages need HR or SRE clearance".
- **Webhook:** an automatic "something changed" message from Confluence/Jira.
- **Groundedness:** how much of the answer is backed by the cited sources.
- **Audit log:** a permanent, append-only record of who asked what and what they were shown or denied.
