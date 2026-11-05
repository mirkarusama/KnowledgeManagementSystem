"use client";
// Connects the assistant-ui chat window to the Agent API's streaming endpoint (Server-Sent Events).
import type { ChatModelAdapter } from "@assistant-ui/react";
import { currentUser, getConfig } from "./auth";

export type Citation = {
  id: string; title: string; url: string; source: string; heading: string;
  updated_at?: string; stale: boolean; classification?: string; snippet: string;
};
export type AnswerMeta = {
  requestId: string; citations: Citation[]; confidence: string; groundedness: number;
  fallback: boolean; seconds: number; step?: string;
};

let threadId: string | null = null;
export function resetThread() { threadId = null; }

async function* sse(body: ReadableStream<Uint8Array>) {
  const reader = body.getReader();
  const decoder = new TextDecoder();
  let buf = "";
  while (true) {
    const { value, done } = await reader.read();
    if (done) return;
    buf += decoder.decode(value, { stream: true });
    let idx;
    while ((idx = buf.search(/\r?\n\r?\n/)) >= 0) {
      const raw = buf.slice(0, idx);
      buf = buf.slice(idx).replace(/^\r?\n\r?\n/, "");
      let event = "message";
      const data: string[] = [];
      for (const line of raw.split(/\r?\n/)) {
        if (line.startsWith("event:")) event = line.slice(6).trim();
        else if (line.startsWith("data:")) data.push(line.slice(5).trimStart());
      }
      if (data.length) yield { event, data: JSON.parse(data.join("\n")) };
    }
  }
}

export const kmsAdapter: ChatModelAdapter = {
  async *run({ messages, abortSignal }) {
    const last = messages[messages.length - 1];
    const question = last.content.map((p) => (p.type === "text" ? p.text : "")).join(" ");
    const [cfg, user] = await Promise.all([getConfig(), currentUser()]);
    if (!user) throw new Error("Your session expired. Please sign in again.");

    const resp = await fetch(`${cfg.apiUrl}/api/chat`, {
      method: "POST",
      headers: { "Content-Type": "application/json", Authorization: `Bearer ${user.access_token}` },
      body: JSON.stringify({ question, thread_id: threadId }),
      signal: abortSignal,
    });
    if (!resp.ok || !resp.body) throw new Error(`The assistant is unavailable (${resp.status}).`);

    let draft = "";
    let step = "Starting";
    for await (const { event, data } of sse(resp.body)) {
      if (event === "meta") {
        threadId = data.thread_id;
      } else if (event === "status") {
        step = data.label;
        if (!draft) yield { content: [{ type: "text", text: "" }], metadata: { custom: { step } } };
      } else if (event === "token") {
        draft += data.text;
        yield { content: [{ type: "text", text: draft }], metadata: { custom: { step: "Writing the answer" } } };
      } else if (event === "final") {
        const meta: AnswerMeta = {
          requestId: data.request_id, citations: data.citations, confidence: data.confidence,
          groundedness: data.groundedness, fallback: data.fallback, seconds: data.seconds,
        };
        // Turn [S1] markers into clickable links to the cited page or ticket.
        const byId = new Map(meta.citations.map((c) => [c.id, c.url]));
        const text = (data.answer as string).replace(/\[(S\d+)\]/g, (m: string, id: string) =>
          byId.get(id) ? `[[${id.slice(1)}]](${byId.get(id)})` : m);
        yield { content: [{ type: "text", text }], metadata: { custom: meta } };
      } else if (event === "error") {
        throw new Error(data.message);
      }
    }
  },
};

export async function sendFeedback(requestId: string, score: 0 | 1, comment?: string) {
  const [cfg, user] = await Promise.all([getConfig(), currentUser()]);
  if (!user) return;
  await fetch(`${cfg.apiUrl}/api/feedback`, {
    method: "POST",
    headers: { "Content-Type": "application/json", Authorization: `Bearer ${user.access_token}` },
    body: JSON.stringify({ request_id: requestId, score, comment }),
  });
}
