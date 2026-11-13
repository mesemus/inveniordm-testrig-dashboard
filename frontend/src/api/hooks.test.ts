import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { renderHook, waitFor } from '@testing-library/react'
import type { Middleware } from 'openapi-fetch'
import { createElement } from 'react'
import type { ReactNode } from 'react'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'

import { ApiError, client } from './client'
import type { TargetOut } from './hooks'
import { isActive, isActiveStatus, useLogout, useRun, useRunTests } from './hooks'

const UID = '00000000-0000-0000-0000-000000000001'
const seen: string[] = []
let reply: { status: number; body: unknown } = { status: 200, body: {} }
let queryClient: QueryClient

const fake: Middleware = {
  onRequest({ request }) {
    seen.push(request.url)
    return new Response(JSON.stringify(reply.body), {
      status: reply.status,
      headers: { 'Content-Type': 'application/json' },
    })
  },
}

function wrapper({ children }: { children: ReactNode }) {
  return createElement(QueryClientProvider, { client: queryClient }, children)
}

describe('api hooks', () => {
  beforeEach(() => {
    seen.length = 0
    queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    client.use(fake)
  })

  afterEach(() => {
    client.eject(fake)
    queryClient.clear()
  })

  it('sends repeated status params, page_size and an encoded q', async () => {
    reply = { status: 200, body: { items: [], total: 0, page: 0, page_size: 50 } }

    const { result } = renderHook(
      () => useRunTests(UID, { page: 0, pageSize: 50, status: ['failed', 'error'], q: 'a#b' }),
      { wrapper },
    )
    await waitFor(() => expect(result.current.isSuccess).toBe(true))

    const url = new URL(seen[0] ?? '', 'http://localhost')
    expect(url.pathname).toBe(`/api/runs/${UID}/tests`)
    expect(url.searchParams.getAll('status')).toEqual(['failed', 'error'])
    expect(url.searchParams.get('page_size')).toBe('50')
    expect(url.searchParams.get('q')).toBe('a#b')
  })

  it('turns a 404 body into an ApiError with the detail', async () => {
    reply = { status: 404, body: { detail: 'run not found' } }

    const { result } = renderHook(() => useRun(UID), { wrapper })
    await waitFor(() => expect(result.current.isError).toBe(true))

    expect(result.current.error).toBeInstanceOf(ApiError)
    expect((result.current.error as ApiError).status).toBe(404)
    expect(result.current.error?.message).toBe('run not found')
  })

  it('reads the first message of a validation body', async () => {
    reply = { status: 422, body: { detail: [{ msg: 'Input should be <= 100' }] } }

    const { result } = renderHook(() => useRun(UID), { wrapper })
    await waitFor(() => expect(result.current.isError).toBe(true))

    expect(result.current.error?.message).toBe('Invalid request: Input should be <= 100')
  })

  it('drops every cached page after a logout', async () => {
    reply = { status: 204, body: undefined }
    queryClient.setQueryData(['targets'], [{ branch: 'master' }])

    const { result } = renderHook(() => useLogout(), { wrapper })
    await result.current.mutateAsync()

    await waitFor(() => expect(queryClient.getQueryData(['targets'])).toBeUndefined())
  })
})

describe('activity helpers', () => {
  it.each(['requested', 'queued', 'running'] as const)('%s is active', (status) => {
    expect(isActiveStatus(status)).toBe(true)
  })

  it.each(['completed', 'infra_error', 'lost', 'failed', 'skipped', 'flaky', null] as const)(
    '%s is not active',
    (status) => {
      expect(isActiveStatus(status)).toBe(false)
    },
  )

  it('isActive reads the branch\'s latest run', () => {
    const targets = [
      { branch: 'master', latest_run: { status: 'running' } },
      { branch: 'other', latest_run: null },
    ] as unknown as TargetOut[]

    expect(isActive(targets, 'master')).toBe(true)
    expect(isActive(targets, 'other')).toBe(false)
    expect(isActive(targets, 'missing')).toBe(false)
    expect(isActive(undefined, 'master')).toBe(false)
  })
})
