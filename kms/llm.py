"""Talks to the model gateway (LiteLLM). The app only uses logical model names
(km-synth, km-router, km-embed), so swapping a model is a gateway config change."""
from __future__ import annotations

import re

from langchain_openai import ChatOpenAI
from openai import AsyncOpenAI

from kms.config import get_settings

_THINK = re.compile(r"<think>.*?</think>", re.S)


def strip_thinking(text: str) -> str:
    return _THINK.sub("", text or "").strip()


def chat_model(role: str = "synth", streaming: bool = True) -> ChatOpenAI:
    s = get_settings()
    return ChatOpenAI(
        model=s.synth_model if role == "synth" else s.router_model,
        base_url=s.llm_base_url,
        api_key=s.llm_api_key,
        temperature=0,
        streaming=streaming,
        timeout=s.llm_timeout_s,
        max_retries=1,
    )


def with_suffix(text: str) -> str:
    suffix = get_settings().prompt_suffix
    return f"{text} {suffix}" if suffix else text


class Embedder:
    def __init__(self):
        s = get_settings()
        self.client = AsyncOpenAI(base_url=s.llm_base_url, api_key=s.llm_api_key, timeout=s.llm_timeout_s)
        self.model = s.embed_model

    async def embed(self, texts: list[str], batch: int = 16) -> list[list[float]]:
        out: list[list[float]] = []
        for i in range(0, len(texts), batch):
            r = await self.client.embeddings.create(model=self.model, input=texts[i:i + batch])
            out.extend(d.embedding for d in sorted(r.data, key=lambda d: d.index))
        return out
