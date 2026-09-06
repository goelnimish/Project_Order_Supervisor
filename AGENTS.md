# Order Supervisor POC — Codex Project Instructions

## Project Mission

Build a small, reliable, end-to-end interview POC called “Order Supervisor.”

The product supervises one order from creation to completion using one durable
Temporal workflow per order. It receives order events over time, decides whether
the main AI supervisor must wake, performs simulated business actions, maintains
compact memory and an auditable timeline, sleeps between meaningful triggers,
accepts live human instructions, and produces a final report.

This is a proof of concept, not a production commerce platform.

Optimize for:

1. Correctness
2. Reliable demonstration
3. Clear architecture
4. Assignment compliance
5. Code quality
6. Ease of explanation
7. UI polish only after the above are complete

## Working Style

Work on one explicitly requested stage at a time.

Before changing files:

1. Inspect the relevant existing files.
2. State a concise implementation plan.
3. Identify assumptions or risks.
4. Make only the changes needed for the current stage.

Do not continue into a later stage unless explicitly instructed.

Do not silently add adjacent features, major abstractions, frameworks, or
infrastructure.

Prefer the smallest complete implementation over a broad unfinished system.

When a requirement is ambiguous, choose the simplest reliable behavior, document
the assumption, and keep the implementation easy to change.

## Assignment Source of Truth and Traceability

The original assignment document is the source of truth. If duplicated assignment
sections differ, implement the stricter or more specific requirement and document
that interpretation. Good-to-have items must not silently become mandatory scope.

Within the explicitly requested stage, add a proposed feature only when it:

1. directly satisfies an assignment requirement;
2. is necessary for a reliable walkthrough; or
3. materially supports a stated evaluation criterion.

Maintain `docs/ACCEPTANCE_MATRIX.md` throughout the project. The matrix must map
each assignment requirement to its implementation location, test or validation
evidence, and completion status.

## Nested Instruction Files

Framework-generated nested `AGENTS.md` files may supplement framework-specific
implementation conventions, but they must not weaken, replace, or override this
root file's:

- assignment requirements;
- architecture boundaries;
- security rules;
- validation rules;
- scope exclusions.

## Required Technology

Frontend:

- Next.js with App Router
- TypeScript
- Tailwind CSS
- npm

Backend:

- Python
- FastAPI
- uv for dependency management
- Pydantic for request, response, configuration, and agent schemas
- SQLAlchemy asynchronous database access
- asyncpg
- Alembic
- pytest
- pytest-asyncio
- Ruff

Orchestration:

- Temporal Python SDK using `temporalio`

Persistence:

- PostgreSQL
- Docker Compose for local PostgreSQL

Do not replace any required technology without explicit approval.

## Mandatory Product Requirements

The final POC must demonstrate:

1. One long-running Temporal workflow per order.
2. Workflow initialization with order context and supervisor instructions.
3. Order events delivered into the running workflow through Temporal signals.
4. Main supervisor inference on:
   - workflow start;
   - an important incoming event;
   - a scheduled Temporal wake-up.
5. Durable sleep and wake behavior without a tight polling loop.
6. A lightweight wake policy or classifier so routine events do not always invoke
   the main AI supervisor.
7. A timeline of important events, decisions, actions, instructions, and status
   changes.
8. A compact rolling memory summary.
9. Current run status, sleep state, and next wake-up time.
10. Additional instructions sent to an already-running workflow.
11. Pause, resume, interrupt, and terminate controls.
12. Final summary, important actions, key learnings, and recommendations.
13. A functional Next.js interface for configuring, starting, monitoring, and
    controlling runs.
14. Persistent activity records in PostgreSQL.

## Supervisor Configuration and Operator UI

The final supervisor configuration must preserve:

- name;
- base instruction;
- available actions;
- optional default wake-up behavior;
- optional model choice or model configuration;
- optional wake aggressiveness.

The final operator interface must allow an operator to:

- create or select a supervisor configuration;
- start a run for an order;
- distinguish active and completed runs;
- inspect timeline and business-action history;
- inspect compact memory;
- see current run status and next wake-up time;
- inject order events;
- add instructions to a live run;
- interrupt a run;
- pause a run;
- resume a run;
- terminate a run;
- inspect final summary, important actions, learnings, and recommendations.

## Exact Required Business Actions

Implement and preserve these exact action names:

- `message_fulfillment_team`
- `message_payments_team`
- `message_logistics_team`
- `message_customer`
- `create_internal_note`

External messaging is simulated.

Every action must:

1. Be validated against the allowed-action list.
2. Execute through a clean activity abstraction.
3. Create or update a persistent activity record.
4. Be visible in the run timeline.
5. Be safe against duplicate execution during retries.

Do not rename, omit, or replace these five actions.

## Architecture Rules

### Temporal Ownership

Temporal owns:

- durable workflow lifecycle;
- per-order workflow state;
- signal handling;
- scheduled timers;
- sleeping and waking;
- pause, resume, interrupt, and termination state;
- deterministic completion rules;
- orchestration of agent and business activities.

### AI Ownership

The main AI supervisor may:

- interpret order context;
- summarize the situation;
- propose allowed business actions;
- draft action content;
- update compact semantic memory;
- recommend a next review time;
- recommend completion.

The AI must not have sole authority to complete the workflow.

### Workflow Completion

Completion must be controlled by explicit workflow-owned rules, such as:

- a terminal order event;
- manual termination;
- configured maximum workflow age;
- another documented deterministic lifecycle condition.

The AI may recommend completion, but deterministic workflow logic makes the final
decision.

Do not expose `close_workflow` or an equivalent unrestricted completion tool to
the AI.

The AI may recommend completion, but only Workflow-owned lifecycle rules may
authorize completion.

### Temporal Determinism

Do not perform nondeterministic network or database operations directly inside
Temporal workflow code.

Use Temporal Activities for:

- LLM calls;
- PostgreSQL writes or external persistence;
- business action execution;
- memory compaction using an LLM;
- final-report generation;
- other network or nondeterministic operations.

Workflow code should primarily coordinate state, signals, timers, lifecycle rules,
and Activity execution.

### Signals

Use signals for at least:

- incoming order events;
- additional run instructions;
- pause;
- resume;
- interrupt;
- termination requests.

### Wake Policy

Known events should initially use predictable rule-based wake behavior.

Routine events may be recorded without invoking the main AI.

Important, ambiguous, or unknown events should wake the main AI or be safely
escalated.

Do not require a second complex agent for event classification.

### Agent Contract

The AI must return validated structured output.

Use a typed schema containing fields such as:

- concise decision summary;
- urgency;
- whether action is needed;
- proposed actions;
- memory update;
- next wake time or duration;
- optional completion recommendation.

Validate every model response before execution.

Reject:

- unknown actions;
- malformed arguments;
- unreasonable wake values;
- attempts to bypass lifecycle or permission rules.

Provide a deterministic or safe fallback when model output is unavailable or
invalid.

Do not store or display private chain-of-thought. Store only concise,
user-appropriate decision summaries or rationales.

### Idempotency

Incoming events and business actions must support duplicate prevention.

Use stable event IDs and deterministic action idempotency keys.

A retried Temporal Activity must not create duplicate customer or internal
messages.

### Persistence

Use PostgreSQL as the UI-facing operational read model and audit trail.

A unified activity log may store:

- incoming events;
- wake decisions;
- suppressed wakes;
- agent decisions;
- sleep decisions;
- business actions;
- additional instructions;
- lifecycle controls;
- memory updates;
- final output.

Keep the database schema small and appropriate for a POC.

## Temporal Determinism Details

Temporal Workflow code must not directly use:

- `datetime.now()`;
- `time.time()`;
- `time.sleep()`;
- arbitrary UUID generation;
- random-number generation;
- filesystem access;
- direct environment-variable reads;
- subprocess execution;
- database calls;
- external network calls.

Use Temporal-supported deterministic APIs and Workflow inputs, Signals, Queries,
and Activities for interaction with external or nondeterministic state. Signal
handlers may mutate or queue Workflow-owned state only.

Every Activity call must define explicit timeouts and a deliberately bounded retry
policy.

Use the deterministic Workflow ID convention `order-supervisor:{order_id}`.
Attempts to start a second active Workflow for the same order must have a
predictable, explicit, and documented outcome.

## Memory Rules

Do not build RAG, embeddings, a vector database, or a knowledge graph.

Agent context should use:

- supervisor base instruction;
- current order state;
- additional run-specific instructions;
- compact memory summary;
- a bounded recent-activity window;
- current trigger;
- allowed actions.

Use simple rolling compaction for older history.

## Scope Exclusions

Do not add unless explicitly requested:

- authentication;
- multi-tenancy;
- production cloud infrastructure;
- Kubernetes;
- microservices;
- message queues;
- real Shopify or commerce integrations;
- real email, SMS, or Slack integrations;
- advanced retrieval or RAG;
- vector databases;
- multiple cooperating agents;
- complex analytics;
- elaborate animations;
- unnecessary UI component frameworks;
- WebSockets when simple polling is sufficient;
- unrelated product features.

## Dependency Rules

Use the fewest dependencies necessary.

Before adding a dependency:

1. Confirm existing code cannot reasonably solve the requirement.
2. Explain why the dependency is needed.
3. Prefer maintained and widely used packages.
4. Avoid overlapping libraries that solve the same problem.

Do not install global packages.

Do not run `sudo` or Homebrew commands.

Do not modify files outside this project directory.

## Security and Secrets

Never place secrets in source code, documentation, prompts, tests, screenshots, or
committed files.

Use `.env` only for local values.

Keep `.env` in `.gitignore`.

Provide `.env.example` with variable names and safe defaults but no secret values.

Never print or expose the LLM API key.

Do not require an LLM API key for basic tests.

## Git Rules

Do not initialize Git, create branches, commit, push, reset, rebase, or modify Git
configuration unless explicitly instructed.

The project is currently being developed locally first.

## Code Quality

Backend code should:

- use type hints;
- use clear Pydantic schemas;
- keep HTTP, workflow, agent, activity, and persistence concerns separated;
- use explicit error handling;
- use asynchronous I/O where appropriate;
- avoid hidden global state;
- include focused tests for important behavior.

Frontend code should:

- use TypeScript;
- use App Router conventions;
- use small understandable components;
- represent loading, empty, success, and error states;
- prioritize operational clarity over decorative design.

Use descriptive names and avoid unnecessary comments that merely repeat the code.

## Final Submission Requirements

The mandatory final deliverables are:

1. Source code
2. README with complete setup instructions
3. Short architecture note

The final system must visibly demonstrate:

- creating or selecting a supervisor configuration;
- starting an order run;
- one Temporal Workflow for the order;
- events delivered through Signals;
- a routine event that suppresses main-supervisor inference;
- an important event that invokes the main supervisor;
- durable sleeping;
- scheduled waking;
- required business-action execution;
- persistent timeline;
- compact memory;
- instructions added to a live run;
- interrupt, pause, resume, and terminate controls;
- deterministic lifecycle completion;
- final summary;
- important actions;
- key learnings;
- recommendations or feedback.

## Validation Discipline

Do not claim a check passed unless the corresponding command was actually run.

After each stage, run all relevant available checks, such as:

- backend tests;
- backend linting;
- frontend linting;
- frontend TypeScript or production build;
- Docker Compose configuration validation;
- health endpoint checks;
- workflow tests;
- end-to-end smoke tests.

When a check fails:

1. Investigate it.
2. Fix it when the fix belongs to the current stage.
3. Re-run it.
4. Clearly report any unresolved failure.

Do not hide warnings, skipped tests, mocked behavior, or incomplete requirements.

## Stage Completion Report

At the end of every Codex task, report:

1. What changed, in plain English.
2. Files created or modified.
3. Important architecture decisions.
4. Commands executed.
5. Tests and checks that passed.
6. Failures, warnings, or limitations.
7. Exact manual validation steps for a beginner.
8. What remains intentionally deferred.
9. Whether any requirement from the current stage is incomplete.

Stop after the requested stage.

## Beginner-Friendly Collaboration

The repository owner is learning this stack and will manually validate each stage.

Use clear explanations without assuming prior Temporal, FastAPI, Next.js, or
PostgreSQL expertise.

When manual intervention is required, provide:

- the exact command;
- the directory where it must be run;
- the expected output;
- common failure output;
- the safest recovery step.

Do not ask the user to manually edit code unless there is a clear reason.
