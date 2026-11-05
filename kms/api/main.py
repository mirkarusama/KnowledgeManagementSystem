"""Agent API: serves the LangGraph orchestrator over HTTP with streaming (Server-Sent Events)."""
from __future__ import annotations

import json
import logging
import time
import uuid
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest
from psycopg_pool import AsyncConnectionPool
from pydantic import BaseModel, Field
from sse_starlette.sse import EventSourceResponse
from starlette.responses import Response

from kms import audit
from kms.agents.context import current_request_id, current_user
from kms.agents.orchestrator import build_graph
from kms.auth import AuthError, User, user_from_authorization
from kms.config import get_settings

log = logging.getLogger("kms.api")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")

QUESTIONS = Counter("kms_questions_total", "Questions received", ["outcome"])
LATENCY = Histogram("kms_answer_seconds", "Time to complete answer", buckets=(1, 2, 5, 10, 15, 30, 60, 120, 300))
FIRST_TOKEN = Histogram("kms_first_token_seconds", "Time to first streamed token", buckets=(0.5, 1, 2, 5, 10, 30, 60))
FEEDBACK = Counter("kms_feedback_total", "User feedback", ["score"])

STATE: dict = {}


@asynccontextmanager
async def lifespan(_app: FastAPI):
    s = get_settings()
    pool = AsyncConnectionPool(s.postgres_dsn, min_size=1, max_size=10, open=False,
                               kwargs={"autocommit": True, "prepare_threshold": 0})
    await pool.open()
    saver = AsyncPostgresSaver(pool)
    await saver.setup()  # creates the checkpoint tables on first start
    STATE["graph"] = build_graph(saver)
    STATE["pool"] = pool
    yield
    await pool.close()


app = FastAPI(title="KMS Agent API", version="0.1.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


def get_user(authorization: str | None = Header(default=None),
             x_debug_user: str | None = Header(default=None)) -> User:
    try:
        return user_from_authorization(authorization, x_debug_user)
    except AuthError as e:
        raise HTTPException(status_code=401, detail=str(e)) from e


class ChatRequest(BaseModel):
    question: str = Field(min_length=1, max_length=4000)
    thread_id: str | None = None


class FeedbackRequest(BaseModel):
    request_id: str
    score: int = Field(ge=0, le=1)
    comment: str | None = Field(default=None, max_length=2000)


def _callbacks() -> list:
    s = get_settings()
    if not (s.langfuse_host and s.langfuse_public_key):
        return []
    try:
        import os

        from langfuse.langchain import CallbackHandler

        os.environ.setdefault("LANGFUSE_HOST", s.langfuse_host)
        os.environ.setdefault("LANGFUSE_PUBLIC_KEY", s.langfuse_public_key)
        os.environ.setdefault("LANGFUSE_SECRET_KEY", s.langfuse_secret_key)
        return [CallbackHandler()]
    except Exception:  # noqa: BLE001 - observability must never break answering
        log.warning("Langfuse not available; continuing without tracing")
        return []


NODE_LABELS = {
    "load_user_context": "Checking who you are",
    "classify_intent": "Understanding the question",
    "plan": "Choosing where to look",
    "domain_agent": "Searching and confirming access",
    "merge_and_rerank_evidence": "Ranking the evidence",
    "synthesize": "Writing the answer",
    "groundedness_check": "Fact-checking against sources",
}


@app.post("/api/chat")
async def chat(req: ChatRequest, request: Request, user: User = Depends(get_user)):
    graph = STATE["graph"]
    request_id = str(uuid.uuid4())
    # Thread ids are namespaced by user so one person can never resume another's conversation.
    thread_id = f"{user.username}:{req.thread_id or uuid.uuid4()}"
    config = {"configurable": {"thread_id": thread_id}, "callbacks": _callbacks(),
              "metadata": {"user": user.username, "request_id": request_id}, "run_name": "km_question"}

    async def events():
        user_tok = current_user.set(user)
        rid_tok = current_request_id.set(request_id)
        t0, first = time.time(), None
        try:
            yield {"event": "meta", "data": json.dumps({"request_id": request_id,
                                                        "thread_id": thread_id.split(":", 1)[1]})}
            async for mode, chunk in graph.astream({"question": req.question}, config,
                                                   stream_mode=["updates", "messages"]):
                if await request.is_disconnected():
                    return
                if mode == "messages":
                    msg, meta = chunk
                    if meta.get("langgraph_node") == "synthesize" and getattr(msg, "content", ""):
                        if first is None:
                            first = time.time() - t0
                            FIRST_TOKEN.observe(first)
                        yield {"event": "token", "data": json.dumps({"text": msg.content})}
                elif mode == "updates":
                    for node in chunk:
                        if node in NODE_LABELS:
                            yield {"event": "status", "data": json.dumps({"step": node, "label": NODE_LABELS[node]})}
            final = (await graph.aget_state(config)).values
            LATENCY.observe(time.time() - t0)
            QUESTIONS.labels("fallback" if final.get("fallback") else "answered").inc()
            yield {"event": "final", "data": json.dumps({
                "request_id": request_id, "answer": final.get("answer", ""), "citations": final.get("citations", []),
                "confidence": final.get("confidence", "Low"), "groundedness": final.get("groundedness", 0.0),
                "fallback": bool(final.get("fallback")), "sources_searched": final.get("sources", []),
                "errors": final.get("errors", []), "seconds": round(time.time() - t0, 2)})}
        except Exception as e:  # noqa: BLE001
            log.exception("chat failed")
            QUESTIONS.labels("error").inc()
            yield {"event": "error", "data": json.dumps({"message": f"Something went wrong: {type(e).__name__}"})}
        finally:
            current_user.reset(user_tok)
            current_request_id.reset(rid_tok)

    return EventSourceResponse(events())


@app.post("/api/feedback")
async def feedback(req: FeedbackRequest, user: User = Depends(get_user)):
    FEEDBACK.labels(str(req.score)).inc()
    async with STATE["pool"].connection() as conn:
        await conn.execute("INSERT INTO feedback (request_id, user_id, score, comment) VALUES (%s,%s,%s,%s)",
                           (req.request_id, user.username, req.score, req.comment))
    await audit.record("feedback", request_id=req.request_id, user=user,
                       details={"score": req.score, "comment": req.comment})
    return {"ok": True}


@app.get("/api/me")
async def me(user: User = Depends(get_user)):
    return {"username": user.username, "groups": user.groups, "atlassian_id": user.atlassian_id}


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/metrics")
async def metrics():
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)
