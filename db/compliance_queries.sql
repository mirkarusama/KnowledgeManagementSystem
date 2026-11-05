-- Example compliance questions, each answered by one SQL query.

-- 1) Which users read confidential content about "Project X" in the last 90 days?
SELECT DISTINCT user_id, doc_id, classification, ts
FROM audit.events
WHERE event_type = 'retrieval' AND decision = 'allow'
  AND classification IN ('confidential', 'highly_confidential', 'restricted')
  AND (details->>'query' ILIKE '%project x%' OR details->>'title' ILIKE '%project x%')
  AND ts > now() - interval '90 days'
ORDER BY ts DESC;

-- 2) Who was denied what, when, and why?
SELECT ts, user_id, source, doc_id, classification, reason FROM audit.denials ORDER BY ts DESC LIMIT 100;

-- 3) Questions the system could not answer confidently (candidates for new documentation).
SELECT ts, user_id, details->>'query' AS question
FROM audit.events WHERE event_type = 'answer' AND details->>'fallback' = 'true'
ORDER BY ts DESC;

-- 4) Usage per day.
SELECT date_trunc('day', ts) AS day, count(*) FILTER (WHERE event_type = 'query') AS questions,
       count(DISTINCT user_id) AS users
FROM audit.events GROUP BY 1 ORDER BY 1 DESC;
