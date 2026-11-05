"""Command line: `kms <command>`. Run `kms --help`."""
from __future__ import annotations

import argparse
import asyncio
import json
import logging


def main(argv=None):
    p = argparse.ArgumentParser(prog="kms", description="Agentic KM pilot tools")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("index-all", help="first full load of Confluence + Jira into the index")
    a = sub.add_parser("acl-sync", help="re-read permissions now (normally every 15 min)")
    a.add_argument("--force", action="store_true", help="ignore the 5%% removal safety limit")
    r = sub.add_parser("reconcile", help="compare source vs index and fix drift (normally nightly)")
    r.add_argument("--force", action="store_true")
    sub.add_parser("poll", help="collect Atlassian Cloud changes now (normally every 5 min)")
    sub.add_parser("archive-audit", help="copy audit rows to write-once storage")
    sub.add_parser("worker", help="run an ingestion worker")
    sub.add_parser("scheduler", help="run the dev scheduler (Airflow replaces this in production)")
    ask = sub.add_parser("ask", help="ask a question from the terminal (dev mode identities)")
    ask.add_argument("question")
    ask.add_argument("--as", dest="who", default="alice", help="alice | bob | carol | dave | erin")
    ask.add_argument("--api", default="http://localhost:8088")
    args = p.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")

    from kms.ingest import jobs

    if args.cmd == "index-all":
        print(json.dumps(asyncio.run(jobs.full_index()), indent=2))
    elif args.cmd == "acl-sync":
        print(json.dumps(asyncio.run(jobs.acl_sync(force=args.force)), indent=2))
    elif args.cmd == "reconcile":
        print(json.dumps(asyncio.run(jobs.reconcile(force=args.force)), indent=2))
    elif args.cmd == "poll":
        print(json.dumps(asyncio.run(jobs.poll_changes()), indent=2))
    elif args.cmd == "archive-audit":
        print(json.dumps(asyncio.run(jobs.archive_audit()), indent=2))
    elif args.cmd == "worker":
        from kms.ingest.worker import run_worker

        asyncio.run(run_worker())
    elif args.cmd == "scheduler":
        from kms.ingest.scheduler import run_scheduler

        asyncio.run(run_scheduler())
    elif args.cmd == "ask":
        _ask(args)


def _ask(args):
    import httpx

    from kms.mock_atlassian.personas import debug_header

    with httpx.stream("POST", f"{args.api}/api/chat", json={"question": args.question},
                      headers={"Authorization": f"Bearer debug:{debug_header(args.who)}"}, timeout=600) as resp:
        event = None
        for line in resp.iter_lines():
            if line.startswith("event:"):
                event = line.split(":", 1)[1].strip()
            elif line.startswith("data:") and event == "status":
                print("  ..", json.loads(line[5:])["label"])
            elif line.startswith("data:") and event == "final":
                d = json.loads(line[5:])
                print(f"\n{d['answer']}\n\nconfidence={d['confidence']} groundedness={d['groundedness']}")
                for c in d["citations"]:
                    print(f"  [{c['id']}] {c['title']} {c['url']}" + ("  (older than 90 days)" if c["stale"] else ""))
            elif line.startswith("data:") and event == "error":
                print("ERROR", line[5:])


if __name__ == "__main__":
    main()
