"""A tiny stand-in for a real language model, speaking the OpenAI API.

Why it exists: so the whole platform can be started and tested with NO model
downloads at all (CI, slow laptops, first-time setup). It is not intelligent:
  * embeddings  = hashed word counts (finds passages sharing words with the question)
  * chat        = picks the source sentences that best match the question and cites them
Switch to real models (Ollama / vLLM) by changing litellm/config.yaml - no code changes.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
import time
import uuid

from fastapi import FastAPI, Request
from sse_starlette.sse import EventSourceResponse

from kms.index.sparse import tokenize

app = FastAPI(title="Offline demo LLM")
DIM = 1024


def _embed(text: str) -> list[float]:
    v = [0.0] * DIM
    for t in tokenize(text):
        h = int(hashlib.md5(t.encode()).hexdigest(), 16)
        v[h % DIM] += 1.0 if (h >> 12) % 2 else -1.0
    n = math.sqrt(sum(x * x for x in v)) or 1.0
    return [x / n for x in v]


@app.post("/v1/embeddings")
async def embeddings(req: Request):
    body = await req.json()
    inputs = body["input"] if isinstance(body["input"], list) else [body["input"]]
    return {"object": "list", "model": body.get("model", "demo-embed"),
            "data": [{"object": "embedding", "index": i, "embedding": _embed(t)} for i, t in enumerate(inputs)],
            "usage": {"prompt_tokens": 0, "total_tokens": 0}}


_SRC = re.compile(r"\[S(\d+)\]([^\n]*)\n(.*?)(?=\n\n\[S\d+\]|\n\nQuestion:)", re.S)


def _answer(messages: list[dict]) -> str:
    text = "\n".join(m.get("content", "") if isinstance(m.get("content"), str) else "" for m in messages)
    if "Route a question" in text:
        return json.dumps({"sources": [], "query": text.rsplit("Question:", 1)[-1].split("\n")[0].strip()})
    q = text.rsplit("Question:", 1)[-1]
    text = text.split("Sources:", 1)[-1] + "\n\nQuestion:"
    qwords = set(tokenize(q))
    scored = []
    for m in _SRC.finditer(text):
        sid, header, body = m.group(1), m.group(2), m.group(3)
        hwords = set(tokenize(header.split("(")[0]))
        for sent in re.split(r"(?<=[.!?])\s+|\n+", body):
            sent = sent.strip(" -")
            words = set(tokenize(sent))
            if len(words) < 4:
                continue
            overlap = len((words | hwords) & qwords)
            if overlap >= max(1, math.ceil(len(qwords) * 0.4)):
                scored.append((overlap / (1 + math.log(len(words))), sid, sent))
    if not scored:
        return "INSUFFICIENT"
    scored.sort(reverse=True)
    best = scored[0][0]
    picked = [s for s in scored if s[0] >= best * 0.5][:3]
    return " ".join(f"{sent.rstrip('.')}. [S{sid}]" for _, sid, sent in picked)


@app.post("/v1/chat/completions")
async def chat(req: Request):
    body = await req.json()
    answer = _answer(body.get("messages", []))
    cid, model = f"chatcmpl-{uuid.uuid4().hex[:12]}", body.get("model", "demo")
    if not body.get("stream"):
        return {"id": cid, "object": "chat.completion", "created": int(time.time()), "model": model,
                "choices": [{"index": 0, "message": {"role": "assistant", "content": answer}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}}

    async def gen():
        for word in re.findall(r"\S+\s*", answer):
            yield {"data": json.dumps({"id": cid, "object": "chat.completion.chunk", "created": int(time.time()),
                                       "model": model, "choices": [{"index": 0, "delta": {"content": word},
                                                                    "finish_reason": None}]})}
        yield {"data": json.dumps({"id": cid, "object": "chat.completion.chunk", "created": int(time.time()),
                                   "model": model, "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]})}
        yield {"data": "[DONE]"}

    return EventSourceResponse(gen())


@app.get("/v1/models")
async def models():
    return {"object": "list", "data": [{"id": m, "object": "model"} for m in ("demo-chat", "demo-embed")]}


@app.get("/health")
async def health():
    return {"status": "ok"}
