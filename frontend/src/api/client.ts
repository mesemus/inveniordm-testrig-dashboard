import createClient from 'openapi-fetch'

import type { paths } from './schema'

export class ApiError extends Error {
  readonly status: number
  readonly detail: unknown

  constructor(status: number, message: string, detail?: unknown) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.detail = detail
  }
}

export const client = createClient<paths>({
  // Same origin in the browser. jsdom's Request needs an absolute URL, so use the origin there.
  baseUrl: typeof window === 'undefined' ? '' : window.location.origin,
})

/** The login URL that comes back to the current page afterwards. */
export function loginUrl(): string {
  return `/api/auth/login?${new URLSearchParams({ next: location.pathname + location.search })}`
}

// Every page needs a login, so a 401 means the session expired: log in again.
client.use({
  onResponse({ response }) {
    if (response.status === 401) {
      location.assign(loginUrl())
    }
  },
})

const UUID_PATTERN = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i

export function isUuid(value: string): boolean {
  return UUID_PATTERN.test(value)
}

/** FastAPI errors are `{"detail": ...}`; a validation body is a list, so show its first message.
 * Exported for `hooks.ts` and the tests. */
export function detailText(error: unknown): string {
  if (typeof error === 'object' && error !== null && 'detail' in error) {
    const detail = (error as { detail?: unknown }).detail
    if (typeof detail === 'string') {
      return detail
    }
    if (Array.isArray(detail)) {
      const first = detail[0] as { msg?: unknown } | undefined
      return typeof first?.msg === 'string' ? `Invalid request: ${first.msg}` : 'Invalid request'
    }
    if (
      typeof detail === 'object' &&
      detail !== null &&
      'message' in detail &&
      typeof (detail as { message?: unknown }).message === 'string'
    ) {
      return (detail as { message: string }).message
    }
  }
  return 'The request failed'
}

/** For rendering any thrown value. `ApiError` already carries a readable message. */
export function errorText(error: unknown): string {
  return error instanceof Error ? error.message : 'The request failed'
}

/** A path-id page treats "no such record" and "malformed id" the same: show NotFound. */
export function isMissingRecord(error: unknown): boolean {
  return error instanceof ApiError && (error.status === 404 || error.status === 422)
}

/** 404 only (e.g. an unknown target, where 422 means something else). */
export function isNotFound(error: unknown): boolean {
  return error instanceof ApiError && error.status === 404
}
