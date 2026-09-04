export interface RequestOptions {
  signal?: AbortSignal;
}

export interface ApiValidationIssue {
  field: string;
  message: string;
}

const configuredApiUrl = process.env.NEXT_PUBLIC_API_URL?.trim();

export const API_BASE_URL = (configuredApiUrl || "http://localhost:8000").replace(
  /\/+$/,
  "",
);

const STATUS_MESSAGES: Partial<Record<number, string>> = {
  400: "The API rejected this request.",
  404: "The requested resource was not found.",
  409: "The request conflicts with the run's current state.",
  422: "Check the submitted fields and try again.",
  503: "A required local service is unavailable. Check the backend, Temporal, and PostgreSQL.",
};

export class ApiError extends Error {
  readonly status: number;
  readonly code: string;
  readonly issues: ApiValidationIssue[];

  constructor({
    status,
    code,
    message,
    issues = [],
  }: {
    status: number;
    code: string;
    message: string;
    issues?: ApiValidationIssue[];
  }) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
    this.issues = issues;
  }
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function parseValidationIssues(detail: unknown): ApiValidationIssue[] {
  if (!Array.isArray(detail)) {
    return [];
  }

  return detail.flatMap((issue) => {
    if (!isRecord(issue) || typeof issue.msg !== "string") {
      return [];
    }

    const location = Array.isArray(issue.loc)
      ? issue.loc
          .filter((part): part is string | number =>
            typeof part === "string" || typeof part === "number",
          )
          .filter((part) => part !== "body")
          .join(".")
      : "request";

    return [
      {
        field: location || "request",
        message: issue.msg,
      },
    ];
  });
}

async function createApiError(response: Response): Promise<ApiError> {
  let body: unknown;
  try {
    body = await response.json();
  } catch {
    body = null;
  }

  const fallback = STATUS_MESSAGES[response.status] || "The API request failed.";
  if (!isRecord(body)) {
    return new ApiError({
      status: response.status,
      code: `http_${response.status}`,
      message: fallback,
    });
  }

  const detail = body.detail;
  if (isRecord(detail)) {
    return new ApiError({
      status: response.status,
      code: typeof detail.code === "string" ? detail.code : `http_${response.status}`,
      message: typeof detail.message === "string" ? detail.message : fallback,
    });
  }

  const issues = parseValidationIssues(detail);
  if (issues.length > 0) {
    const firstIssue = issues[0];
    return new ApiError({
      status: response.status,
      code: "validation_error",
      message: `${firstIssue.field}: ${firstIssue.message}`,
      issues,
    });
  }

  return new ApiError({
    status: response.status,
    code: `http_${response.status}`,
    message: typeof detail === "string" ? detail : fallback,
  });
}

function apiUrl(path: string): string {
  return `${API_BASE_URL}${path.startsWith("/") ? path : `/${path}`}`;
}

export async function apiRequest<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers);
  headers.set("Accept", "application/json");
  if (init.body !== undefined && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }

  let response: Response;
  try {
    response = await fetch(apiUrl(path), {
      ...init,
      cache: "no-store",
      headers,
    });
  } catch (error) {
    if (isAbortError(error)) {
      throw error;
    }

    throw new ApiError({
      status: 0,
      code: "network_error",
      message: `Unable to reach the API at ${API_BASE_URL}. Check that the backend is running.`,
    });
  }

  if (!response.ok) {
    throw await createApiError(response);
  }

  if (response.status === 204) {
    return undefined as T;
  }

  try {
    return (await response.json()) as T;
  } catch {
    throw new ApiError({
      status: response.status,
      code: "invalid_response",
      message: "The API returned an unreadable response.",
    });
  }
}

export function isAbortError(error: unknown): boolean {
  return (
    (typeof DOMException !== "undefined" &&
      error instanceof DOMException &&
      error.name === "AbortError") ||
    (error instanceof Error && error.name === "AbortError")
  );
}

export function getApiErrorMessage(
  error: unknown,
  fallback = "Something went wrong. Try again.",
): string {
  return error instanceof ApiError ? error.message : fallback;
}
