"""Golden-set evaluation - the quality gate that runs in CI and weekly.

    python eval/run_eval.py                       # built-in metrics (no extra installs)
    python eval/run_eval.py --ragas               # + RAGAS faithfulness / answer relevancy (pip install .[eval])

Built-in metrics:
  access_leaks       answers citing a document the persona must not see      (must be 0)
  retrieval_hit      expected document among the citations                   (target >= 0.8)
  fallback_correct   unanswerable questions got the honest fallback          (target 1.0)
  groundedness       mean groundedness of non-fallback answers               (target >= 0.95 with real models)
Exit code is non-zero if a gate fails, which blocks the deploy in CI.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from kms.mock_atlassian.personas import debug_header  # noqa: E402

DOC_ID_FROM_URL = [("/pages/", "confluence:"), ("/browse/", "jira:")]


def doc_id(url: str) -> str:
    for marker, prefix in DOC_ID_FROM_URL:
        if marker in url:
            return prefix + url.rsplit(marker, 1)[1].split("?")[0].strip("/")
    return url


def ask(api: str, persona: str, question: str) -> dict:
    with httpx.stream("POST", f"{api}/api/chat", json={"question": question}, timeout=600,
                      headers={"Authorization": f"Bearer debug:{debug_header(persona)}"}) as r:
        event = None
        for line in r.iter_lines():
            if line.startswith("event:"):
                event = line[6:].strip()
            elif line.startswith("data:") and event == "final":
                return json.loads(line[5:])
            elif line.startswith("data:") and event == "error":
                raise RuntimeError(line)
    raise RuntimeError("no final event")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--api", default="http://localhost:8088")
    p.add_argument("--golden", default=str(Path(__file__).with_name("golden.jsonl")))
    p.add_argument("--min-hit", type=float, default=0.8)
    p.add_argument("--min-groundedness", type=float, default=0.6)
    p.add_argument("--ragas", action="store_true")
    p.add_argument("--out", default="eval/report.json")
    args = p.parse_args()

    rows = [json.loads(line) for line in Path(args.golden).read_text().splitlines() if line.strip()]
    results, leaks, hits, answerable, fb_ok, unanswerable, ground = [], 0, 0, 0, 0, 0, []
    for g in rows:
        res = ask(args.api, g["persona"], g["question"])
        # Only fully answered responses count as "showing" a document; fallback suggestions are also checked.
        cited = {doc_id(c["url"]) for c in res["citations"]}
        leak = cited & set(g.get("must_not", []))
        leaks += bool(leak)
        if g["expected_docs"]:
            answerable += 1
            hit = bool(cited & set(g["expected_docs"])) and not res["fallback"]
            hits += hit
            if not res["fallback"]:
                ground.append(res["groundedness"])
        else:
            unanswerable += 1
            fb_ok += res["fallback"]
        results.append({"id": g["id"], "persona": g["persona"], "question": g["question"], "answer": res["answer"],
                        "cited": sorted(cited), "fallback": res["fallback"], "groundedness": res["groundedness"],
                        "leak": sorted(leak), "reference": g["reference"],
                        "contexts": [c["snippet"] for c in res["citations"]]})
        print(f"{g['id']} {g['persona']:<6} fallback={res['fallback']!s:<5} cited={sorted(cited)} {'LEAK ' + str(leak) if leak else ''}")

    summary = {
        "access_leaks": leaks,
        "retrieval_hit": round(hits / answerable, 3) if answerable else None,
        "fallback_correct": round(fb_ok / unanswerable, 3) if unanswerable else None,
        "groundedness": round(sum(ground) / len(ground), 3) if ground else None,
    }
    if args.ragas:
        summary["ragas"] = run_ragas(results)
    Path(args.out).write_text(json.dumps({"summary": summary, "results": results}, indent=2))
    print(json.dumps(summary, indent=2))
    failed = leaks > 0 or (summary["retrieval_hit"] or 0) < args.min_hit or \
        (summary["fallback_correct"] is not None and summary["fallback_correct"] < 1.0) or \
        (summary["groundedness"] or 0) < args.min_groundedness
    sys.exit(1 if failed else 0)


def run_ragas(results: list[dict]) -> dict:
    """RAGAS with the self-hosted judge model behind LiteLLM (no data leaves the environment)."""
    import os

    from datasets import Dataset
    from langchain_openai import ChatOpenAI, OpenAIEmbeddings
    from ragas import evaluate
    from ragas.metrics import answer_relevancy, faithfulness

    base = os.getenv("KMS_LLM_BASE_URL", "http://localhost:4000/v1")
    key = os.getenv("KMS_LLM_API_KEY", "sk-kms-dev")
    llm = ChatOpenAI(model="km-synth", base_url=base, api_key=key, temperature=0)
    emb = OpenAIEmbeddings(model="km-embed", base_url=base, api_key=key, check_embedding_ctx_length=False)
    answered = [r for r in results if not r["fallback"]]
    ds = Dataset.from_list([{"question": r["question"], "answer": r["answer"], "contexts": r["contexts"],
                             "ground_truth": r["reference"]} for r in answered])
    scores = evaluate(ds, metrics=[faithfulness, answer_relevancy], llm=llm, embeddings=emb)
    return {k: float(v) for k, v in scores._repr_dict.items()} if hasattr(scores, "_repr_dict") else str(scores)


if __name__ == "__main__":
    main()
