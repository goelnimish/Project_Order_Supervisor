# Order Supervisor architecture

## Overview

Order Supervisor is a local proof of concept that keeps one durable Temporal
Workflow alive for one order. Operators configure a supervisor, start a run,
send order events and instructions, and inspect the resulting state and audit
history in the browser. The system is intentionally small: business actions are
simulated and PostgreSQL is the operational read model, not a replacement for
Temporal's workflow history.

The request path is:

```text
Next.js operator console -> FastAPI control plane -> Temporal Workflow
                                                    -> Supervisor Activity
                                                       (deterministic rules or Ollama/qwen3:1.7b)
                                                    -> Business-action Activities
                                                    -> PostgreSQL projection/audit log
```

## Main components

- **Next.js App Router:** renders supervisor setup, run lists, run detail, timeline,
  memory, controls, final output, and analytics. It polls the API for the small
  amount of eventual consistency between a Signal and its PostgreSQL projection.
- **FastAPI:** validates HTTP requests, persists supervisor/run records, starts
  Workflows, sends Signals, and serves the PostgreSQL-backed read model.
- **Temporal Worker:** registers `OrderSupervisorWorkflow` and its Activities on
  the `order-supervisor` task queue. Workflow code owns state, timers, Signals,
  and lifecycle decisions.
- **Supervisor Provider:** exposes one typed decision contract. The deterministic
  provider is useful for repeatable development; the Ollama provider calls the
  local `qwen3:1.7b` model from an Activity and validates its structured output.
- **Business Actions:** an explicit allowlisted dispatcher implements exactly
  `message_fulfillment_team`, `message_payments_team`,
  `message_logistics_team`, `message_customer`, and `create_internal_note`.
  Each action is a local simulation with a persistent evidence row.
- **PostgreSQL:** stores supervisor configurations, run snapshots, final output,
  and the unified activity timeline used by the UI and analytics endpoints.
- **Ollama:** optional local model runtime. It is never called directly from
  Workflow code; the network request runs inside the supervisor Activity.

## Workflow lifecycle

```text
start
  -> receive a Signal or timer
  -> classify the trigger (routine, important, or terminal)
  -> invoke the supervisor when policy requires it
  -> validate the typed decision and configured action allowlist
  -> execute a retry-safe business action when proposed
  -> persist the transition, memory, and run snapshot
  -> schedule a durable timer and sleep
  -> repeat until a Workflow-owned completion rule applies
```

`payment_confirmed` is a routine event and normally records a suppressed wake.
`shipment_delayed` is important and wakes the supervisor. `delivered` is a
terminal order event: it authorizes completion in Workflow code, after which a
final-output Activity generates the four user-facing sections. Manual graceful
termination is the other terminal path. An AI completion recommendation is
recorded as advice only and cannot close the Workflow.

## Why Temporal

Temporal supplies durable timers, Signal delivery, replay-safe orchestration,
Workflow Queries, and a clear retry boundary for Activities. A sleeping run is
not a Python polling loop: the Workflow waits on a durable timer or Signal and
resumes after a process restart. Pause and interrupt block automated inference
while retaining incoming Signals; resume releases the queued work.

## AI boundary

The LLM proposes a concise decision, optional allowlisted actions, a compact
memory update, and a bounded next-review delay. Pydantic schemas validate the
response. The application checks permissions and action arguments, while
Workflow-owned rules control lifecycle completion. Activities perform all
nondeterministic work: model calls, PostgreSQL writes, simulated actions, and
final-report generation. Private chain-of-thought is not stored or displayed.

## Memory and timeline

Each run keeps a bounded structured memory containing order state, important
facts, open issues, actions taken, active constraints, and the next review. The
provider receives that summary plus a bounded recent-activity window and live
instructions. The full important-event, decision, action, instruction, control,
sleep, and completion history is stored separately in PostgreSQL as the
auditable timeline.

## Idempotency

Technical retry idempotency uses deterministic keys scoped to the Workflow, run,
supervisor invocation, and action index. PostgreSQL uniqueness makes a retried
Activity return the original evidence instead of creating a second message.
Semantic protection also prevents the same successful action from being
re-issued for the same wake-worthy external event while allowing a distinct
event to trigger a new action. Incoming events have stable IDs and duplicates
are ignored by the Workflow.

## Persistence model

Temporal remains authoritative for live orchestration. Bounded persistence
Activities append an activity row and update the run snapshot in one PostgreSQL
transaction. The API and UI read this projection, so a Signal acknowledgement can
appear before the corresponding row is visible. The UI polls briefly and shows
the live Workflow Query state when available.

## Failure handling

Supervisor, action, persistence, and final-output Activities use explicit
timeouts and bounded retries. Invalid or unavailable supervisor output is
rejected safely, executes no unvalidated action, and schedules another review.
If final-output generation still fails, a deterministic report is persisted so a
Workflow-owned terminal result is not lost. Persistence exhaustion keeps the
transition queued for durable recovery. These are architectural safeguards, not
unbounded cloud infrastructure guarantees.
