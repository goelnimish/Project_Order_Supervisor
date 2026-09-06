# Order Supervisor

> **An auditable, durable AI supervisor for long-running order lifecycles.**  
> Built with **Temporal**, **FastAPI**, **PostgreSQL**, **Next.js**, and local LLMs (**Ollama / Qwen**).

---

## What is Order Supervisor?

**Order Supervisor** is a production-style proof of concept demonstrating how autonomous AI agents can safely and durably supervise real-world business processes from creation to completion.

In traditional systems, order exception handling either requires rigid rule engines that break on unexpected edge cases or expensive, fragile LLM polling loops that hallucinate and burn API credits. **Order Supervisor** solves this by pairing durable workflow orchestration with bounded AI supervision:

* **One Durable Workflow Per Order:** Each order is managed by its own independent, long-running Temporal workflow (`order-supervisor:{order_id}`). The workflow persists across service restarts, deployments, or network interruptions, sleeping durably between events without busy polling.
* **Smart Wake Policy & Cost Efficiency:** Incoming events (e.g. payment confirmations, warehouse milestones, carrier delay alerts) are delivered via Temporal Signals. A lightweight, predictable wake policy classifies each trigger: routine events are silently recorded in the PostgreSQL audit log without invoking the LLM, while critical exceptions and scheduled reviews wake the main AI supervisor.
* **Strict Execution Guardrails:** The AI supervisor operates within strict architectural boundaries. It evaluates order context, updates compact rolling memory, and proposes actions exclusively from a validated allowlist (`message_fulfillment_team`, `message_payments_team`, `message_logistics_team`, `message_customer`, `create_internal_note`). Actions execute via retry-safe, idempotent Temporal Activities—the LLM is never given unchecked execution authority or workflow-completion tools.
* **Human-in-the-Loop Operator Console:** Operators can observe active and historical runs via a real-time Next.js dashboard, inspect the PostgreSQL audit timeline and compact memory, inject external events, provide live steering instructions to running workflows, pause/resume execution, or interrupt for manual review.
* **Deterministic Completion & Synthesis:** When terminal conditions are met (e.g. an order is delivered), the workflow deterministically triggers finalization—generating an auditable final summary, key learnings, and actionable post-order recommendations.
* **100% Local & Self-Contained:** Built to run locally with zero external API dependencies. It supports local inference via Ollama (`qwen3:1.7b`) alongside a deterministic rule-based fallback, Docker Compose for PostgreSQL and Temporal, and local simulated business action handlers.

---

## Current scope

- Functional Next.js App Router operator console with TypeScript and Tailwind CSS
- FastAPI backend managed by `uv`
- PostgreSQL 16 with async SQLAlchemy, asyncpg, and Alembic head
  `20260904_02`
- File-backed local Temporal development server and Web UI
- One `OrderSupervisorWorkflow` per order using
  `order-supervisor:{order_id}`
- Rule-based routine-event suppression and important-event escalation
- Deterministic and direct-HTTP Ollama supervisor providers sharing one typed
  decision contract
- Five allowlisted, simulated business actions with persistent retry-safe
  execution records
- Bounded structured memory plus a separate full activity timeline
- Workflow-authorized finalization followed by typed AI or deterministic final
  output
- Live event, instruction, pause, resume, interrupt, and graceful-terminate APIs
- API-visible run snapshots, compact memory, final output, live Workflow Query
  state, and persistent history
- Minimal global and per-run operator analytics derived from PostgreSQL
- Real-data supervisor configuration, run start/list/detail, timeline, memory,
  event, instruction, lifecycle-control, and final-output interfaces

## Repository structure

    .
    ├── frontend/                    Next.js operational console and typed API client
    ├── backend/
    │   ├── app/
    │   │   ├── analytics/           Typed PostgreSQL aggregate queries
    │   │   ├── models/              Supervisor, run, and activity ORM models
    │   │   ├── repositories/        Focused async persistence operations
    │   │   ├── routers/             Supervisor and run HTTP endpoints
    │   │   ├── schemas/             API contracts
    │   │   ├── supervisor/          Typed provider contracts and implementations
    │   │   └── temporal/            Workflow, Activities, policy, and DTOs
    │   ├── migrations/versions/     Reversible PostgreSQL schema revisions
    │   └── tests/                   API, provider, action, and Workflow coverage
    ├── docs/                        Assignment acceptance matrix
    ├── scripts/                     Stage 1 through Stage 3.5 demonstrations
    ├── docker-compose.yml           Local PostgreSQL and Temporal services
    ├── Makefile                     Local development commands
    └── .env.example                 Safe local configuration template

The short system overview is in [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md).

## Prerequisites and install

- Node.js 20.9 or newer and npm
- Python 3.13
- `uv`
- Docker Desktop with Docker Compose
- `make`
- Ollama only for the optional real-model evaluation and Stage 3 AI demo

From the repository root:

    cp .env.example .env
    make install

The `.env` file is ignored. Its defaults contain no secrets. Do not place
secrets in supervisor `model_config`; the API accepts only the typed,
secret-free fields `provider`, `model`, and `temperature`. Temperature is not
an execution control in this POC: the operator UI fixes it at `0`, and the
Ollama adapter always uses `0`.

## Supervisor provider configuration

The safe local defaults are:

    LLM_PROVIDER=deterministic
    OLLAMA_BASE_URL=http://localhost:11434
    OLLAMA_MODEL=qwen3:1.7b
    OLLAMA_TIMEOUT_SECONDS=30

`LLM_PROVIDER=deterministic` keeps application startup and automated tests
independent of Ollama. A supervisor configuration can explicitly select either
`deterministic` or `ollama`; that selection and model are frozen into the run's
Workflow input.

The Ollama provider calls the local `/api/chat` endpoint directly from a
Temporal Activity. It uses JSON-schema output generated from the Pydantic
contract and these resource-conscious settings:

- `stream: false`
- `think: false`
- `keep_alive: 0`
- `temperature: 0`
- `num_ctx: 2048`
- `num_predict: 512`

The implementation does not use an Ollama SDK, agent framework, LangChain,
LangGraph, RAG, or vector storage.

## Run the operator console

The browser UI requires PostgreSQL, Temporal, the Worker, FastAPI, and—when an
Ollama supervisor is selected—the local `qwen3:1.7b` model. After the one-time
install above, open five terminals in the repository root.

Terminal 1 — start infrastructure, verify Temporal, and apply migrations:

    make infra-up
    make temporal-infra-check
    make db-migrate

Terminal 2 — start the Temporal Worker and leave it running:

    make temporal-worker

Terminal 3 — start FastAPI and leave it running:

    make backend-dev

Terminal 4 — start Ollama with the required model available:

    ollama pull qwen3:1.7b
    ollama serve

If the Ollama desktop application is already serving the model, do not start a
second server.

Terminal 5 — start the frontend and leave it running:

    make frontend-dev

Open [http://localhost:3000](http://localhost:3000). The route map is:

- `/` — PostgreSQL-backed global analytics and navigation.
- `/supervisors` — list and create safe supervisor configurations.
- `/runs` — start a Workflow, filter persisted runs, and inspect their state.
- `/runs/{run_id}` — live Workflow state, per-run analytics, persistent
  timeline, compact memory, instructions, events, lifecycle controls, durable
  identifiers, and final output.

The deterministic browser demo path is:

1. Open Overview and note the global analytics.
2. Open Supervisors; create or select an Ollama `qwen3:1.7b` configuration with
   a short wake interval and the five allowed actions.
3. Open Runs and start a unique order with that configuration.
4. On the detail screen, observe startup inference, then the Sleeping state and
   actual next wake time.
5. Send `payment_confirmed`; wait for the persistent timeline to show the event
   and routine wake suppression.
6. Send `shipment_delayed`; wait for the Qwen decision and persisted
   `message_logistics_team` action.
7. Confirm compact memory changed, add `For this order, prioritize speed over
   cost.`, and wait until the retained instruction and timeline row appear.
8. Pause, resume, interrupt for human review, and resume again; after every
   accepted signal, wait for the durable status/timeline transition.
9. Allow a scheduled wake to appear, then send `delivered`.
10. Confirm deterministic Completed status, the five per-run metrics, and all
    four final-output sections: final summary, important actions, key learnings,
    and recommendations.
11. Return to Overview and confirm the aggregate values changed.
12. To validate graceful termination separately, start a disposable order and
    use Terminate with a reason; confirm its final output and terminal timeline.

Active detail pages poll roughly every two seconds, the run list every four
seconds, and Overview every ten seconds. Signal endpoints acknowledge before
PostgreSQL necessarily contains the transition, so the UI says accepted and
keeps the affected control pending until polling reveals durable evidence.
Terminal run detail stops automatic polling. This local POC has no
authentication; do not expose it as a production service.

## Manual end-to-end runbook

Follow these steps in order when bringing up the complete product. Keep each
long-running command in its own terminal; stop it later with `Ctrl+C`.

### 1. Install once

From the repository root:

    cp .env.example .env
    make install

The checked-in `.env.example` uses the local PostgreSQL, Temporal, and
deterministic-provider defaults. The `.env` file is ignored and contains no
secret by default.

### 2. Start infrastructure and migrate

In Terminal 1:

    make infra-up
    make temporal-infra-check
    make db-migrate

Wait for both containers to be healthy. The migration command should finish at
the Alembic `head` revision. These commands retain named Docker volumes; do not
remove them as a troubleshooting shortcut.

### 3. Start the optional real model

For an Ollama demonstration, in Terminal 2 run:

    ollama pull qwen3:1.7b
    ollama serve

If the Ollama desktop app is already serving, only run the pull command when the
model is missing. A deterministic supervisor can be used without this terminal.

### 4. Start the Temporal Worker

In Terminal 3:

    make temporal-worker

Leave it running. Its startup output should name the `order-supervisor` task
queue. No order can progress until this Worker is polling.

### 5. Start FastAPI

In Terminal 4:

    make backend-dev

Check `http://localhost:8000/health` in a browser. It should report the API and
database as healthy. Keep this terminal running.

### 6. Start Next.js

In Terminal 5:

    make frontend-dev

Open [http://localhost:3000](http://localhost:3000). The browser console is the
operator surface; it polls PostgreSQL-backed run state while Temporal remains
the lifecycle authority.

### 7. Configure a supervisor

1. Open **Supervisors** and choose **Create supervisor**.
2. Enter a name and base instruction, for example
   `Monitor this order and surface important exceptions.`
3. Choose **Ollama / qwen3:1.7b** for a real-model run, or **Deterministic** for
   a model-free run.
4. Set the wake interval to `45` seconds and wake aggressiveness to `moderate`.
5. Enable all five exact actions:
   `message_fulfillment_team`, `message_payments_team`,
   `message_logistics_team`, `message_customer`, and `create_internal_note`.
6. Save, then confirm the configuration appears in the list.

### 8. Start and operate one order

1. Open **Runs**, enter a unique order ID (for example
   `demo-order-20260905-01`), provide a small order context, select the saved
   supervisor, and click **Start run**.
2. Open the new run detail. Verify the Workflow ID is
   `order-supervisor:<order-id>` and wait for startup inference to finish.
3. Confirm the status is **Sleeping** and a future **Next wake** is visible.
4. Send an event with a new event ID and type `payment_confirmed`. Wait for the
   timeline row and the `supervisor_wake_suppressed` entry; the invocation count
   should not increase.
5. Send another new event with type `shipment_delayed` and a payload such as
   `{"delay_hours":24}`. Wait for the supervisor decision and the
   `message_logistics_team` action in the timeline and action history.
6. Add the live instruction `For this order, prioritize speed over cost.`
   Confirm it appears in retained instructions and the timeline.
7. Exercise **Pause**, wait for **Paused**, then **Resume** and wait for the
   next state. Exercise **Interrupt** with a reason such as `Review carrier
   exception`, wait for **Interrupted**, and **Resume** again.
8. Send a fresh event of type `delivered`. Wait for **Completed**, then inspect
   the final summary, important actions, key learnings, recommendations, memory,
   and the full timeline.
9. Return to **Overview** to see global analytics. The run-detail analytics
   strip shows the counts for this run.

The API acknowledges Signals before the PostgreSQL projection necessarily has
the new row. Keep the detail page open until polling shows the accepted event,
control transition, or action. Terminal runs stop automatic polling. For a
separate control-path check, start a disposable order and use **Terminate** with
an explicit reason; graceful termination produces a terminal row and final
output without hard-stopping the Temporal execution.

### 9. Shut down safely

Press `Ctrl+C` in the frontend, API, Worker, and (if used) Ollama terminals.
Then, from the repository root, run:

    make infra-down

This stops PostgreSQL and Temporal while retaining their named volumes for the
next session. Start again at step 2. Never expose this unauthenticated local POC
to the public internet.

## Run the Stage 3 real-AI demo

First make sure Ollama is running, then install the exact model once:

    ollama pull qwen3:1.7b

Open four terminals in the repository root.

Terminal 1 — start PostgreSQL and Temporal, verify them, and apply migrations:

    make infra-up
    make temporal-infra-check
    make db-migrate

Expected output includes both containers becoming `Healthy`, PostgreSQL
`accepting connections`, Temporal `SERVING`, and Alembic using its PostgreSQL
migration context. Re-running `make db-migrate` is safe.

Upgrade safety gate — complete this before Terminal 2 when reusing an existing
Temporal volume:

    docker compose exec -T temporal temporal workflow list \
      --address 127.0.0.1:7233 --query 'ExecutionStatus="Running"'

A fresh setup should print no rows. If an upgrade from Stage 0–2 lists a running
Workflow, do not start the Stage 3 Worker. Complete or gracefully terminate that
execution while its old Worker is still available, verify the list is empty,
and only then continue. Closed histories and both named volumes may remain.

Terminal 2 — start the Worker and leave it running:

    make temporal-worker

Expected output includes `Temporal Worker starting` and the
`order-supervisor` task queue.

Terminal 3 — start FastAPI and leave it running:

    make backend-dev

Expected output includes `Application startup complete`. Interactive API
documentation is at [http://localhost:8000/docs](http://localhost:8000/docs).

Terminal 4 — verify the five routing cases, then run the API-driven real-AI
scenario:

    make ollama-eval
    make stage3-demo STAGE3_PROVIDER=ollama

The evaluator explicitly requires Ollama and `qwen3:1.7b`. It prints the model,
selected action, latency, and pass/fail for payment failure, shipment delay,
stalled fulfillment, a direct customer-update request, and an unknown issue.
Success requires `5/5`.

The Stage 3 demo creates a unique all-actions supervisor and order. It proves a
startup inference, routine wake suppression, a real shipment-delay decision,
persisted `message_logistics_team` execution, compact memory, a live instruction
in later scheduled-inference context, deterministic delivered completion, and
persisted final output. It prints the final report and full persistent timeline,
then ends with:

    STAGE 3 AI DEMO PASSED

For the same scenario without Ollama, use the explicitly selected deterministic
provider:

    make stage3-demo STAGE3_PROVIDER=deterministic

If the Ollama API or structured response fails, the Activity makes at most three
attempts. The Workflow executes no unvalidated action, records a concise
rejection, and schedules another durable review. It does not silently switch
providers during that decision. Selecting the deterministic provider is the
predictable fallback mode for development; final-output failure additionally
uses a deterministic report so the Workflow-owned terminal result is never
lost.

Common recovery steps:

- `Ollama is unavailable`: start the Ollama application or `ollama serve`, then
  rerun the command.
- `Required model qwen3:1.7b is missing`: run `ollama pull qwen3:1.7b`.
- Temporal is unavailable: run `make temporal-infra-check`, then restart only
  the Worker.
- A database test reports a missing table: run `make db-migrate`.
- A Docker port is allocated: use
  `lsof -nP -iTCP:<port> -sTCP:LISTEN` and stop only a process you recognize.

Never delete the named volumes merely to repair a migration.

## Stage 3 validation record

The recorded Stage 3 milestone validation on 2026-09-04 verified:

- The then-current backend suite and Ruff lint/format checks passed. Detailed
  milestone evidence is maintained in `docs/ACCEPTANCE_MATRIX.md`.
- Fresh Stage 1 and Stage 2 regression demos passed.
- `qwen3:1.7b` routing evaluation: 5/5. Observed latencies were 3.769 s
  (payment), 3.536 s (shipment delay), 3.573 s (stalled fulfillment),
  3.145 s (customer update), and 3.098 s (unknown issue).
- Real-AI run `ee1bb484-ca11-474b-a4c7-2fa826efd452` ended
  `STAGE 3 AI DEMO PASSED` with 24 distinct timeline keys and two distinct
  simulated-action keys.
- The model's initial workflow-start response was malformed. The contract
  rejected it after bounded retries, persisted the safe outcome, executed no
  action, and kept the Workflow alive. The later shipment-delay and scheduled
  decisions were valid; the first selected `message_logistics_team`, and the
  scheduled request contained the live instruction ID.
- The delivered event, not the AI, authorized completion. The run persisted and
  returned all four validated final-output fields.
- Frontend lint and the canonical Next.js webpack production build passed. The
  target explicitly uses webpack because Next's default Turbopack path could not
  bind its internal local port in the Codex sandbox.

## Simulated business actions

The exact executable action names are:

- `message_fulfillment_team`
- `message_payments_team`
- `message_logistics_team`
- `message_customer`
- `create_internal_note`

One strict Temporal Activity dispatcher maps the enum to five explicit local
handlers. Before dispatch, application code validates the exact action name,
the single typed `content` argument, lifecycle-boundary language, and whether
the supervisor enabled the action. No model text is used as a Python function
name, and no real message leaves the application.

Each invocation uses the Workflow-owned key
`{workflow_id}:{run_id}:action:{supervisor_invocation}:{action_index}`.
PostgreSQL's unique `activities.activity_key` constraint makes Activity retries
return the original simulated result instead of creating another message or
timeline row.

## Memory and final output

Compact memory is a six-field structured snapshot: `order_state`,
`important_facts`, `open_issues`, `actions_taken`, `active_constraints`, and
`next_review`. Lists retain at most six validated items. A provider receives the
current memory, at most ten recent timeline entries, at most eight live
instructions, and a total user-context budget of 4,000 characters. This rolling
snapshot is persisted in `runs.memory_summary`; the complete audit timeline
remains separate.

Only Workflow-owned rules authorize finalization: currently a terminal
`delivered` event or graceful manual termination. After authorization, a
separate typed Activity generates `final_summary`, `important_actions`,
`key_learnings`, and `recommendations`. PostgreSQL stores the object in
`runs.final_output`, and `GET /api/runs/{run_id}` returns it. An AI completion
recommendation is audit data only and cannot end a Workflow.

## Minimal Run Analytics

Stage 3.5 implements the assignment's optional richer run analytics
Good-to-Have as a deliberately small operator-insight layer. Global analytics
report total runs, active/completed/terminated outcomes, the wake suppression
rate, executed business actions, average time to first intervention, and the
distribution across the five required action names. Per-run analytics report
duration, received events, AI supervisor invocations, wake suppressions, and
executed business actions.

Total runs includes every persisted status. Active means `starting`, `running`,
`sleeping`, `paused`, or `interrupted`; completed and terminated count only
their exact statuses, so a failed run appears only in the total. Event,
invocation, suppression, and action counts use only `order_event_received`,
`supervisor_invocation`, `supervisor_wake_suppressed`, and validated
`business_action_executed` rows respectively; duplicate, proposed, rejected,
failed, and unknown-action records do not inflate them.

All metrics are read-only aggregates over the existing PostgreSQL `runs` and
`activities` records; there is no warehouse, BI platform, or additional
analytics infrastructure. The wake suppression rate is persisted
`supervisor_wake_suppressed` decisions divided by all immediate event wake
decisions (`supervisor_wake_suppressed` plus `supervisor_wake_requested`), and
is `null` when that denominator is zero. Average time to first intervention
uses only executed actions whose trigger is an incoming event. Each action is
paired with the closest preceding same-type `supervisor_wake_requested` record,
then with its accepted `order_event_received` record by external event ID. The
earliest matched action per run contributes the nonnegative interval from event
receipt to action execution; unmatched runs are excluded and an empty eligible
set returns `null`. Active duration uses the current database time, while terminal
duration is `completed_at - started_at`.

These GET endpoints need PostgreSQL and FastAPI only; they remain readable when
Temporal or the Worker is unavailable. Use the persisted run UUID printed by
the Stage 2 demo, or an existing Stage 3 run `id` returned by the runs API, to
inspect both analytics views with:

    make stage35-demo STAGE35_RUN_ID=<run-uuid>

The smoke utility makes exactly the two analytics requests, validates the
stable five-action distribution, prints both responses, and ends with
`STAGE 3.5 ANALYTICS DEMO PASSED`.

## API surface

| Method | Path | Purpose |
| --- | --- | --- |
| `POST` | `/api/supervisors` | Create a supervisor configuration |
| `GET` | `/api/supervisors` | List supervisor configurations |
| `GET` | `/api/supervisors/{supervisor_id}` | Get one supervisor |
| `POST` | `/api/runs` | Persist and start one order Workflow |
| `GET` | `/api/runs` | List runs; optional `status` filter |
| `GET` | `/api/runs/{run_id}` | Get state, timeline, memory, actions, and final output |
| `GET` | `/api/analytics/summary` | Get PostgreSQL-derived global operator analytics |
| `GET` | `/api/runs/{run_id}/analytics` | Get PostgreSQL-derived analytics for one run |
| `POST` | `/api/runs/{run_id}/events` | Signal an order event |
| `POST` | `/api/runs/{run_id}/instructions` | Signal a live instruction |
| `POST` | `/api/runs/{run_id}/pause` | Pause automated processing |
| `POST` | `/api/runs/{run_id}/resume` | Resume a paused/interrupted run |
| `POST` | `/api/runs/{run_id}/interrupt` | Enter urgent human-review state |
| `POST` | `/api/runs/{run_id}/terminate` | Request graceful termination |

Signal endpoints return `202 Accepted`. Unknown records return `404`, invalid
lifecycle transitions and duplicate active orders return `409`, validation
failures return a value-redacted `422`, and unavailable dependencies return a
credential-free `503` response. Run detail remains available from PostgreSQL
with `workflow_state: null` when Temporal cannot be reached.

## Control semantics

- **Pause:** the Workflow remains alive. Events and duplicate IDs are accepted,
  deduplicated, queued, and audited, but neither queued events nor scheduled
  reviews invoke the supervisor until resume. The Workflow waits durably.
- **Resume:** clears paused or interrupted blocking state. Queued events are
  processed. If the scheduled deadline passed while blocked, exactly one
  immediate scheduled review runs before the next normal deadline is set.
- **Interrupt:** immediately breaks the current wait and enters an explicit
  `interrupted` human-review state. Events remain accepted and deduplicated, but
  automated inference stays blocked until resume or terminate.
- **Terminate:** the API sends `request_termination`; it does not hard-terminate
  Temporal. The Workflow records and retains the reason, authorizes a
  `terminated` outcome, generates final output or its deterministic fallback,
  persists the terminal state, clears its wake time, and exits cleanly.

## Temporal, AI, and PostgreSQL boundary

Temporal is authoritative for lifecycle, order state, Signals, timers,
sleep/wake behavior, controls, action orchestration, and deterministic
completion. Workflow code performs no database or network I/O.

The AI interprets bounded context, proposes only typed actions, updates compact
memory, recommends a next review, and writes final-report content. Pydantic and
the action layer validate its output. The AI never owns lifecycle authority.

PostgreSQL is the API/UI-facing operational read model and audit trail. A
bounded-retry Temporal Activity writes each transition using the deterministic,
run-scoped key `{workflow_id}:{run_id}:{timeline_sequence}`. The activity row
and run snapshot update share one database transaction, and a replayed older
transition cannot regress a newer snapshot.

This is eventual consistency, not a distributed transaction: API clients and
the demos use short bounded polling after Signals. A database start row is
created before Temporal starts; a failed Temporal start is marked `failed`.
A PostgreSQL partial unique index plus API validation prevents two active runs
for one order.

## Stage 1 and Stage 2 regression demos

With infrastructure, Worker, and FastAPI running:

    make temporal-demo
    make stage2-demo

The Stage 1 direct-Temporal path should show invocation counts `1 → 1 → 2 → 3`
and end with `STAGE 1 DEMO PASSED`. The Stage 2 public-API path exercises
persistence, live instructions, pause/resume, interrupt/resume, and graceful
termination, then ends with `STAGE 2 API DEMO PASSED`. Both explicitly use the
deterministic provider and do not require Ollama.

## Tests and checks

Start infrastructure and apply migrations before the complete backend suite:

    make infra-up
    make db-migrate
    make backend-test
    make backend-lint
    make frontend-lint
    make frontend-build

Run all repository code checks with `make check`. Infrastructure health, the
real-model evaluator, and the three live demos remain separate validations.

When finished, stop the API and Worker with `Ctrl+C`, then retain data while
stopping infrastructure:

    make infra-down

## Stage 3 upgrade boundary

Stage 3 deliberately evolves the serialized result returned by the existing
supervisor Activity. The upgrade safety gate appears before Worker startup in
the demo instructions above because every pre-Stage-3 Workflow must finish
under its old Worker first. This local validation began with no open legacy
histories. Hot replay of an already-open Stage 1/2 execution is not supported
by this POC; this is a code-deployment boundary, not a reason to delete data.

## Intentionally deferred

Authentication, real external messaging, cloud deployment, RAG, vector databases, queues,
multi-agent behavior, and production infrastructure remain outside this POC scope.
