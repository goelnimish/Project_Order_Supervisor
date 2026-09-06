# Product Requirements Document (PRD): Order Supervisor Platform

## 1. Executive Summary & Problem Context

In e-commerce and retail fulfillment, between 8% and 14% of orders hit non-standard operational exceptions: carrier scans stall, shipping addresses fail validation, warehouses report split-stock backorders, or payment authorizations expire before dispatch. 

Today, businesses handle these exceptions using two deeply flawed paradigms:

1. **Rigid Rule Engines (If-This-Then-That):** Legacy OMS/ERP systems rely on hardcoded event rules. They work well for happy paths, but brittle edge cases (e.g., an address updated while a carrier scan is pending during a weather hold) lead to false alarms, uncoordinated customer communications, and trapped shipments.
2. **Unconstrained Autonomous AI Agents:** Modern attempts to wrap LLMs around order operations often run into severe cost and reliability walls. Unbounded agent loops poll continuously, burn hundreds of dollars in API credits, suffer memory degradation over multi-day orders, and occasionally hallucinate dangerous business actions like unauthorized refunds or duplicate order dispatches.

### The Solution: Order Supervisor

**Order Supervisor** merges durable distributed workflow orchestration with bounded, cost-controlled AI reasoning:

* **Temporal owns the workflow lifecycle:** Each customer order is backed by a single durable Temporal workflow (`order-supervisor:{order_id}`) that can sleep for days, survive infrastructure restarts, and react deterministically to incoming event signals.
* **Lightweight wake policy owns cost:** 80%+ of routine events (e.g., `payment_confirmed`, `shipment_created`) are classified and logged without waking the main AI model, reducing operational LLM spend to near zero on happy paths.
* **The AI supervisor operates inside a strict sandbox:** When an important exception occurs (e.g., `shipment_delayed`, `payment_failed`), the AI evaluates the context, updates compact rolling memory, and proposes actions exclusively from a strictly validated 5-action allowlist.
* **Humans retain total steering authority:** Operators can inspect a real-time command console, inject guidance into a running workflow, pause automation, or interrupt execution for manual investigation.

---

## 2. Target Personas & Jobs To Be Done (JTBD)

### Persona 1: Alex — Fulfillment Operations Lead
* **Profile:** Oversees warehouse throughput and carrier SLA performance across 25,000 weekly shipments.
* **Pain Point:** Spends 3 hours each morning reviewing stalled carrier reports and coordinating with 3 different carrier reps via disjointed email threads.
* **JTBD:** *"When carrier scans stall or warehouses miss SLAs, I want an automated system to diagnose the holdup, alert the carrier team with tracking context, and notify the customer before they file a complaint—without requiring manual triage on every routine milestone."*

### Persona 2: Priya — Tier-2 Customer Care Escalations Specialist
* **Profile:** Handles high-value VIP accounts, escalated delivery disputes, and customer appeasement.
* **Pain Point:** Lack of visibility into automated backend actions. Once a customer calls, Priya cannot easily see what automated messages were sent or stop an automated system from miscommunicating.
* **JTBD:** *"When a high-value customer reaches out with a delivery constraint, I want to review the full timeline of events, inject live operational instructions directly into the active order supervisor (e.g., 'prioritize air courier over ground'), or immediately pause automation while I investigate."*

### Persona 3: Marcus — Enterprise Systems & Platform Architect
* **Profile:** Responsible for reliability, data integrity, and tech stack compliance.
* **Pain Point:** Fear of non-deterministic AI behavior corrupting the ERP, double-charging customers, or losing state during deployment rollouts.
* **JTBD:** *"When we integrate an AI supervisor into our commerce architecture, I want ironclad proof that workflow state is replay-safe, actions are idempotent across network retries, and the AI cannot unilaterally complete orders or execute unapproved actions."*

---

## 3. Product Principles

1. **Orchestration Owns Life, AI Owns Reasoning, Guardrails Own Execution:** Temporal controls workflow lifecycle and completion rules. The AI is an advisory cognitive subsystem. Business actions execute through validated, idempotent activities.
2. **Never Busy-Poll When You Can Sleep Durably:** Workflows must not spin up background CPU loops or continuous LLM calls. They sleep on durable Temporal timers and wake only when an external trigger or scheduled deadline arrives.
3. **Auditability Over Hidden Magic:** Operators must be able to inspect every incoming event, wake decision, LLM summary, memory update, and action dispatch. No private chain-of-thought or opaque black-box decisions.
4. **Idempotency Is Non-Negotiable:** Webhooks and carrier events duplicate frequently. Every event signal and business action must guarantee at-most-once execution side effects.

---

## 4. Functional Requirements (FR)

### FR-1: Durable Single-Workflow Lifecycle
* **Priority:** P0 (Must Have)
* **Description:** The system must provision exactly one durable Temporal workflow per order, bound to the deterministic ID pattern `order-supervisor:{order_id}`.
* **Acceptance Criteria:**
  * Starting an order creates an active workflow and persists an initial run record in PostgreSQL.
  * Attempting to start a second active run for the same `order_id` must be rejected with a 409 Conflict.
  * The workflow must survive worker crashes and system restarts without losing state, pending signals, or timers.

### FR-2: Signal-Driven Ingress & Event Deduplication
* **Priority:** P0 (Must Have)
* **Description:** External commerce triggers (payments, fulfillment, shipments, carrier alerts) must be delivered to running workflows via Temporal Signals.
* **Acceptance Criteria:**
  * The workflow accepts typed `OrderEvent` signals containing `event_id`, `event_type`, `occurred_at`, and `payload`.
  * If an event with an already-seen `event_id` is received, the workflow must record a deduplication audit entry and immediately drop the duplicate from processing.

### FR-3: Deterministic Wake Policy & Cost Classifier
* **Priority:** P0 (Must Have)
* **Description:** The system must classify incoming events into predictable operational tiers before determining whether to invoke the main AI supervisor.
* **Acceptance Criteria:**
  * **Routine Events** (`order_created`, `payment_confirmed`, `shipment_created`): Update internal order state projection, append an audited `supervisor_wake_suppressed` entry, and do not invoke the LLM.
  * **Important Events** (`payment_failed`, `shipment_delayed`, `refund_requested`, `customer_message_received`, `no_update_for_n_hours`, `unknown`): Wake the supervisor immediately.
  * **Terminal Events** (`delivered`): Trigger deterministic workflow completion sequence.

### FR-4: Bounded Cognitive Supervisor Contract
* **Priority:** P0 (Must Have)
* **Description:** When invoked, the AI supervisor (or deterministic rule provider) must accept bounded context and return a strictly validated `AgentDecision` schema.
* **Acceptance Criteria:**
  * Input context must be bounded: order context, supervisor instructions, rolling memory summary, retained operator instructions, and the 10 most recent activity entries.
  * Model response must validate against a strict Pydantic contract containing:
    * `decision_summary`: A concise 1-2 sentence operational rationale.
    * `urgency`: Low, medium, high, or critical.
    * `should_act`: Boolean indicator of whether actions are proposed.
    * `actions`: List of proposed actions matching the allowed schema.
    * `memory_update`: Updated compact memory object.
    * `next_wake_seconds`: Bounded integer (between 1 second and 24 hours).
  * If model output is invalid or times out, the system must log an audit failure, execute zero actions, and schedule a safe retry review.

### FR-5: Strict Five-Action Business Execution Allowlist
* **Priority:** P0 (Must Have)
* **Description:** The supervisor may only execute actions from an immutable five-action allowlist.
* **Actions:**
  1. `message_fulfillment_team`: Dispatches warehouse priority pick or restock inquiries.
  2. `message_payments_team`: Alerts fraud or payment operations on authorization failures.
  3. `message_logistics_team`: Contacts carriers regarding tracking delays or transit exceptions.
  4. `message_customer`: Sends proactive delay notifications or status updates.
  5. `create_internal_note`: Records operator notes or flags for human shift handoffs.
* **Acceptance Criteria:**
  * Actions are simulated locally but backed by persistent PostgreSQL activity records.
  * Every execution uses a deterministic composite idempotency key (`run_id:invocation:action_index`).
  * Workflow-owned semantic guard prevents duplicate actions for the same triggering event.

### FR-6: Compact Rolling Semantic Memory
* **Priority:** P0 (Must Have)
* **Description:** Workflows must maintain a compact, human-readable summary of order state rather than passing unbounded conversational transcripts to the LLM.
* **Acceptance Criteria:**
  * Memory is structured as: `order_state`, `important_facts`, `open_issues`, `actions_taken`, `active_constraints`, `next_review`.
  * Updated exclusively by the supervisor activity on each review cycle.
  * Rendered cleanly in the operator console without exposing raw JSON.

### FR-7: Operator Steering & Lifecycle Controls
* **Priority:** P0 (Must Have)
* **Description:** Operators must have full visibility and real-time control over running workflows via the web dashboard.
* **Acceptance Criteria:**
  * **Live Instructions:** Operators can submit steering instructions (e.g., *"Customer is VIP, waive expedited shipping fee"*). The instruction is retained and injected into all future supervisor reviews.
  * **Pause:** Freezes automated wake cycles while continuing to accept incoming signals.
  * **Interrupt:** Immediately blocks automation for urgent human investigation until an operator reviews and resumes.
  * **Resume:** Releases a paused or interrupted workflow back into active running/sleeping cycles.
  * **Graceful Terminate:** Allows an operator to manually close a run with a documented business reason, triggering final summary synthesis.

### FR-8: Deterministic Lifecycle Finalization & 4-Quadrant Synthesis
* **Priority:** P0 (Must Have)
* **Description:** Order completion must be controlled by explicit workflow rules, never by an unrestricted AI tool.
* **Acceptance Criteria:**
  * Completion triggers: Receipt of `delivered` signal or operator graceful termination.
  * An AI recommendation to complete is recorded as advisory only and cannot close the workflow.
  * On finalization, a dedicated activity synthesizes four distinct operational deliverables:
    1. **Final Summary:** Comprehensive narrative of the order lifecycle.
    2. **Important Actions Taken:** Key business interventions dispatched during the run.
    3. **Key Learnings:** Operational bottlenecks or carrier friction points identified.
    4. **Recommendations:** Preventative recommendations for future orders or vendor reviews.

---

## 5. Non-Functional Requirements (NFR)

| ID | Category | Requirement | Validation Criterion |
|---|---|---|---|
| **NFR-1** | **Determinism** | Temporal workflow code must be 100% deterministic. No direct wall-clock time, random IDs, filesystem, or database access inside workflow methods. | Replay tests pass; all nondeterministic operations execute exclusively within Temporal Activities. |
| **NFR-2** | **Idempotency** | Duplicate webhook signals or activity retries must never result in duplicate external messages or corrupt states. | Database uniqueness on `(run_id, activity_key)`; composite key verification. |
| **NFR-3** | **Cost & Latency** | Happy path orders must incur $0.00 in LLM costs. Exception evaluations must execute in <5 seconds using local SLMs (`qwen3:1.7b`). | Routine wake suppression verified; Ollama prompt context capped at 2,048 tokens. |
| **NFR-4** | **Fault Recovery** | If a worker process is terminated mid-execution, the workflow must resume from its last completed step upon worker restart. | Verified via Docker Compose container restart tests without state loss. |
| **NFR-5** | **Eventual Consistency** | The operational UI must reflect signal ingress and timeline updates within 1.5 seconds. | Next.js client polling cadence (1.5s active, 5s sleeping, stopped when terminal). |

---

## 6. Out of Scope (Explicit Guardrails)

To maintain focus and avoid architectural bloat in this POC, the following are explicitly deferred:

* **Production Commerce Integrations:** Real Shopify webhooks, SAP ERP connectors, or live carrier APIs (FedEx, UPS, DHL). All business actions remain simulated with persistent audit evidence.
* **Live Customer Messaging Channels:** No direct Twilio SMS, SendGrid email, or Zendesk ticket generation.
* **Vector Databases & RAG:** No embeddings, Pinecone, Milvus, or knowledge graph stores. Rolling semantic memory is sufficient for single-order lifecycles.
* **Multi-Tenant Authorization & RBAC:** Single-tenant operator environment; no OAuth2/SAML SSO or role-based permission tiers.
* **Autonomous Workflow Completion Tool:** The LLM is strictly prohibited from holding an autonomous `complete_workflow` execution tool.

---

## 7. Success Metrics & KPIs (The TPM Scorecard)

```text
+-----------------------------------+-----------------------------------+--------------------+
| Metric                            | Description                       | Target Threshold   |
+-----------------------------------+-----------------------------------+--------------------+
| Autonomous Resolution Rate        | % of order exceptions handled     | >= 85%             |
|                                   | without manual human intervention |                    |
+-----------------------------------+-----------------------------------+--------------------+
| Routine Wake Suppression Ratio    | % of routine signals handled      | >= 75%             |
|                                   | without invoking the LLM          |                    |
+-----------------------------------+-----------------------------------+--------------------+
| Mean Time to Remediation (MTTR)   | Average duration from exception   | <= 10 minutes      |
|                                   | signal to business action dispatch| (vs 4 hours manual)|
+-----------------------------------+-----------------------------------+--------------------+
| Zero-Duplicate Guarantee          | Duplicate actions executed across | 0.0%               |
|                                   | retries or repeated webhook scans |                    |
+-----------------------------------+-----------------------------------+--------------------+
| Inference Cost Per Order          | Average compute/API cost per      | <= $0.01 / order   |
|                                   | supervised order lifecycle        |                    |
+-----------------------------------+-----------------------------------+--------------------+
```
