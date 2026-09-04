"use client";

import { useState } from "react";
import type { FormEvent } from "react";

import { getApiErrorMessage } from "@/lib/api/client";
import type { InstructionRequest, WorkflowStatus } from "@/lib/api/types";
import { humanizeIdentifier } from "@/lib/format";
import { getRunControlAvailability } from "@/lib/run-status";

export interface InstructionFormProps {
  status: WorkflowStatus;
  isMutationPending: boolean;
  retainedInstructionCount: number;
  onAddInstruction: (request: InstructionRequest) => Promise<void>;
}

export function InstructionForm({
  status,
  isMutationPending,
  retainedInstructionCount,
  onAddInstruction,
}: InstructionFormProps) {
  const [instruction, setInstruction] = useState("");
  const [fieldError, setFieldError] = useState<string | null>(null);
  const [requestError, setRequestError] = useState<string | null>(null);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [acceptedAtCount, setAcceptedAtCount] = useState<number | null>(null);

  const isAvailable = getRunControlAvailability(status).instruction;
  const isAwaitingPersistence =
    acceptedAtCount !== null && retainedInstructionCount <= acceptedAtCount;
  const isDisabled =
    !isAvailable || isMutationPending || isSubmitting || isAwaitingPersistence;

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setFieldError(null);
    setRequestError(null);

    const normalizedInstruction = instruction.trim();
    if (!normalizedInstruction) {
      setFieldError("Enter an instruction for the supervisor before sending it.");
      return;
    }

    setIsSubmitting(true);
    try {
      const instructionCountBeforeRequest = retainedInstructionCount;
      await onAddInstruction({ instruction: normalizedInstruction });
      setAcceptedAtCount(instructionCountBeforeRequest);
      setInstruction("");
    } catch (error) {
      setRequestError(
        getApiErrorMessage(
          error,
          "The instruction could not be sent. Check the backend connection, then try again.",
        ),
      );
    } finally {
      setIsSubmitting(false);
    }
  }

  return (
    <section
      aria-labelledby="instruction-form-title"
      className="rounded-[var(--radius-panel)] border border-[var(--line)] bg-[var(--panel)]"
    >
      <div className="border-b border-[var(--line-soft)] px-4 py-4 sm:px-5">
        <h2
          className="text-sm font-semibold tracking-[-0.01em] text-[var(--ink)]"
          id="instruction-form-title"
        >
          Add live instruction
        </h2>
        <p className="mt-1 max-w-[65ch] text-xs leading-5 text-[var(--muted)]">
          Add operator context without replacing the supervisor&apos;s base instruction.
        </p>
      </div>

      <form className="p-4 sm:p-5" onSubmit={handleSubmit}>
        <fieldset className="grid gap-4 border-0 p-0" disabled={isDisabled}>
          <legend className="sr-only">Live supervisor instruction</legend>
          <div className="field">
            <label htmlFor="live-run-instruction">Instruction</label>
            <textarea
              aria-describedby={fieldError ? "live-run-instruction-error" : "live-run-instruction-help"}
              aria-invalid={Boolean(fieldError)}
              className="textarea min-h-28"
              id="live-run-instruction"
              onChange={(event) => {
                setInstruction(event.target.value);
                setFieldError(null);
                setRequestError(null);
              }}
              placeholder="For this order, prioritize speed over cost."
              value={instruction}
            />
            {fieldError ? (
              <p
                className="text-xs leading-5 text-[#ffd3ce]"
                id="live-run-instruction-error"
                role="alert"
              >
                {fieldError}
              </p>
            ) : (
              <p className="text-xs leading-5 text-[var(--quiet)]" id="live-run-instruction-help">
                The instruction is retained in compact run context and appears in the audit timeline.
              </p>
            )}
          </div>

          <button className="button button-primary w-full" type="submit">
            {isSubmitting ? "Sending instruction..." : "Add instruction"}
          </button>
        </fieldset>

        <div aria-live="polite" className="mt-3 min-h-5 text-xs leading-5">
          {!isAvailable ? (
            <p className="text-[var(--quiet)]">
              This {humanizeIdentifier(status).toLowerCase()} run no longer accepts instructions.
            </p>
          ) : isMutationPending || isAwaitingPersistence ? (
            <p className="text-[var(--warning)]">
              {isAwaitingPersistence
                ? "Accepted. Instruction input will unlock when the retained instruction is persisted."
                : "A signal is awaiting durable state. Instruction input will unlock when it is recorded."}
            </p>
          ) : requestError ? (
            <p className="text-[#ffd3ce]" role="alert">
              {requestError}
            </p>
          ) : null}
        </div>
      </form>
    </section>
  );
}
