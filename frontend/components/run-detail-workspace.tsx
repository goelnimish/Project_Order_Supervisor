"use client";

import Link from "next/link";
import { useEffect, useMemo, useRef, useState } from "react";

import { ActivityTimeline } from "@/components/activity-timeline";
import { EventInjector } from "@/components/event-injector";
import { FinalOutputPanel } from "@/components/final-output-panel";
import { InstructionForm } from "@/components/instruction-form";
import {
  LifecycleControls,
  type LifecycleAction,
} from "@/components/lifecycle-controls";
import { MemoryPanel } from "@/components/memory-panel";
import { OrderStatePanel } from "@/components/order-state-panel";
import { RunAnalyticsStrip } from "@/components/run-analytics-strip";
import { StatusBadge } from "@/components/status-badge";
import { getRunAnalytics } from "@/lib/api/analytics";
import {
  ApiError,
  getApiErrorMessage,
  isAbortError,
} from "@/lib/api/client";
import {
  addRunInstruction,
  getRunDetail,
  injectOrderEvent,
  interruptRun,
  pauseRun,
  resumeRun,
  terminateRun,
} from "@/lib/api/runs";
import { listSupervisors } from "@/lib/api/supervisors";
import {
  isCurrentOrderState,
  isRetainedInstruction,
} from "@/lib/api/guards";
import type {
  InstructionRequest,
  InterruptRunRequest,
  OrderEventRequest,
  PauseRunRequest,
  RunAnalytics,
  RunDetail,
  RetainedInstruction,
  Supervisor,
  TerminateRunRequest,
  WorkflowStatus,
} from "@/lib/api/types";
import { formatDateTime, formatRelativeTime, humanizeIdentifier } from "@/lib/format";
import { isTerminalStatus } from "@/lib/run-status";

const DETAIL_POLL_INTERVAL_MS = 2_000;
const ANALYTICS_POLL_INTERVAL_MS = 4_000;

function getEffectiveStatus(run: RunDetail): WorkflowStatus {
  if (isTerminalStatus(run.status)) {
    return run.status;
  }

  return run.workflow_state?.workflow_status ?? run.status;
}

function hasLifecycleConverged(
  action: LifecycleAction,
  status: WorkflowStatus,
): boolean {
  if (isTerminalStatus(status)) {
    return true;
  }

  switch (action) {
    case "pause":
      return status === "paused";
    case "resume":
      return status === "starting" || status === "running" || status === "sleeping";
    case "interrupt":
      return status === "interrupted";
    case "terminate":
      return status === "terminated";
  }
}

function mutationConflict(): ApiError {
  return new ApiError({
    status: 409,
    code: "signal_pending",
    message: "Another signal is still being accepted. Wait for durable state, then try again.",
  });
}

function RunDetailSkeleton() {
  return (
    <div aria-busy="true" className="page-content" role="status">
      <span className="sr-only">Loading run details.</span>
      <header className="border-b border-[var(--line-soft)] pb-7">
        <span aria-hidden="true" className="skeleton block h-3 w-24 rounded" />
        <div className="mt-5 flex items-start justify-between gap-6">
          <div className="min-w-0 flex-1">
            <span aria-hidden="true" className="skeleton block h-10 w-full max-w-xl rounded" />
            <span aria-hidden="true" className="skeleton mt-4 block h-3 w-full max-w-md rounded" />
          </div>
          <span aria-hidden="true" className="skeleton block h-8 w-24 rounded-full" />
        </div>
      </header>

      <div className="mt-5 grid grid-flow-dense gap-px overflow-hidden rounded-[var(--radius-panel)] border border-[var(--line)] bg-[var(--line-soft)] sm:grid-cols-2 lg:grid-cols-5">
        {Array.from({ length: 5 }, (_, index) => (
          <div className="bg-[var(--panel)] px-5 py-5" key={index}>
            <span aria-hidden="true" className="skeleton block h-3 w-20 rounded" />
            <span aria-hidden="true" className="skeleton mt-3 block h-5 w-28 rounded" />
          </div>
        ))}
      </div>

      <div className="mt-5 grid gap-4 lg:grid-cols-12 lg:items-start">
        <span aria-hidden="true" className="skeleton block h-[32rem] rounded-[var(--radius-panel)] lg:col-span-8" />
        <span aria-hidden="true" className="skeleton block h-[32rem] rounded-[var(--radius-panel)] lg:col-span-4" />
      </div>
    </div>
  );
}

function RetainedInstructionsPanel({
  instructions,
}: {
  instructions: RetainedInstruction[];
}) {
  return (
    <section
      aria-labelledby="retained-instructions-title"
      className="rounded-[var(--radius-panel)] border border-[var(--line)] bg-[var(--panel)]"
    >
      <div className="border-b border-[var(--line-soft)] px-4 py-4 sm:px-5">
        <h2
          className="text-sm font-semibold tracking-[-0.01em] text-[var(--ink)]"
          id="retained-instructions-title"
        >
          Retained instructions
        </h2>
        <p className="mt-1 text-xs leading-5 text-[var(--muted)]">
          Persisted operator guidance included in future supervisor reviews.
        </p>
      </div>
      {instructions.length === 0 ? (
        <p className="px-4 py-5 text-sm leading-6 text-[var(--quiet)] sm:px-5">
          No live instructions have been retained yet.
        </p>
      ) : (
        <ol className="divide-y divide-[var(--line-soft)]">
          {instructions.map((instruction) => (
            <li className="px-4 py-4 sm:px-5" key={instruction.instruction_id}>
              <p className="whitespace-pre-wrap text-sm leading-6 text-[var(--ink)]">
                {instruction.instruction}
              </p>
              <time
                className="mt-2 block font-mono text-[11px] tabular-nums text-[var(--quiet)]"
                dateTime={instruction.created_at}
              >
                {formatDateTime(instruction.created_at)}
              </time>
            </li>
          ))}
        </ol>
      )}
    </section>
  );
}

export function RunDetailWorkspace({ runId }: { runId: string }) {
  const [run, setRun] = useState<RunDetail | null>(null);
  const [analytics, setAnalytics] = useState<RunAnalytics | null>(null);
  const [supervisors, setSupervisors] = useState<Supervisor[] | null>(null);
  const [detailError, setDetailError] = useState<string | null>(null);
  const [analyticsError, setAnalyticsError] = useState<string | null>(null);
  const [supervisorError, setSupervisorError] = useState<string | null>(null);
  const [isAnalyticsLoading, setIsAnalyticsLoading] = useState(true);
  const [isRequestPending, setIsRequestPending] = useState(false);
  const [pendingAction, setPendingAction] = useState<LifecycleAction | null>(null);
  const [lastSyncedAt, setLastSyncedAt] = useState<string | null>(null);
  const [refreshVersion, setRefreshVersion] = useState(0);
  const [isNotFound, setIsNotFound] = useState(false);

  const pendingActionRef = useRef<LifecycleAction | null>(null);
  const mutationInFlightRef = useRef(false);
  const terminalRef = useRef(false);

  const status = run ? getEffectiveStatus(run) : null;
  const terminal = status ? isTerminalStatus(status) : false;
  const persistedTerminal = run ? isTerminalStatus(run.status) : false;

  useEffect(() => {
    const controller = new AbortController();

    async function loadSupervisors() {
      try {
        const nextSupervisors = await listSupervisors({ signal: controller.signal });
        setSupervisors(nextSupervisors);
        setSupervisorError(null);
      } catch (error) {
        if (!isAbortError(error)) {
          setSupervisorError(
            getApiErrorMessage(error, "Supervisor names are temporarily unavailable."),
          );
        }
      }
    }

    void loadSupervisors();
    return () => controller.abort();
  }, []);

  useEffect(() => {
    let disposed = false;
    let controller: AbortController | null = null;
    let pollTimer: ReturnType<typeof setTimeout> | null = null;

    async function refreshDetail() {
      controller = new AbortController();
      let shouldContinue = !terminalRef.current;

      try {
        const nextRun = await getRunDetail(runId, { signal: controller.signal });
        if (disposed) {
          return;
        }

        const nextStatus = getEffectiveStatus(nextRun);
        const nextIsPersistedTerminal = isTerminalStatus(nextRun.status);
        terminalRef.current = nextIsPersistedTerminal;
        shouldContinue = !nextIsPersistedTerminal;

        setRun(nextRun);
        setDetailError(null);
        setIsNotFound(false);
        setLastSyncedAt(new Date().toISOString());

        const actionAwaitingState = pendingActionRef.current;
        if (
          actionAwaitingState &&
          hasLifecycleConverged(actionAwaitingState, nextStatus)
        ) {
          pendingActionRef.current = null;
          setPendingAction(null);
        }
      } catch (error) {
        if (!disposed && !isAbortError(error)) {
          if (error instanceof ApiError && error.status === 404) {
            shouldContinue = false;
            setIsNotFound(true);
          }
          setDetailError(
            getApiErrorMessage(
              error,
              "The run ledger could not be refreshed. Check the backend, then try again.",
            ),
          );
        }
      } finally {
        if (!disposed && shouldContinue) {
          pollTimer = setTimeout(refreshDetail, DETAIL_POLL_INTERVAL_MS);
        }
      }
    }

    void refreshDetail();

    return () => {
      disposed = true;
      controller?.abort();
      if (pollTimer) {
        clearTimeout(pollTimer);
      }
    };
  }, [refreshVersion, runId]);

  useEffect(() => {
    if (isNotFound) {
      return;
    }

    let disposed = false;
    let controller: AbortController | null = null;
    let pollTimer: ReturnType<typeof setTimeout> | null = null;

    async function refreshAnalytics() {
      controller = new AbortController();

      try {
        const nextAnalytics = await getRunAnalytics(runId, {
          signal: controller.signal,
        });
        if (disposed) {
          return;
        }

        setAnalytics(nextAnalytics);
        setAnalyticsError(null);
      } catch (error) {
        if (!disposed && !isAbortError(error)) {
          setAnalyticsError(
            getApiErrorMessage(
              error,
              "Run analytics could not be refreshed. Check the backend, then try again.",
            ),
          );
        }
      } finally {
        if (!disposed) {
          setIsAnalyticsLoading(false);
          if (!persistedTerminal) {
            pollTimer = setTimeout(refreshAnalytics, ANALYTICS_POLL_INTERVAL_MS);
          }
        }
      }
    }

    void refreshAnalytics();

    return () => {
      disposed = true;
      controller?.abort();
      if (pollTimer) {
        clearTimeout(pollTimer);
      }
    };
  }, [isNotFound, persistedTerminal, refreshVersion, runId]);

  const supervisorName = useMemo(() => {
    if (!run || !supervisors) {
      return null;
    }

    return (
      supervisors.find((supervisor) => supervisor.id === run.supervisor_config_id)
        ?.name ?? null
    );
  }, [run, supervisors]);

  function requestImmediateRefresh() {
    setIsNotFound(false);
    setRefreshVersion((version) => version + 1);
  }

  async function performSignal(request: () => Promise<unknown>) {
    if (mutationInFlightRef.current || pendingActionRef.current) {
      throw mutationConflict();
    }

    mutationInFlightRef.current = true;
    setIsRequestPending(true);

    try {
      await request();
      requestImmediateRefresh();
    } finally {
      mutationInFlightRef.current = false;
      setIsRequestPending(false);
    }
  }

  async function performLifecycleSignal(
    action: LifecycleAction,
    request: () => Promise<unknown>,
  ) {
    if (mutationInFlightRef.current || pendingActionRef.current) {
      throw mutationConflict();
    }

    mutationInFlightRef.current = true;
    pendingActionRef.current = action;
    setPendingAction(action);
    setIsRequestPending(true);

    try {
      await request();
      requestImmediateRefresh();
    } catch (error) {
      pendingActionRef.current = null;
      setPendingAction(null);
      throw error;
    } finally {
      mutationInFlightRef.current = false;
      setIsRequestPending(false);
    }
  }

  async function handleInjectEvent(request: OrderEventRequest) {
    await performSignal(() => injectOrderEvent(runId, request));
  }

  async function handleAddInstruction(request: InstructionRequest) {
    await performSignal(() => addRunInstruction(runId, request));
  }

  async function handlePause(request: PauseRunRequest) {
    await performLifecycleSignal("pause", () => pauseRun(runId, request));
  }

  async function handleResume() {
    await performLifecycleSignal("resume", () => resumeRun(runId));
  }

  async function handleInterrupt(request: InterruptRunRequest) {
    await performLifecycleSignal("interrupt", () => interruptRun(runId, request));
  }

  async function handleTerminate(request: TerminateRunRequest) {
    await performLifecycleSignal("terminate", () => terminateRun(runId, request));
  }

  if (!run) {
    if (!detailError) {
      return <RunDetailSkeleton />;
    }

    return (
      <div className="page-content">
        <Link className="text-sm text-[var(--accent)] hover:text-[var(--focus)]" href="/runs">
          Back to runs
        </Link>
        <section className="panel mt-5 px-5 py-8 sm:px-7" role="alert">
          <h1 className="text-balance text-3xl font-semibold tracking-[-0.03em] text-[var(--ink)]">
            This run ledger could not be loaded.
          </h1>
          <p className="mt-3 max-w-[65ch] text-sm leading-6 text-[var(--muted)]">
            {detailError}
          </p>
          <button
            className="button button-primary mt-6"
            onClick={requestImmediateRefresh}
            type="button"
          >
            Retry loading
          </button>
        </section>
      </div>
    );
  }

  const workflowState = run.workflow_state;
  const orderState = workflowState
    ? workflowState.order_state
    : run.current_order_state;
  const memory = run.memory_summary;
  const finalOutput = run.final_output;
  const nextWakeAt = workflowState ? workflowState.next_wake_at : run.next_wake_at;
  const retainedInstructions = run.additional_instructions.flatMap((instruction) =>
    isRetainedInstruction(instruction) ? [instruction] : [],
  );
  const retainedInstructionCount = retainedInstructions.length;
  const persistedEventIds = run.activities.flatMap((activity) =>
    activity.external_event_id ? [activity.external_event_id] : [],
  );
  const isMutationLocked = isRequestPending || pendingAction !== null;
  const lifecycleReason =
    workflowState?.interrupt_reason ??
    workflowState?.pause_reason ??
    workflowState?.completion_reason ??
    run.completion_reason;
  const orderStateSummary = isCurrentOrderState(orderState)
    ? `${humanizeIdentifier(orderState.lifecycle)} · Payment ${humanizeIdentifier(orderState.payment)} · Shipment ${humanizeIdentifier(orderState.shipment)}`
    : "State details unavailable";

  return (
    <div className="page-content">
      <header className="border-b border-[var(--line-soft)] pb-7">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <Link className="text-sm text-[var(--accent)] hover:text-[var(--focus)]" href="/runs">
            Back to runs
          </Link>
          <div className="flex items-center gap-3 text-xs text-[var(--quiet)]">
            <span>{persistedTerminal ? "Terminal state. Auto-refresh stopped." : "Live refresh every 2 seconds"}</span>
            <button
              className="button button-secondary min-h-9 px-3 text-xs"
              onClick={requestImmediateRefresh}
              type="button"
            >
              Refresh now
            </button>
          </div>
        </div>

        <div className="mt-6 grid gap-5 md:grid-cols-[minmax(0,1fr)_auto] md:items-start">
          <div className="min-w-0">
            <h1 className="break-words text-balance text-[clamp(2rem,4.5vw,4rem)] font-semibold leading-[0.98] tracking-[-0.035em] text-[var(--ink)]">
              Order {run.order_id}
            </h1>
            <p className="mt-4 max-w-[72ch] break-all font-mono text-xs leading-5 text-[var(--muted)]">
              Run {run.id}
            </p>
            <p className="mt-3 text-sm leading-6 text-[var(--muted)]">
              Current order state: {orderStateSummary}
            </p>
          </div>
          <StatusBadge status={status ?? run.status} />
        </div>

        {lifecycleReason ? (
          <p className="mt-4 max-w-[72ch] text-sm leading-6 text-[var(--muted)]">
            {lifecycleReason}
          </p>
        ) : null}
      </header>

      {detailError ? (
        <div className="state-message state-message-error mt-5 flex flex-wrap items-center justify-between gap-3" role="alert">
          <p>{detailError} The last successful ledger state remains visible below.</p>
          <button className="button button-secondary min-h-9 px-3 text-xs" onClick={requestImmediateRefresh} type="button">
            Retry refresh
          </button>
        </div>
      ) : null}

      {!workflowState ? (
        <div className="state-message mt-5" role="status">
          Temporal query state is unavailable. Showing the persisted run and activity ledger while controls continue through API signals.
        </div>
      ) : null}

      {pendingAction ? (
        <div className="mt-5 rounded-[var(--radius-control)] border border-[color:color-mix(in_srgb,var(--warning)_45%,transparent)] bg-[color:color-mix(in_srgb,var(--warning)_9%,transparent)] px-4 py-3 text-sm text-[#f3cc83]" role="status">
          The {pendingAction} signal was accepted. Conflicting controls remain locked until the durable workflow state changes.
        </div>
      ) : null}

      <dl className="mt-5 grid grid-flow-dense gap-px overflow-hidden rounded-[var(--radius-panel)] border border-[var(--line)] bg-[var(--line-soft)] sm:grid-cols-2 lg:grid-cols-5">
        <div className="bg-[var(--panel)] px-5 py-4">
          <dt className="text-xs text-[var(--quiet)]">Next wake</dt>
          <dd className="mt-1.5 text-sm font-semibold text-[var(--ink)]">
            {terminal ? "Run closed" : formatRelativeTime(nextWakeAt)}
          </dd>
          {nextWakeAt && !terminal ? (
            <time className="mt-1 block font-mono text-[11px] tabular-nums text-[var(--muted)]" dateTime={nextWakeAt}>
              {formatDateTime(nextWakeAt)}
            </time>
          ) : null}
        </div>
        <div className="bg-[var(--panel)] px-5 py-4">
          <dt className="text-xs text-[var(--quiet)]">Supervisor</dt>
          <dd className="mt-1.5 break-words text-sm font-semibold text-[var(--ink)]">
            {supervisorName ??
              (supervisorError
                ? "Name unavailable"
                : supervisors
                  ? "Configuration not found"
                  : "Loading name...")}
          </dd>
          {supervisorError ? (
            <p className="mt-1 text-[11px] leading-4 text-[#ffc4bd]">{supervisorError}</p>
          ) : null}
        </div>
        <div className="bg-[var(--panel)] px-5 py-4">
          <dt className="text-xs text-[var(--quiet)]">Instructions retained</dt>
          <dd className="mt-1.5 font-mono text-sm font-semibold tabular-nums text-[var(--ink)]">
            {retainedInstructionCount}
          </dd>
        </div>
        <div className="bg-[var(--panel)] px-5 py-4">
          <dt className="text-xs text-[var(--quiet)]">State source</dt>
          <dd className="mt-1.5 text-sm font-semibold text-[var(--ink)]">
            {workflowState ? "Temporal query" : "Persisted ledger"}
          </dd>
        </div>
        <div className="bg-[var(--panel)] px-5 py-4">
          <dt className="text-xs text-[var(--quiet)]">Last synced</dt>
          <dd className="mt-1.5 font-mono text-xs tabular-nums text-[var(--ink)]">
            {formatDateTime(lastSyncedAt)}
          </dd>
        </div>
      </dl>

      <div className="mt-4">
        <RunAnalyticsStrip
          analytics={analytics}
          error={analyticsError}
          isLoading={isAnalyticsLoading}
        />
      </div>

      <div className="mt-5 grid gap-4 lg:grid-cols-12 lg:items-start">
        <div className={`${terminal ? "order-1" : "order-2"} min-w-0 lg:order-1 lg:col-span-8`}>
          <ActivityTimeline activities={run.activities} />
        </div>

        <aside className={`${terminal ? "order-2" : "order-1"} min-w-0 lg:order-2 lg:col-span-4`} aria-labelledby="command-deck-heading">
          <div className="mb-3 flex items-baseline justify-between gap-3 px-1">
            <h2 className="text-lg font-semibold tracking-[-0.02em] text-[var(--ink)]" id="command-deck-heading">
              Command deck
            </h2>
            <span className="text-xs text-[var(--quiet)]">
              {terminal ? "Read only" : "Signals live"}
            </span>
          </div>
          <div className="grid gap-4">
            <EventInjector
              isMutationPending={isMutationLocked}
              onInject={handleInjectEvent}
              persistedEventIds={persistedEventIds}
              status={status ?? run.status}
            />
            <RetainedInstructionsPanel instructions={retainedInstructions} />
            <InstructionForm
              isMutationPending={isMutationLocked}
              onAddInstruction={handleAddInstruction}
              retainedInstructionCount={retainedInstructionCount}
              status={status ?? run.status}
            />
            <LifecycleControls
              onInterrupt={handleInterrupt}
              onPause={handlePause}
              onResume={handleResume}
              onTerminate={handleTerminate}
              pendingAction={pendingAction}
              status={status ?? run.status}
            />
          </div>
        </aside>
      </div>

      <div className="mt-5 grid grid-flow-dense gap-4 lg:grid-cols-12 lg:items-start">
        <div className="min-w-0 lg:col-span-5">
          <OrderStatePanel state={orderState} />
        </div>
        <div className="min-w-0 lg:col-span-7">
          <MemoryPanel memory={memory} />
        </div>
      </div>

      <section className="panel mt-5 overflow-hidden" aria-labelledby="durable-identifiers-heading">
        <header className="border-b border-[var(--line-soft)] px-5 py-4 sm:px-6">
          <h2 className="text-lg font-semibold tracking-[-0.02em] text-[var(--ink)]" id="durable-identifiers-heading">
            Durable identifiers
          </h2>
          <p className="mt-1 text-xs leading-5 text-[var(--quiet)]">
            Use these values to correlate the API record with Temporal and PostgreSQL.
          </p>
        </header>
        <dl className="grid gap-px bg-[var(--line-soft)] md:grid-cols-2">
          {[
            ["Workflow ID", run.workflow_id],
            ["Temporal run ID", run.temporal_run_id ?? "Not available"],
            ["Started", formatDateTime(run.started_at)],
            ["Completed", formatDateTime(run.completed_at)],
          ].map(([label, value]) => (
            <div className="min-w-0 bg-[var(--panel)] px-5 py-4 sm:px-6" key={label}>
              <dt className="text-xs text-[var(--quiet)]">{label}</dt>
              <dd className="mt-1.5 break-all font-mono text-xs leading-5 text-[var(--ink)]">
                {value}
              </dd>
            </div>
          ))}
        </dl>
      </section>

      <div className="mt-5">
        <FinalOutputPanel output={finalOutput} />
      </div>
    </div>
  );
}
