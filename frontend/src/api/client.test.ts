import { afterEach, describe, expect, it, vi } from 'vitest'

import { client, detailText } from './client'

describe('an expired session', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('sends a 401 to the login and back to the current page', async () => {
    const assign = vi.fn()
    vi.stubGlobal('location', { pathname: '/test-cases/7', search: '?target=14.x', assign })

    // A per-request fetch: a middleware that answers in onRequest would skip onResponse.
    await client.GET('/api/targets', {
      fetch: async () => new Response('{"detail": "log in first"}', { status: 401 }),
    })

    expect(assign).toHaveBeenCalledWith(
      '/api/auth/login?next=%2Ftest-cases%2F7%3Ftarget%3D14.x',
    )
  })
})

describe('detailText', () => {
  it('returns a string detail verbatim', () => {
    expect(detailText({ detail: 'run not found' })).toBe('run not found')
  })

  it('turns a FastAPI validation list into one readable line', () => {
    expect(
      detailText({
        detail: [{ type: 'less_than_equal', loc: ['query', 'page_size'], msg: 'Input should be <= 100' }],
      }),
    ).toBe('Invalid request: Input should be <= 100')
  })

  it('falls back for an unknown error body', () => {
    expect(detailText(undefined)).toBe('The request failed')
  })
})
