"use client";

import { useRef, useState } from "react";
import type { FormEvent } from "react";

import { getApiErrorMessage } from "@/lib/api/client";
import {
  KNOWN_ORDER_EVENT_TYPES,
  type JsonObject,
  type KnownOrderEventType,
  type OrderEventRequest,
  type WorkflowStatus,
} from "@/lib/api/types";
import { humanizeIdentifier } from "@/lib/format";
import { getRunControlAvailability } from "@/lib/run-status";

export interface EventInjectorProps {
  status: WorkflowStatus;
  isMutationPending: boolean;
  persistedEventIds: string[];
  onInject: (request: OrderEventRequest) => Promise<void>;
}

function parsePayload(value: string): JsonObject | undefined {
  if (!value.trim()) {
    return undefined;
  }

  const parsed: unknown = JSON.parse(value);
  if (!parsed || Array.isArray(parsed) || typeof parsed !== "object") {
    throw new Error("Payload must be a JSON object.");
  }

  return parsed as JsonObject;
}

export function EventInjector({
  status,
  isMutationPending,
  persistedEventIds,
  onInject,
}: EventInjectorProps) {
  const [eventType, setEventType] =
    useState<KnownOrderEventType>("order_created");
  const [payloadText, setPayloadText] = useState("");
  const [payloadError, setPayloadError] = useState<string | null>(null);
  const [requestError, setRequestError] = useState<string | null>(null);
  const [acceptedEventId, setAcceptedEventId] = useState<string | null>(null);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const eventIdRef = useRef<string | null>(null);
  const occurredAtRef = useRef<string | null>(null);

  const isAvailable = getRunControlAvailability(status).event;
  const isAwaitingPersistence =
    acceptedEventId !== null && !persistedEventIds.includes(acceptedEventId);
  const isDisabled =
    !isAvailable || isMutationPending || isSubmitting || isAwaitingPersistence;

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setPayloadError(null);
    setRequestError(null);

    let payload: JsonObject | undefined;
    try {
      payload = parsePayload(payloadText);
    } catch {
      setPayloadError(
        "Use a JSON object such as {\"reason\":\"carrier delay\"}, or leave this field empty.",
      );
      return;
    }

    const eventId = eventIdRef.current ?? crypto.randomUUID();
    const occurredAt = occurredAtRef.current ?? new Date().toISOString();
    eventIdRef.current = eventId;
    occurredAtRef.current = occurredAt;
    setIsSubmitting(true);

    try {
      await onInject({
        event_id: eventId,
        event_type: eventType,
        occurred_at: occurredAt,
        ...(payload ? { payload } : {}),
      });
      eventIdRef.current = null;
      occurredAtRef.current = null;
      setPayloadText("");
      setAcceptedEventId(eventId);
    } catch (error) {
      setRequestError(
        getApiErrorMessage(
          error,
          "The event could not be sent. Check the backend connection, then retry. The same event key will be reused.",
        ),
      );
    } finally {
      setIsSubmitting(false);
    }
  }

  return (
    <section
      aria-labelledby="event-injector-title"
      className="rounded-[var(--radius-panel)] border border-[var(--line)] bg-[var(--panel)]"
    >
      <div className="border-b border-[var(--line-soft)] px-4 py-4 sm:px-5">
        <h2
          className="text-sm font-semibold tracking-[-0.01em] text-[var(--ink)]"
          id="event-injector-title"
        >
          Inject order event
        </h2>
        <p className="mt-1 max-w-[65ch] text-xs leading-5 text-[var(--muted)]">
          Send a known event into the running workflow as a Temporal signal.
        </p>
      </div>

      <form className="p-4 sm:p-5" onSubmit={handleSubmit}>
        <fieldset
          className="grid gap-4 border-0 p-0"
          disabled={isDisabled}
        >
          <legend className="sr-only">Order event details</legend>

          <div className="field">
            <label htmlFor="order-event-type">Event type</label>
            <select
              className="select"
              id="order-event-type"
              onChange={(event) => {
                setEventType(event.target.value as KnownOrderEventType);
                setRequestError(null);
              }}
              value={eventType}
            >
              {KNOWN_ORDER_EVENT_TYPES.map((option) => (
                <option key={option} value={option}>
                  {humanizeIdentifier(option)}
                </option>
              ))}
            </select>
          </div>

          <div className="field">
            <label htmlFor="order-event-payload">Payload (optional JSON object)</label>
            <textarea
              aria-describedby={payloadError ? "order-event-payload-error" : "order-event-payload-help"}
              aria-invalid={Boolean(payloadError)}
              className="textarea min-h-24 font-mono text-xs leading-5"
              id="order-event-payload"
              onChange={(event) => {
                setPayloadText(event.target.value);
                setPayloadError(null);
                setRequestError(null);
              }}
              placeholder={'{"reason":"carrier delay"}'}
              spellCheck={false}
              value={payloadText}
            />
            {payloadError ? (
              <p
                className="text-xs leading-5 text-[#ffd3ce]"
                id="order-event-payload-error"
                role="alert"
              >
                {payloadError}
              </p>
            ) : (
              <p className="text-xs leading-5 text-[var(--quiet)]" id="order-event-payload-help">
                Occurrence time is captured when you send. A failed request keeps its event key for a safe retry.
              </p>
            )}
          </div>

          <button className="button button-primary w-full" type="submit">
            {isSubmitting ? "Sending event..." : "Send event"}
          </button>
        </fieldset>

        <div aria-live="polite" className="mt-3 min-h-5 text-xs leading-5">
          {!isAvailable ? (
            <p className="text-[var(--quiet)]">
              This {humanizeIdentifier(status).toLowerCase()} run no longer accepts events.
            </p>
          ) : isMutationPending || isAwaitingPersistence ? (
            <p className="text-[var(--warning)]">
              {isAwaitingPersistence
                ? "Accepted. Event input will unlock when the persisted timeline records this event."
                : "A signal is awaiting durable state. Event input will unlock when it is recorded."}
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
