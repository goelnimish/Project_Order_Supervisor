"use client";

import { useCallback, useEffect, useState } from "react";
import type { FormEvent } from "react";

import {
  getApiErrorMessage,
  isAbortError,
} from "@/lib/api/client";
import {
  createSupervisor,
  listSupervisors,
} from "@/lib/api/supervisors";
import {
  BUSINESS_ACTION_NAMES,
  type BusinessActionName,
  type Supervisor,
  type SupervisorCreate,
  type WakeAggressiveness,
} from "@/lib/api/types";
import {
  formatBusinessActionName,
  formatDateTime,
  humanizeIdentifier,
} from "@/lib/format";

const DEFAULT_WAKE_SECONDS = "300";
const DEFAULT_TEMPERATURE = "0";

type Provider = "deterministic" | "ollama";

type FieldErrors = {
  name?: string;
  baseInstruction?: string;
  availableActions?: string;
  defaultWakeSeconds?: string;
  temperature?: string;
};

function providerLabel(provider: string | null | undefined) {
  if (!provider) {
    return "Environment default";
  }

  if (provider === "none" || provider === "deterministic") {
    return "Deterministic";
  }

  return humanizeIdentifier(provider);
}

function modelLabel(supervisor: Supervisor) {
  const provider = supervisor.model_config.provider;

  if (!provider) {
    return "Environment default";
  }

  if (provider === "none" || provider === "deterministic") {
    return "Built-in rules";
  }

  return supervisor.model_config.model ?? "Default model";
}

function SupervisorListSkeleton() {
  return (
    <div aria-busy="true" className="space-y-0" role="status">
      <span className="sr-only">Loading supervisor configurations.</span>
      {[0, 1, 2].map((row) => (
        <div
          aria-hidden="true"
          className="border-t border-[var(--line-soft)] px-5 py-5 first:border-t-0"
          key={row}
        >
          <div className="h-4 w-40 animate-pulse rounded bg-white/10" />
          <div className="mt-3 h-3 w-full max-w-lg animate-pulse rounded bg-white/[0.07]" />
          <div className="mt-2 h-3 w-3/5 animate-pulse rounded bg-white/[0.07]" />
          <div className="mt-5 grid grid-cols-2 gap-3 sm:grid-cols-4">
            {[0, 1, 2, 3].map((cell) => (
              <div
                className="h-10 animate-pulse rounded-lg bg-white/[0.06]"
                key={cell}
              />
            ))}
          </div>
        </div>
      ))}
    </div>
  );
}

function SupervisorRecord({ supervisor }: { supervisor: Supervisor }) {
  const temperature = supervisor.model_config.temperature;
  const hasLongInstruction = supervisor.base_instruction.length > 280;
  const instructionPreview = hasLongInstruction
    ? `${supervisor.base_instruction.slice(0, 277).trimEnd()}...`
    : supervisor.base_instruction;

  return (
    <article className="border-t border-[var(--line-soft)] px-5 py-5 first:border-t-0 sm:px-6">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
        <div className="min-w-0">
          <h3 className="break-words text-base font-semibold tracking-[-0.015em] text-[var(--ink)]">
            {supervisor.name}
          </h3>
          <p className="mt-1 break-all font-mono text-[11px] leading-5 text-[var(--quiet)]">
            {supervisor.id}
          </p>
        </div>
        <p className="shrink-0 text-xs text-[var(--muted)]">
          Created {formatDateTime(supervisor.created_at)}
        </p>
      </div>

      <p className="mt-4 max-w-[72ch] break-words whitespace-pre-wrap text-sm leading-6 text-[var(--muted)]">
        {instructionPreview}
      </p>
      {hasLongInstruction ? (
        <details className="mt-2 text-sm text-[var(--muted)]">
          <summary className="w-fit cursor-pointer text-xs font-medium text-[var(--accent)] underline decoration-transparent underline-offset-4 transition-colors hover:decoration-current">
            Read full instruction
          </summary>
          <p className="mt-3 max-w-[72ch] break-words whitespace-pre-wrap rounded-lg bg-[var(--surface-strong)] p-4 leading-6">
            {supervisor.base_instruction}
          </p>
        </details>
      ) : null}

      <dl className="mt-5 grid gap-px overflow-hidden rounded-lg bg-[var(--line-soft)] sm:grid-cols-2 xl:grid-cols-4">
        <div className="bg-[var(--surface-strong)] px-3.5 py-3">
          <dt className="text-xs text-[var(--quiet)]">Wake cadence</dt>
          <dd className="mt-1 font-mono text-sm tabular-nums text-[var(--ink)]">
            {supervisor.default_wake_seconds}s
          </dd>
        </div>
        <div className="bg-[var(--surface-strong)] px-3.5 py-3">
          <dt className="text-xs text-[var(--quiet)]">Wake aggressiveness</dt>
          <dd className="mt-1 text-sm text-[var(--ink)]">
            {humanizeIdentifier(supervisor.wake_aggressiveness)}
          </dd>
        </div>
        <div className="bg-[var(--surface-strong)] px-3.5 py-3">
          <dt className="text-xs text-[var(--quiet)]">Provider</dt>
          <dd className="mt-1 text-sm text-[var(--ink)]">
            {providerLabel(supervisor.model_config.provider)}
          </dd>
        </div>
        <div className="bg-[var(--surface-strong)] px-3.5 py-3">
          <dt className="text-xs text-[var(--quiet)]">Model</dt>
          <dd className="mt-1 truncate text-sm text-[var(--ink)]" title={modelLabel(supervisor)}>
            {modelLabel(supervisor)}
            {temperature === null ? "" : ` at ${temperature}`}
          </dd>
        </div>
      </dl>

      <div className="mt-5">
        <p className="text-xs text-[var(--quiet)]">
          Allowed actions ({supervisor.available_actions.length})
        </p>
        <ul className="mt-2 flex flex-wrap gap-1.5" aria-label="Allowed business actions">
          {supervisor.available_actions.map((action) => (
            <li
              className="rounded-md border border-[var(--line-soft)] bg-white/[0.025] px-2 py-1 text-xs text-[var(--muted)]"
              key={action}
            >
              {formatBusinessActionName(action)}
            </li>
          ))}
        </ul>
      </div>
    </article>
  );
}

export function SupervisorsWorkspace() {
  const [supervisors, setSupervisors] = useState<Supervisor[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState<string | null>(null);
  const [successMessage, setSuccessMessage] = useState<string | null>(null);

  const [name, setName] = useState("");
  const [baseInstruction, setBaseInstruction] = useState("");
  const [selectedActions, setSelectedActions] = useState<BusinessActionName[]>(
    [...BUSINESS_ACTION_NAMES],
  );
  const [defaultWakeSeconds, setDefaultWakeSeconds] = useState(
    DEFAULT_WAKE_SECONDS,
  );
  const [wakeAggressiveness, setWakeAggressiveness] =
    useState<WakeAggressiveness>("moderate");
  const [provider, setProvider] = useState<Provider>("deterministic");
  const [temperature, setTemperature] = useState(DEFAULT_TEMPERATURE);
  const [fieldErrors, setFieldErrors] = useState<FieldErrors>({});

  const loadSupervisors = useCallback(async (signal?: AbortSignal) => {
    try {
      const result = await listSupervisors({ signal });
      setSupervisors(result);
    } catch (error) {
      if (!isAbortError(error)) {
        setLoadError(
          getApiErrorMessage(
            error,
            "Supervisor configurations could not be loaded. Check that the backend and PostgreSQL are running, then retry.",
          ),
        );
      }
    } finally {
      if (!signal?.aborted) {
        setIsLoading(false);
      }
    }
  }, []);

  useEffect(() => {
    const controller = new AbortController();

    void listSupervisors({ signal: controller.signal })
      .then((result) => {
        setSupervisors(result);
      })
      .catch((error: unknown) => {
        if (!isAbortError(error)) {
          setLoadError(
            getApiErrorMessage(
              error,
              "Supervisor configurations could not be loaded. Check that the backend and PostgreSQL are running, then retry.",
            ),
          );
        }
      })
      .finally(() => {
        if (!controller.signal.aborted) {
          setIsLoading(false);
        }
      });

    return () => controller.abort();
  }, []);

  function refreshSupervisors() {
    setIsLoading(true);
    setLoadError(null);
    void loadSupervisors();
  }

  function toggleAction(action: BusinessActionName) {
    setSelectedActions((current) => {
      const next = new Set(current);

      if (next.has(action)) {
        next.delete(action);
      } else {
        next.add(action);
      }

      return BUSINESS_ACTION_NAMES.filter((candidate) => next.has(candidate));
    });
    setFieldErrors((current) => ({ ...current, availableActions: undefined }));
  }

  function validateForm() {
    const nextErrors: FieldErrors = {};
    const wakeSeconds = Number(defaultWakeSeconds);
    const parsedTemperature = Number(temperature);

    if (!name.trim()) {
      nextErrors.name = "Enter a name for this supervisor configuration.";
    }

    if (!baseInstruction.trim()) {
      nextErrors.baseInstruction = "Enter the supervisor's base instruction.";
    }

    if (selectedActions.length === 0) {
      nextErrors.availableActions = "Select at least one allowed action.";
    }

    if (
      !Number.isInteger(wakeSeconds) ||
      wakeSeconds < 1 ||
      wakeSeconds > 86_400
    ) {
      nextErrors.defaultWakeSeconds =
        "Use a whole number from 1 to 86,400 seconds.";
    }

    if (
      provider === "ollama" &&
      (temperature.trim() === "" ||
        !Number.isFinite(parsedTemperature) ||
        parsedTemperature < 0 ||
        parsedTemperature > 2)
    ) {
      nextErrors.temperature = "Use a temperature from 0 to 2.";
    }

    setFieldErrors(nextErrors);
    return Object.keys(nextErrors).length === 0;
  }

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setSubmitError(null);
    setSuccessMessage(null);

    if (!validateForm()) {
      return;
    }

    const payload: SupervisorCreate = {
      name: name.trim(),
      base_instruction: baseInstruction.trim(),
      available_actions: selectedActions,
      default_wake_seconds: Number(defaultWakeSeconds),
      wake_aggressiveness: wakeAggressiveness,
      model_config:
        provider === "ollama"
          ? {
              provider,
              model: "qwen3:1.7b",
              temperature: Number(temperature),
            }
          : { provider },
    };

    setIsSubmitting(true);

    try {
      const created = await createSupervisor(payload);
      setSupervisors((current) => [...current, created]);
      setName("");
      setBaseInstruction("");
      setSelectedActions([...BUSINESS_ACTION_NAMES]);
      setDefaultWakeSeconds(DEFAULT_WAKE_SECONDS);
      setWakeAggressiveness("moderate");
      setProvider("deterministic");
      setTemperature(DEFAULT_TEMPERATURE);
      setFieldErrors({});
      setSuccessMessage(`${created.name} is ready to supervise new runs.`);
    } catch (error) {
      setSubmitError(
        getApiErrorMessage(
          error,
          "The supervisor configuration could not be created. Review the fields and try again.",
        ),
      );
    } finally {
      setIsSubmitting(false);
    }
  }

  const isInitialLoading = isLoading && supervisors.length === 0 && !loadError;

  return (
    <div className="min-w-0">
      <header className="page-toolbar">
        <div className="max-w-[64ch]">
          <h1 className="text-2xl font-semibold tracking-[-0.025em] text-[var(--ink)] sm:text-3xl">
            Supervisor configurations
          </h1>
          <p className="mt-2 text-sm leading-6 text-[var(--muted)]">
            Define reusable instructions, action permissions, wake behavior, and the inference runtime for future order runs.
          </p>
        </div>
        <div className="flex shrink-0 items-center gap-3">
          <p className="font-mono text-xs tabular-nums text-[var(--muted)]">
            {supervisors.length} {supervisors.length === 1 ? "configuration" : "configurations"}
          </p>
          <button
            className="button button-secondary"
            disabled={isLoading}
            onClick={refreshSupervisors}
            type="button"
          >
            {isLoading ? "Refreshing..." : "Refresh"}
          </button>
        </div>
      </header>

      <div className="page-content">
        <div className="grid items-start gap-4 xl:grid-cols-[minmax(0,1.3fr)_minmax(22rem,0.7fr)]">
          <section className="panel order-2 min-w-0 overflow-hidden xl:order-1" aria-labelledby="saved-supervisors-heading">
            <div className="flex items-center justify-between gap-4 border-b border-[var(--line-soft)] px-5 py-4 sm:px-6">
              <div>
                <h2 className="text-sm font-semibold text-[var(--ink)]" id="saved-supervisors-heading">
                  Saved configurations
                </h2>
                <p className="mt-1 text-xs leading-5 text-[var(--quiet)]">
                  Listed in creation order from PostgreSQL.
                </p>
              </div>
            </div>

            {loadError ? (
              <div className="state-message state-message-error m-4" role="alert">
                <p className="font-medium text-[var(--danger)]">Could not load configurations</p>
                <p className="mt-1 text-sm leading-6 text-[var(--muted)]">{loadError}</p>
                <button
                  className="button button-secondary mt-4"
                  disabled={isLoading}
                  onClick={refreshSupervisors}
                  type="button"
                >
                  Retry
                </button>
              </div>
            ) : null}

            {isInitialLoading ? <SupervisorListSkeleton /> : null}

            {!isInitialLoading && supervisors.length === 0 && !loadError ? (
              <div className="px-5 py-14 text-center sm:px-8">
                <h3 className="text-base font-semibold text-[var(--ink)]">
                  No configurations yet
                </h3>
                <p className="mx-auto mt-2 max-w-[48ch] text-sm leading-6 text-[var(--muted)]">
                  Create the first supervisor in the workbench. It will become available when you start an order run.
                </p>
              </div>
            ) : null}

            {supervisors.length > 0 ? (
              <div>
                {supervisors.map((supervisor) => (
                  <SupervisorRecord key={supervisor.id} supervisor={supervisor} />
                ))}
              </div>
            ) : null}
          </section>

          <aside className="panel order-1 xl:order-2 xl:sticky xl:top-4" aria-labelledby="create-supervisor-heading">
            <div className="border-b border-[var(--line-soft)] px-5 py-4 sm:px-6">
              <h2 className="text-sm font-semibold text-[var(--ink)]" id="create-supervisor-heading">
                Create configuration
              </h2>
              <p className="mt-1 text-xs leading-5 text-[var(--quiet)]">
                Settings are copied into each new workflow at start time.
              </p>
            </div>

            <form className="space-y-5 px-5 py-5 sm:px-6" noValidate onSubmit={handleSubmit}>
              <div className="field">
                <label htmlFor="supervisor-name">Name</label>
                <input
                  aria-describedby={fieldErrors.name ? "supervisor-name-error" : "supervisor-name-help"}
                  aria-invalid={Boolean(fieldErrors.name)}
                  autoComplete="off"
                  className="input"
                  id="supervisor-name"
                  maxLength={120}
                  onChange={(event) => {
                    setName(event.target.value);
                    setFieldErrors((current) => ({ ...current, name: undefined }));
                  }}
                  placeholder="Returns exception desk"
                  required
                  value={name}
                />
                {fieldErrors.name ? (
                  <p className="text-xs leading-5 text-[var(--danger)]" id="supervisor-name-error" role="alert">
                    {fieldErrors.name}
                  </p>
                ) : (
                  <p className="text-xs leading-5 text-[var(--quiet)]" id="supervisor-name-help">
                    Use a role or queue name operators will recognize.
                  </p>
                )}
              </div>

              <div className="field">
                <label htmlFor="supervisor-instruction">Base instruction</label>
                <textarea
                  aria-describedby={
                    fieldErrors.baseInstruction
                      ? "supervisor-instruction-error"
                      : "supervisor-instruction-help"
                  }
                  aria-invalid={Boolean(fieldErrors.baseInstruction)}
                  className="textarea min-h-32 resize-y"
                  id="supervisor-instruction"
                  maxLength={10_000}
                  onChange={(event) => {
                    setBaseInstruction(event.target.value);
                    setFieldErrors((current) => ({
                      ...current,
                      baseInstruction: undefined,
                    }));
                  }}
                  placeholder="Keep the order moving safely. Escalate payment and fulfillment exceptions with concise notes."
                  required
                  value={baseInstruction}
                />
                {fieldErrors.baseInstruction ? (
                  <p className="text-xs leading-5 text-[var(--danger)]" id="supervisor-instruction-error" role="alert">
                    {fieldErrors.baseInstruction}
                  </p>
                ) : (
                  <p className="text-xs leading-5 text-[var(--quiet)]" id="supervisor-instruction-help">
                    This instruction is included in every supervisor invocation for the run.
                  </p>
                )}
              </div>

              <fieldset
                aria-describedby={fieldErrors.availableActions ? "supervisor-actions-error" : "supervisor-actions-help"}
                aria-invalid={Boolean(fieldErrors.availableActions)}
                className="min-w-0"
              >
                <legend className="text-sm font-medium text-[var(--ink)]">Allowed actions</legend>
                <p className="mt-1 text-xs leading-5 text-[var(--quiet)]" id="supervisor-actions-help">
                  The supervisor can only propose actions selected here.
                </p>
                <div className="mt-3 grid gap-2">
                  {BUSINESS_ACTION_NAMES.map((action) => (
                    <label
                      className="flex cursor-pointer items-start gap-3 rounded-lg border border-[var(--line-soft)] bg-white/[0.02] px-3 py-2.5 text-sm text-[var(--muted)] transition-colors hover:border-[var(--line)] hover:bg-white/[0.035]"
                      key={action}
                    >
                      <input
                        checked={selectedActions.includes(action)}
                        className="mt-0.5 size-4 shrink-0 accent-[var(--accent)]"
                        onChange={() => toggleAction(action)}
                        type="checkbox"
                        value={action}
                      />
                      <span className="min-w-0 leading-5">{formatBusinessActionName(action)}</span>
                    </label>
                  ))}
                </div>
                {fieldErrors.availableActions ? (
                  <p className="mt-2 text-xs leading-5 text-[var(--danger)]" id="supervisor-actions-error" role="alert">
                    {fieldErrors.availableActions}
                  </p>
                ) : null}
              </fieldset>

              <div className="form-grid sm:grid-cols-2">
                <div className="field">
                  <label htmlFor="default-wake-seconds">Wake interval (seconds)</label>
                  <input
                    aria-describedby={
                      fieldErrors.defaultWakeSeconds
                        ? "default-wake-error"
                        : "default-wake-help"
                    }
                    aria-invalid={Boolean(fieldErrors.defaultWakeSeconds)}
                    className="input font-mono tabular-nums"
                    id="default-wake-seconds"
                    inputMode="numeric"
                    max={86_400}
                    min={1}
                    onChange={(event) => {
                      setDefaultWakeSeconds(event.target.value);
                      setFieldErrors((current) => ({
                        ...current,
                        defaultWakeSeconds: undefined,
                      }));
                    }}
                    required
                    step={1}
                    type="number"
                    value={defaultWakeSeconds}
                  />
                  {fieldErrors.defaultWakeSeconds ? (
                    <p className="text-xs leading-5 text-[var(--danger)]" id="default-wake-error" role="alert">
                      {fieldErrors.defaultWakeSeconds}
                    </p>
                  ) : (
                    <p className="text-xs leading-5 text-[var(--quiet)]" id="default-wake-help">
                      1 to 86,400
                    </p>
                  )}
                </div>

                <div className="field">
                  <label htmlFor="wake-aggressiveness">Wake aggressiveness</label>
                  <select
                    className="select"
                    id="wake-aggressiveness"
                    onChange={(event) =>
                      setWakeAggressiveness(event.target.value as WakeAggressiveness)
                    }
                    value={wakeAggressiveness}
                  >
                    <option value="low">Low</option>
                    <option value="moderate">Moderate</option>
                    <option value="high">High</option>
                  </select>
                  <p className="text-xs leading-5 text-[var(--quiet)]">
                    Guides the AI supervisor&apos;s recommended next-review timing. Event wake
                    and suppress decisions remain rule-based.
                  </p>
                </div>
              </div>

              <div className="form-grid sm:grid-cols-2">
                <div className="field">
                  <label htmlFor="supervisor-provider">Provider</label>
                  <select
                    className="select"
                    id="supervisor-provider"
                  onChange={(event) => {
                    setProvider(event.target.value as Provider);
                    setTemperature(DEFAULT_TEMPERATURE);
                    setFieldErrors((current) => ({ ...current, temperature: undefined }));
                    }}
                    value={provider}
                  >
                    <option value="deterministic">Deterministic</option>
                    <option value="ollama">Ollama</option>
                  </select>
                  <p className="text-xs leading-5 text-[var(--quiet)]">
                    {provider === "ollama" ? "Local model inference." : "Built-in rules with no model service."}
                  </p>
                </div>

                <div className="field">
                  <label htmlFor="supervisor-model">Model</label>
                  <select className="select" disabled id="supervisor-model" value={provider === "ollama" ? "qwen3:1.7b" : "deterministic-rules-v1"}>
                    <option value="deterministic-rules-v1">Built-in rules</option>
                    <option value="qwen3:1.7b">qwen3:1.7b</option>
                  </select>
                  <p className="text-xs leading-5 text-[var(--quiet)]">
                    Fixed for a repeatable local demonstration.
                  </p>
                </div>
              </div>

              <div className="field">
                <label htmlFor="supervisor-temperature">Temperature</label>
                <input
                  aria-describedby={fieldErrors.temperature ? "supervisor-temperature-error" : "supervisor-temperature-help"}
                  aria-invalid={Boolean(fieldErrors.temperature)}
                  className="input max-w-36 font-mono tabular-nums"
                  disabled
                  id="supervisor-temperature"
                  inputMode="decimal"
                  max={2}
                  min={0}
                  readOnly
                  step={0.1}
                  type="number"
                  value={temperature}
                />
                {fieldErrors.temperature ? (
                  <p className="text-xs leading-5 text-[var(--danger)]" id="supervisor-temperature-error" role="alert">
                    {fieldErrors.temperature}
                  </p>
                ) : (
                  <p className="text-xs leading-5 text-[var(--quiet)]" id="supervisor-temperature-help">
                    {provider === "ollama"
                      ? "Fixed at 0 because the current Ollama adapter uses deterministic sampling."
                      : "Used only with Ollama."}
                  </p>
                )}
              </div>

              {submitError ? (
                <div className="state-message state-message-error" role="alert">
                  <p className="font-medium text-[var(--danger)]">Configuration not created</p>
                  <p className="mt-1 text-sm leading-6 text-[var(--muted)]">{submitError}</p>
                </div>
              ) : null}

              {successMessage ? (
                <div aria-live="polite" className="state-message state-message-success" role="status">
                  <p className="font-medium text-[var(--positive)]">Configuration created</p>
                  <p className="mt-1 text-sm leading-6 text-[var(--muted)]">{successMessage}</p>
                </div>
              ) : null}

              <button className="button button-primary w-full" disabled={isSubmitting} type="submit">
                {isSubmitting ? "Creating configuration..." : "Create configuration"}
              </button>
            </form>
          </aside>
        </div>
      </div>
    </div>
  );
}
