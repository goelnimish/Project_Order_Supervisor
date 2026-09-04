"use client";

import { useState } from "react";
import type { FormEvent } from "react";

import { getApiErrorMessage } from "@/lib/api/client";
import type {
  InterruptRunRequest,
  PauseRunRequest,
  TerminateRunRequest,
  WorkflowStatus,
} from "@/lib/api/types";
import { humanizeIdentifier } from "@/lib/format";
import { getRunControlAvailability } from "@/lib/run-status";

export type LifecycleAction = "pause" | "resume" | "interrupt" | "terminate";

type ReasonedLifecycleAction = Exclude<LifecycleAction, "resume">;

interface OpenEditor {
  action: ReasonedLifecycleAction;
  openedAtStatus: WorkflowStatus;
}

export interface LifecycleControlsProps {
  status: WorkflowStatus;
  pendingAction: LifecycleAction | null;
  onPause: (request: PauseRunRequest) => Promise<void>;
  onResume: () => Promise<void>;
  onInterrupt: (request: InterruptRunRequest) => Promise<void>;
  onTerminate: (request: TerminateRunRequest) => Promise<void>;
}

export function LifecycleControls({
  status,
  pendingAction,
  onPause,
  onResume,
  onInterrupt,
  onTerminate,
}: LifecycleControlsProps) {
  const [openEditor, setOpenEditor] = useState<OpenEditor | null>(null);
  const [pauseReason, setPauseReason] = useState("");
  const [interruptReason, setInterruptReason] = useState("");
  const [terminateReason, setTerminateReason] = useState("");
  const [fieldError, setFieldError] = useState<string | null>(null);
  const [requestError, setRequestError] = useState<string | null>(null);
  const [localPendingAction, setLocalPendingAction] =
    useState<LifecycleAction | null>(null);

  const availability = getRunControlAvailability(status);
  const activePendingAction = pendingAction ?? localPendingAction;
  const isBusy = activePendingAction !== null;
  const visibleEditor =
    openEditor &&
    openEditor.openedAtStatus === status &&
    availability[openEditor.action]
      ? openEditor.action
      : null;

  function chooseAction(action: ReasonedLifecycleAction) {
    if (!availability[action] || isBusy) {
      return;
    }

    setOpenEditor({ action, openedAtStatus: status });
    setFieldError(null);
    setRequestError(null);
  }

  function closeEditor() {
    setOpenEditor(null);
    setFieldError(null);
    setRequestError(null);
  }

  async function submitAction(
    action: LifecycleAction,
    request: () => Promise<void>,
  ) {
    setFieldError(null);
    setRequestError(null);
    setLocalPendingAction(action);

    try {
      await request();
      setOpenEditor(null);
      if (action === "pause") {
        setPauseReason("");
      } else if (action === "interrupt") {
        setInterruptReason("");
      } else if (action === "terminate") {
        setTerminateReason("");
      }
    } catch (error) {
      setRequestError(
        getApiErrorMessage(
          error,
          `The ${action} signal could not be sent. Check the current run state, then try again.`,
        ),
      );
    } finally {
      setLocalPendingAction(null);
    }
  }

  async function handlePause(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const reason = pauseReason.trim();
    await submitAction("pause", () => onPause(reason ? { reason } : {}));
  }

  async function handleResume() {
    if (!availability.resume || isBusy) {
      return;
    }
    await submitAction("resume", onResume);
  }

  async function handleInterrupt(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const reason = interruptReason.trim();
    if (!reason) {
      setFieldError("Enter why this run must be interrupted.");
      return;
    }
    await submitAction("interrupt", () => onInterrupt({ reason }));
  }

  async function handleTerminate(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const reason = terminateReason.trim();
    if (!reason) {
      setFieldError("Enter why this run must be terminated.");
      return;
    }
    await submitAction("terminate", () => onTerminate({ reason }));
  }

  return (
    <section
      aria-labelledby="lifecycle-controls-title"
      className="rounded-[var(--radius-panel)] border border-[var(--line)] bg-[var(--panel)]"
    >
      <div className="border-b border-[var(--line-soft)] px-4 py-4 sm:px-5">
        <h2
          className="text-sm font-semibold tracking-[-0.01em] text-[var(--ink)]"
          id="lifecycle-controls-title"
        >
          Workflow lifecycle
        </h2>
        <p className="mt-1 max-w-[65ch] text-xs leading-5 text-[var(--muted)]">
          Pause automated work, resume it, interrupt for review, or end the workflow.
        </p>
      </div>

      <div className="p-4 sm:p-5">
        <div className="grid grid-cols-2 gap-2" role="group" aria-label="Workflow lifecycle actions">
          <button
            aria-pressed={visibleEditor === "pause"}
            className="button button-secondary w-full"
            disabled={!availability.pause || isBusy}
            onClick={() => chooseAction("pause")}
            type="button"
          >
            Pause
          </button>
          <button
            className="button button-primary w-full"
            disabled={!availability.resume || isBusy}
            onClick={() => void handleResume()}
            type="button"
          >
            {activePendingAction === "resume" ? "Resuming..." : "Resume"}
          </button>
          <button
            aria-pressed={visibleEditor === "interrupt"}
            className="button button-secondary w-full"
            disabled={!availability.interrupt || isBusy}
            onClick={() => chooseAction("interrupt")}
            type="button"
          >
            Interrupt for human review
          </button>
          <button
            aria-pressed={visibleEditor === "terminate"}
            className="button button-danger w-full"
            disabled={!availability.terminate || isBusy}
            onClick={() => chooseAction("terminate")}
            type="button"
          >
            Terminate
          </button>
        </div>

        {visibleEditor === "pause" ? (
          <form className="mt-4 border-t border-[var(--line-soft)] pt-4" onSubmit={handlePause}>
            <div className="field">
              <label htmlFor="pause-run-reason">Pause reason (optional)</label>
              <textarea
                className="textarea min-h-20"
                disabled={isBusy}
                id="pause-run-reason"
                onChange={(event) => {
                  setPauseReason(event.target.value);
                  setRequestError(null);
                }}
                placeholder="Waiting for operator review."
                value={pauseReason}
              />
            </div>
            <div className="mt-3 grid grid-cols-2 gap-2">
              <button className="button button-primary w-full" disabled={isBusy} type="submit">
                {activePendingAction === "pause" ? "Pausing..." : "Confirm pause"}
              </button>
              <button className="button button-secondary w-full" disabled={isBusy} onClick={closeEditor} type="button">
                Cancel
              </button>
            </div>
          </form>
        ) : null}

        {visibleEditor === "interrupt" ? (
          <form className="mt-4 border-t border-[var(--line-soft)] pt-4" onSubmit={handleInterrupt}>
            <div className="field">
              <label htmlFor="interrupt-run-reason">Interrupt reason</label>
              <textarea
                aria-describedby={fieldError ? "interrupt-run-reason-error" : undefined}
                aria-invalid={Boolean(fieldError)}
                className="textarea min-h-20"
                disabled={isBusy}
                id="interrupt-run-reason"
                onChange={(event) => {
                  setInterruptReason(event.target.value);
                  setFieldError(null);
                  setRequestError(null);
                }}
                placeholder="Review the payment exception before more actions execute."
                required
                value={interruptReason}
              />
              {fieldError ? (
                <p className="text-xs leading-5 text-[#ffd3ce]" id="interrupt-run-reason-error" role="alert">
                  {fieldError}
                </p>
              ) : null}
            </div>
            <div className="mt-3 grid grid-cols-2 gap-2">
              <button className="button button-primary w-full" disabled={isBusy} type="submit">
                {activePendingAction === "interrupt" ? "Interrupting..." : "Confirm interrupt"}
              </button>
              <button className="button button-secondary w-full" disabled={isBusy} onClick={closeEditor} type="button">
                Cancel
              </button>
            </div>
          </form>
        ) : null}

        {visibleEditor === "terminate" ? (
          <form className="mt-4 border-t border-[#744743] pt-4" onSubmit={handleTerminate}>
            <h3 className="text-sm font-semibold text-[#ffd3ce]">Confirm workflow termination</h3>
            <p className="mt-1 text-xs leading-5 text-[#efaaa2]">
              Termination is final. Temporal will stop this workflow after it durably accepts the signal.
            </p>
            <div className="field mt-3">
              <label htmlFor="terminate-run-reason">Termination reason</label>
              <textarea
                aria-describedby={fieldError ? "terminate-run-reason-error" : "terminate-run-reason-help"}
                aria-invalid={Boolean(fieldError)}
                className="textarea min-h-20"
                disabled={isBusy}
                id="terminate-run-reason"
                onChange={(event) => {
                  setTerminateReason(event.target.value);
                  setFieldError(null);
                  setRequestError(null);
                }}
                placeholder="Operator ended the run after the order was cancelled."
                required
                value={terminateReason}
              />
              {fieldError ? (
                <p className="text-xs leading-5 text-[#ffd3ce]" id="terminate-run-reason-error" role="alert">
                  {fieldError}
                </p>
              ) : (
                <p className="text-xs leading-5 text-[var(--quiet)]" id="terminate-run-reason-help">
                  This reason is written to the durable audit trail.
                </p>
              )}
            </div>
            <div className="mt-3 grid grid-cols-2 gap-2">
              <button className="button button-danger w-full" disabled={isBusy} type="submit">
                {activePendingAction === "terminate" ? "Terminating..." : "Confirm termination"}
              </button>
              <button className="button button-secondary w-full" disabled={isBusy} onClick={closeEditor} type="button">
                Keep run active
              </button>
            </div>
          </form>
        ) : null}

        <div aria-live="polite" className="mt-3 min-h-5 text-xs leading-5">
          {activePendingAction ? (
            <p className="text-[var(--warning)]">
              {humanizeIdentifier(activePendingAction)} accepted. Waiting for durable state...
            </p>
          ) : requestError ? (
            <p className="text-[#ffd3ce]" role="alert">
              {requestError}
            </p>
          ) : !Object.values(availability).some(Boolean) ? (
            <p className="text-[var(--quiet)]">
              This {humanizeIdentifier(status).toLowerCase()} run has no available lifecycle controls.
            </p>
          ) : (
            <p className="text-[var(--quiet)]">
              Controls are enabled according to the workflow&apos;s current durable state.
            </p>
          )}
        </div>
      </div>
    </section>
  );
}
