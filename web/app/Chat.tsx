"use client";
import {
  AssistantRuntimeProvider, ComposerPrimitive, MessagePrimitive, ThreadPrimitive, useAuiState, useLocalRuntime,
} from "@assistant-ui/react";
import { MarkdownTextPrimitive } from "@assistant-ui/react-markdown";
import type { User } from "oidc-client-ts";
import { useState } from "react";
import { userManager } from "@/lib/auth";
import { kmsAdapter, resetThread, sendFeedback, type AnswerMeta, type Citation } from "@/lib/chatAdapter";

const EXAMPLES = [
  "How do I deploy the payments service?",
  "What caused the VPN outage?",
  "How do I get production database access?",
  "How many weeks of parental leave do we get?",
];

export default function Chat({ user }: { user: User }) {
  const runtime = useLocalRuntime(kmsAdapter);
  const name = (user.profile.preferred_username as string) ?? "you";
  const groups = ((user.profile as Record<string, unknown>).groups as string[] | undefined) ?? [];
  return (
    <AssistantRuntimeProvider runtime={runtime}>
      <div className="app">
        <header>
          <strong>Knowledge Assistant</strong>
          <span className="who" title={`Groups: ${groups.join(", ")}`}>Signed in as {name}</span>
          <button onClick={() => { resetThread(); window.location.reload(); }}>New chat</button>
          <button onClick={async () => (await userManager()).signoutRedirect()}>Sign out</button>
        </header>
        <ThreadPrimitive.Root className="thread">
          <ThreadPrimitive.Viewport className="viewport">
            <ThreadPrimitive.Empty>
              <div className="welcome">
                <h2>Hi {name}, what do you need to know?</h2>
                <p>Answers come from Confluence and Jira, with a link for every fact.</p>
                <div className="examples">
                  {EXAMPLES.map((q) => (
                    <ThreadPrimitive.Suggestion key={q} prompt={q} send className="example">{q}</ThreadPrimitive.Suggestion>
                  ))}
                </div>
              </div>
            </ThreadPrimitive.Empty>
            <ThreadPrimitive.Messages components={{ UserMessage, AssistantMessage }} />
          </ThreadPrimitive.Viewport>
          <ComposerPrimitive.Root className="composer">
            <ComposerPrimitive.Input className="input" placeholder="Ask a question…" autoFocus />
            <ComposerPrimitive.Send className="primary">Ask</ComposerPrimitive.Send>
          </ComposerPrimitive.Root>
        </ThreadPrimitive.Root>
      </div>
    </AssistantRuntimeProvider>
  );
}

function UserMessage() {
  return (
    <MessagePrimitive.Root className="msg user">
      <MessagePrimitive.Parts />
    </MessagePrimitive.Root>
  );
}

function MarkdownText() {
  return <MarkdownTextPrimitive className="md" />;
}

function AssistantMessage() {
  const custom = useAuiState((s) => s.message.metadata?.custom) as Partial<AnswerMeta> | undefined;
  const running = useAuiState((s) => s.message.status?.type === "running");
  const hasText = useAuiState((s) => s.message.content.some((p) => p.type === "text" && p.text.length > 0));
  return (
    <MessagePrimitive.Root className="msg assistant">
      {running && !hasText && <div className="step">⏳ {custom?.step ?? "Working"}…</div>}
      <MessagePrimitive.Parts components={{ Text: MarkdownText }} />
      <MessagePrimitive.Error>
        <div className="error">Sorry, something went wrong. Please try again.</div>
      </MessagePrimitive.Error>
      {custom?.requestId && <AnswerFooter meta={custom as AnswerMeta} />}
    </MessagePrimitive.Root>
  );
}

function AnswerFooter({ meta }: { meta: AnswerMeta }) {
  const [voted, setVoted] = useState<0 | 1 | null>(null);
  return (
    <div className="footer">
      <div className="badges">
        <span className={`conf conf-${meta.confidence.toLowerCase()}`} title="How well the answer is backed by the sources">
          Confidence: {meta.confidence}
        </span>
        <span className="muted">{meta.seconds}s</span>
      </div>
      {meta.citations.length > 0 && (
        <ol className="citations">
          {meta.citations.map((c) => <CitationCard key={c.id} c={c} />)}
        </ol>
      )}
      <div className="feedback">
        {voted === null ? (
          <>
            <span className="muted">Was this helpful?</span>
            <button onClick={() => { setVoted(1); sendFeedback(meta.requestId, 1); }}>👍</button>
            <button onClick={() => {
              const why = window.prompt("What was wrong or missing? (optional)") ?? undefined;
              setVoted(0); sendFeedback(meta.requestId, 0, why);
            }}>👎</button>
          </>
        ) : <span className="muted">Thanks for the feedback.</span>}
      </div>
    </div>
  );
}

function CitationCard({ c }: { c: Citation }) {
  const icon = c.source === "jira" ? "🎫" : "📄";
  return (
    <li>
      <a href={c.url} target="_blank" rel="noreferrer">{icon} [{c.id.slice(1)}] {c.title}{c.heading ? ` › ${c.heading}` : ""}</a>
      <div className="meta">
        {c.updated_at && <span>Updated {c.updated_at.slice(0, 10)}</span>}
        {c.stale && <span className="stale" title="This source is more than 90 days old">Older than 90 days: verify before acting</span>}
        {c.classification && c.classification !== "internal" && <span className="label">{c.classification.replace("_", " ")}</span>}
      </div>
    </li>
  );
}
