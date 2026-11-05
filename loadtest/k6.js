// Load test (Week 6): `k6 run -e API=http://localhost:8088 loadtest/k6.js`
// Simulates pilot concurrency; checks p95 time-to-complete-answer.
import http from "k6/http";
import { check } from "k6";

export const options = {
  scenarios: { pilot: { executor: "ramping-vus", stages: [
    { duration: "1m", target: 10 }, { duration: "3m", target: 25 }, { duration: "1m", target: 0 }] } },
  thresholds: { http_req_duration: ["p(95)<15000"], checks: ["rate>0.99"] },
};

const USERS = ["alice;all-staff,engineering,sre;acc-alice", "bob;all-staff,engineering;acc-bob",
  "carol;all-staff,hr;acc-carol", "erin;all-staff,contractors;acc-erin"];
const QUESTIONS = ["How do I deploy the payments service?", "What caused the VPN outage?",
  "How do I submit expenses?", "When do on-call shifts start?", "How was PAY-142 fixed?"];

export default function () {
  const user = USERS[Math.floor(Math.random() * USERS.length)];
  const q = QUESTIONS[Math.floor(Math.random() * QUESTIONS.length)];
  const res = http.post(`${__ENV.API}/api/chat`, JSON.stringify({ question: q }), {
    headers: { "Content-Type": "application/json", Authorization: `Bearer debug:${user}` }, timeout: "120s" });
  check(res, { "status 200": (r) => r.status === 200, "has final answer": (r) => r.body.includes("event: final") });
}
