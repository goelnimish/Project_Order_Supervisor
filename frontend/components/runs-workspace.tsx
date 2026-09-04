"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { FormEvent, useEffect, useMemo, useState } from "react";

import { getApiErrorMessage, isAbortError } from "@/lib/api/client";
import { createRun, listRuns } from "@/lib/api/runs";
import { listSupervisors } from "@/lib/api/supervisors";
import type {
  Run,
  RunCreate,
  Supervisor,
  WorkflowStatus,
} from "@/lib/api/types";
import { formatDateTime, humanizeIdentifier } from "@/lib/format";
import { isActiveStatus } from "@/lib/run-status";

const POLL_INTERVAL_MS = 4_000;

type RunFilter = "all" | "active" | "completed" | "terminated" | "failed";
type RunField = "order_id" | "supervisor_config_id" | "initial_state";

interface RunFieldError {
  field: RunField;
  message: string;
}

const FILTERS: ReadonlyArray<{ label: string; value: RunFilter }> = [
  { label: "All", value: "all" },
  { label: "Active", value: "active" },
  { label: "Completed", value: "completed" },
  { label: "Terminated", value: "terminated" },
  { label: "Failed", value: "failed" },
];

function runMatchesFilter(run: Run, filter: RunFilter) {
  if (filter === "active") {
    return isActiveStatus(run.status);
  }

  if (filter === "completed" || filter === "terminated" || filter === "failed") {
    return run.status === filter;
  }

  return true;
}

function getOrderStateSummary(state: Run["current_order_state"]): string {
  const fields = ["payment", "shipment", "refund"] as const;
  const parts = fields.flatMap((field) => {
    const value = state[field];
    return typeof value === "string" && value.trim()
      ? [`${humanizeIdentifier(field)} ${humanizeIdentifier(value)}`]
      : [];
  });

  return parts.length > 0 ? parts.join(" · ") : "State details unavailable";
}

function getStatusClasses(status: WorkflowStatus) {
  switch (status) {
    case "completed":
      return "border-[color:color-mix(in_srgb,var(--positive)_42%,transparent)] bg-[color:color-mix(in_srgb,var(--positive)_12%,transparent)] text-[var(--positive)]";
    case "running":
      return "border-[color:color-mix(in_srgb,var(--accent)_44%,transparent)] bg-[color:color-mix(in_srgb,var(--accent)_12%,transparent)] text-[var(--accent)]";
    case "sleeping":
      return "border-[color:color-mix(in_srgb,var(--accent)_44%,transparent)] bg-[color:color-mix(in_srgb,var(--accent)_12%,transparent)] text-[var(--accent)]";
    case "paused":
      return "border-[color:color-mix(in_srgb,var(--warning)_44%,transparent)] bg-[color:color-mix(in_srgb,var(--warning)_12%,transparent)] text-[var(--warning)]";
    case "interrupted":
      return "border-[color:color-mix(in_srgb,var(--warning)_44%,transparent)] bg-[color:color-mix(in_srgb,var(--warning)_12%,transparent)] text-[var(--warning)]";
    case "failed":
    case "terminated":
      return "border-[color:color-mix(in_srgb,var(--danger)_44%,transparent)] bg-[color:color-mix(in_srgb,var(--danger)_12%,transparent)] text-[var(--danger)]";
    case "starting":
    default:
      return "border-[var(--line)] bg-[var(--surface-strong)] text-[var(--muted)]";
  }
}

function StatusBadge({ status }: { status: WorkflowStatus }) {
  return (
    <span
      className={`inline-flex min-h-6 items-center rounded-full border px-2.5 py-1 text-[0.6875rem] font-semibold leading-none tracking-[0.04em] ${getStatusClasses(status)}`}
    >
      {humanizeIdentifier(status)}
    </span>
  );
}

function RunsSkeleton() {
  return (
    <div className="divide-y divide-[var(--line-soft)]" role="status">
      <span className="sr-only">Loading runs</span>
      {Array.from({ length: 5 }, (_, index) => (
        <div
          className="grid grid-cols-[1.4fr_1fr_0.8fr] gap-5 px-5 py-5"
          key={index}
        >
          <span className="h-3 w-3/4 animate-pulse rounded bg-[var(--line)]" />
          <span className="h-3 w-2/3 animate-pulse rounded bg-[var(--line-soft)]" />
          <span className="h-3 w-1/2 animate-pulse rounded bg-[var(--line-soft)]" />
        </div>
      ))}
    </div>
  );
}

export function RunsWorkspace() {
  const router = useRouter();
  const [runs, setRuns] = useState<Run[] | null>(null);
  const [supervisors, setSupervisors] = useState<Supervisor[] | null>(null);
  const [filter, setFilter] = useState<RunFilter>("all");
  const [loadError, setLoadError] = useState<string | null>(null);
  const [lastSyncedAt, setLastSyncedAt] = useState<string | null>(null);
  const [refreshVersion, setRefreshVersion] = useState(0);

  const [orderId, setOrderId] = useState("");
  const [supervisorId, setSupervisorId] = useState("");
  const [customerId, setCustomerId] = useState("");
  const [initialState, setInitialState] = useState("{}");
  const [fieldError, setFieldError] = useState<RunFieldError | null>(null);
  const [submitError, setSubmitError] = useState<string | null>(null);
  const [submitNotice, setSubmitNotice] = useState<string | null>(null);
  const [isSubmitting, setIsSubmitting] = useState(false);

  useEffect(() => {
    let disposed = false;
    let supervisorsLoaded = false;
    let controller: AbortController | null = null;
    let pollTimer: ReturnType<typeof setTimeout> | null = null;

    async function refreshRuns() {
      controller = new AbortController();

      try {
        if (!supervisorsLoaded) {
          const [nextRuns, nextSupervisors] = await Promise.all([
            listRuns({ signal: controller.signal }),
            listSupervisors({ signal: controller.signal }),
          ]);

          if (disposed) {
            return;
          }

          setRuns(nextRuns);
          setSupervisors(nextSupervisors);
          setSupervisorId((currentId) =>
            nextSupervisors.some((supervisor) => supervisor.id === currentId)
              ? currentId
              : "",
          );
          supervisorsLoaded = true;
        } else {
          const nextRuns = await listRuns({ signal: controller.signal });
          if (disposed) {
            return;
          }

          setRuns(nextRuns);
        }

        setLoadError(null);
        setLastSyncedAt(new Date().toISOString());
      } catch (error) {
        if (!disposed && !isAbortError(error)) {
          setLoadError(getApiErrorMessage(error));
        }
      } finally {
        if (!disposed) {
          pollTimer = setTimeout(refreshRuns, POLL_INTERVAL_MS);
        }
      }
    }

    void refreshRuns();

    return () => {
      disposed = true;
      controller?.abort();
      if (pollTimer) {
        clearTimeout(pollTimer);
      }
    };
  }, [refreshVersion]);

  const supervisorNames = useMemo(
    () =>
      new Map(
        (supervisors ?? []).map((supervisor) => [supervisor.id, supervisor.name]),
      ),
    [supervisors],
  );

  const sortedRuns = useMemo(
    () =>
      [...(runs ?? [])].sort(
        (left, right) =>
          Date.parse(right.updated_at) - Date.parse(left.updated_at),
      ),
    [runs],
  );

  const filteredRuns = useMemo(
    () => sortedRuns.filter((run) => runMatchesFilter(run, filter)),
    [filter, sortedRuns],
  );

  const filterCounts = useMemo(
    () =>
      FILTERS.reduce<Record<RunFilter, number>>(
        (counts, option) => {
          counts[option.value] = sortedRuns.filter((run) =>
            runMatchesFilter(run, option.value),
          ).length;
          return counts;
        },
        { all: 0, active: 0, completed: 0, terminated: 0, failed: 0 },
      ),
    [sortedRuns],
  );

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setFieldError(null);
    setSubmitError(null);
    setSubmitNotice(null);

    const normalizedOrderId = orderId.trim();
    const normalizedCustomerId = customerId.trim();
    let parsedInitialState: Record<string, unknown> | undefined;

    if (!normalizedOrderId) {
      setFieldError({
        field: "order_id",
        message: "Enter an order ID before starting the run.",
      });
      return;
    }

    if (!supervisorId) {
      setFieldError({
        field: "supervisor_config_id",
        message: "Select a supervisor configuration before starting the run.",
      });
      return;
    }

    if (initialState.trim()) {
      try {
        const parsed: unknown = JSON.parse(initialState);
        if (!parsed || Array.isArray(parsed) || typeof parsed !== "object") {
          setFieldError({
            field: "initial_state",
            message: "Initial state must be a JSON object, such as {}.",
          });
          return;
        }
        parsedInitialState = parsed as Record<string, unknown>;
      } catch {
        setFieldError({
          field: "initial_state",
          message: "Initial state is not valid JSON. Check commas and quotes.",
        });
        return;
      }
    }

    const orderContext: RunCreate["order_context"] = {};
    if (normalizedCustomerId) {
      orderContext.customer_id = normalizedCustomerId;
    }
    if (parsedInitialState) {
      orderContext.initial_state = parsedInitialState;
    }

    setIsSubmitting(true);

    try {
      const run = await createRun({
        order_id: normalizedOrderId,
        supervisor_config_id: supervisorId,
        order_context: orderContext,
      });
      setSubmitNotice("Run accepted. Opening its durable state now.");
      router.push(`/runs/${encodeURIComponent(run.id)}`);
    } catch (error) {
      setSubmitError(getApiErrorMessage(error));
      setIsSubmitting(false);
    }
  }

  const hasNoSupervisors = supervisors !== null && supervisors.length === 0;
  const initialLoadFailed = runs === null && supervisors === null && loadError;

  return (
    <div className="page-content">
      <header className="page-toolbar">
        <div className="max-w-3xl">
          <h1 className="text-balance text-[clamp(2rem,4vw,3.6rem)] font-semibold leading-[0.98] tracking-[-0.035em] text-[var(--ink)]">
            Order runs
          </h1>
          <p className="mt-4 max-w-2xl text-sm leading-6 text-[var(--muted)]">
            Start one Temporal workflow for an order, then inspect persisted state as
            signals, decisions, actions, and interventions arrive.
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-x-4 gap-y-2 text-xs text-[var(--quiet)]">
          <span>Refreshes every 4 seconds</span>
          {lastSyncedAt ? (
            <span>
              Last synced{" "}
              <time dateTime={lastSyncedAt}>{formatDateTime(lastSyncedAt)}</time>
            </span>
          ) : null}
        </div>
      </header>

      <div className="grid gap-4 lg:grid-cols-12 lg:items-start">
        <section className="panel lg:col-span-5" aria-labelledby="start-run-heading">
          <div className="border-b border-[var(--line-soft)] px-5 py-5 sm:px-6">
            <h2
              className="text-xl font-semibold tracking-[-0.02em] text-[var(--ink)]"
              id="start-run-heading"
            >
              Start a supervision run
            </h2>
            <p className="mt-2 max-w-[58ch] text-sm leading-6 text-[var(--muted)]">
              The API creates the durable workflow and returns its run record. Its
              latest state may take a moment to appear in the ledger.
            </p>
          </div>

          <form className="space-y-5 px-5 py-6 sm:px-6" noValidate onSubmit={handleSubmit}>
            <div className="form-grid">
              <label className="field" htmlFor="run-order-id">
                <span>Order ID</span>
                <input
                  aria-describedby={fieldError?.field === "order_id" ? "run-form-feedback" : undefined}
                  aria-invalid={fieldError?.field === "order_id"}
                  autoComplete="off"
                  className="input"
                  id="run-order-id"
                  name="order_id"
                  onChange={(event) => {
                    setOrderId(event.target.value);
                    if (fieldError?.field === "order_id") {
                      setFieldError(null);
                    }
                  }}
                  placeholder="order-1007"
                  required
                  value={orderId}
                />
              </label>

              <label className="field" htmlFor="run-supervisor">
                <span>Supervisor</span>
                <select
                  aria-describedby={fieldError?.field === "supervisor_config_id" ? "run-form-feedback" : undefined}
                  aria-invalid={fieldError?.field === "supervisor_config_id"}
                  className="select"
                  disabled={supervisors === null || hasNoSupervisors}
                  id="run-supervisor"
                  name="supervisor_config_id"
                  onChange={(event) => {
                    setSupervisorId(event.target.value);
                    if (fieldError?.field === "supervisor_config_id") {
                      setFieldError(null);
                    }
                  }}
                  required
                  value={supervisorId}
                >
                  {supervisors === null ? (
                    <option value="">Loading configurations...</option>
                  ) : hasNoSupervisors ? (
                    <option value="">No configurations available</option>
                  ) : (
                    <option value="">Select a configuration</option>
                  )}
                  {(supervisors ?? []).map((supervisor) => (
                    <option key={supervisor.id} value={supervisor.id}>
                      {supervisor.name} · {supervisor.id.slice(0, 8)}
                    </option>
                  ))}
                </select>
              </label>

              <label className="field sm:col-span-2" htmlFor="run-customer-id">
                <span>Customer ID</span>
                <span className="text-[var(--quiet)]">Optional</span>
                <input
                  autoComplete="off"
                  className="input"
                  id="run-customer-id"
                  name="customer_id"
                  onChange={(event) => setCustomerId(event.target.value)}
                  placeholder="customer-42"
                  value={customerId}
                />
              </label>

              <label className="field sm:col-span-2" htmlFor="run-initial-state">
                <span>Initial order state</span>
                <span className="text-[var(--quiet)]">Optional JSON object</span>
                <textarea
                  aria-describedby={`initial-state-hint${fieldError?.field === "initial_state" ? " run-form-feedback" : ""}`}
                  aria-invalid={fieldError?.field === "initial_state"}
                  className="textarea min-h-32 font-mono text-xs leading-5"
                  id="run-initial-state"
                  name="initial_state"
                  onChange={(event) => {
                    setInitialState(event.target.value);
                    if (fieldError?.field === "initial_state") {
                      setFieldError(null);
                    }
                  }}
                  spellCheck={false}
                  value={initialState}
                />
                <span className="text-xs leading-5 text-[var(--quiet)]" id="initial-state-hint">
                  Leave blank to let the workflow initialize an empty order state.
                </span>
              </label>
            </div>

            <div aria-live="polite" id="run-form-feedback">
              {fieldError || submitError ? (
                <div className="state-message state-message-error" role="alert">
                  <strong>Run not started.</strong>{" "}
                  <span>{fieldError?.message ?? submitError}</span>
                </div>
              ) : null}
              {submitNotice ? (
                <div className="state-message state-message-success" role="status">
                  {submitNotice}
                </div>
              ) : null}
              {hasNoSupervisors ? (
                <div className="state-message">
                  Create a supervisor configuration before starting a run.{" "}
                  <Link className="font-semibold text-[var(--ink)] underline underline-offset-4" href="/supervisors">
                    Open supervisors
                  </Link>
                </div>
              ) : null}
            </div>

            <button
              className="button button-primary w-full sm:w-auto"
              disabled={isSubmitting || hasNoSupervisors || supervisors === null}
              type="submit"
            >
              {isSubmitting ? "Starting run..." : "Start run"}
            </button>
          </form>
        </section>

        <section className="panel min-w-0 overflow-hidden lg:col-span-7" aria-labelledby="run-ledger-heading">
          <div className="flex flex-col gap-4 border-b border-[var(--line-soft)] px-5 py-5 sm:px-6">
            <div className="flex flex-wrap items-end justify-between gap-3">
              <div>
                <h2
                  className="text-xl font-semibold tracking-[-0.02em] text-[var(--ink)]"
                  id="run-ledger-heading"
                >
                  Run ledger
                </h2>
                <p className="mt-1 text-sm text-[var(--muted)]">
                  Persisted runs, newest activity first.
                </p>
              </div>
              {runs ? (
                <span className="text-xs tabular-nums text-[var(--quiet)]">
                  {runs.length} {runs.length === 1 ? "run" : "runs"}
                </span>
              ) : null}
            </div>

            <div className="flex flex-wrap gap-1.5" role="group" aria-label="Filter runs">
              {FILTERS.map((option) => (
                <button
                  aria-pressed={filter === option.value}
                  className={`button min-h-8 px-3 py-1.5 text-xs ${
                    filter === option.value ? "button-secondary" : "text-[var(--muted)]"
                  }`}
                  key={option.value}
                  onClick={() => setFilter(option.value)}
                  type="button"
                >
                  {option.label}
                  <span className="ml-2 tabular-nums text-[var(--quiet)]">
                    {filterCounts[option.value]}
                  </span>
                </button>
              ))}
            </div>
          </div>

          {loadError && runs !== null ? (
            <div className="border-b border-[var(--line-soft)] px-5 py-4 sm:px-6">
              <div className="state-message state-message-error" role="status">
                <span>Refresh failed. Existing rows remain visible. {loadError}</span>
                <button
                  className="button ml-auto shrink-0"
                  onClick={() => setRefreshVersion((version) => version + 1)}
                  type="button"
                >
                  Retry
                </button>
              </div>
            </div>
          ) : null}

          {initialLoadFailed ? (
            <div className="px-5 py-14 sm:px-6">
              <div className="state-message state-message-error" role="alert">
                <div>
                  <strong>Runs could not be loaded.</strong>
                  <p className="mt-1 text-sm">{loadError}</p>
                </div>
                <button
                  className="button ml-auto shrink-0"
                  onClick={() => setRefreshVersion((version) => version + 1)}
                  type="button"
                >
                  Retry
                </button>
              </div>
            </div>
          ) : runs === null ? (
            <RunsSkeleton />
          ) : filteredRuns.length === 0 ? (
            <div className="px-6 py-16 text-center">
              <h3 className="text-base font-semibold text-[var(--ink)]">
                {runs.length === 0
                  ? "No runs have started"
                  : `No ${filter} runs right now`}
              </h3>
              <p className="mx-auto mt-2 max-w-md text-sm leading-6 text-[var(--muted)]">
                {runs.length === 0
                  ? "Complete the launch form to create the first durable order workflow."
                  : "Choose another filter or wait for workflow state to change on the next refresh."}
              </p>
            </div>
          ) : (
            <div
              aria-label="Runs table. Scroll horizontally to see all columns."
              className="overflow-x-auto focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-[-2px] focus-visible:outline-[var(--accent)]"
              tabIndex={0}
            >
              <table className="w-full min-w-[760px] border-collapse text-left text-sm">
                <thead>
                  <tr className="border-b border-[var(--line-soft)] text-[0.6875rem] font-semibold text-[var(--quiet)]">
                    <th className="px-5 py-3" scope="col">Order and run</th>
                    <th className="px-4 py-3" scope="col">Supervisor</th>
                    <th className="px-4 py-3" scope="col">State</th>
                    <th className="px-4 py-3" scope="col">Next wake</th>
                    <th className="px-4 py-3" scope="col">Timing</th>
                    <th className="px-5 py-3 text-right" scope="col">Open</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-[var(--line-soft)]">
                  {filteredRuns.map((run) => (
                    <tr className="transition-colors hover:bg-white/[0.025]" key={run.id}>
                      <td className="px-5 py-4 align-middle">
                        <div className="max-w-52 break-words font-semibold text-[var(--ink)]">
                          {run.order_id}
                        </div>
                        <div className="mt-1 font-mono text-[0.6875rem] text-[var(--quiet)]">
                          {run.id.slice(0, 8)}
                        </div>
                      </td>
                      <td className="max-w-44 px-4 py-4 align-middle text-[var(--muted)]">
                        <span className="line-clamp-2">
                          {supervisorNames.get(run.supervisor_config_id) ?? "Unknown supervisor"}
                        </span>
                      </td>
                      <td className="px-4 py-4 align-middle">
                        <StatusBadge status={run.status} />
                        <p className="mt-2 max-w-40 text-xs leading-5 text-[var(--quiet)]">
                          {getOrderStateSummary(run.current_order_state)}
                        </p>
                      </td>
                      <td className="whitespace-nowrap px-4 py-4 align-middle tabular-nums text-[var(--muted)]">
                        {run.next_wake_at ? formatDateTime(run.next_wake_at) : "Not scheduled"}
                      </td>
                      <td className="whitespace-nowrap px-4 py-4 align-middle text-xs tabular-nums text-[var(--muted)]">
                        <span className="block text-[var(--quiet)]">Started</span>
                        <time className="mt-1 block" dateTime={run.started_at}>{formatDateTime(run.started_at)}</time>
                        {run.completed_at ? (
                          <>
                            <span className="mt-2 block text-[var(--quiet)]">Completed</span>
                            <time className="mt-1 block" dateTime={run.completed_at}>{formatDateTime(run.completed_at)}</time>
                          </>
                        ) : null}
                      </td>
                      <td className="px-5 py-4 text-right align-middle">
                        <Link
                          className="button button-secondary"
                          href={`/runs/${encodeURIComponent(run.id)}`}
                        >
                          Inspect
                        </Link>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </section>
      </div>
    </div>
  );
}
