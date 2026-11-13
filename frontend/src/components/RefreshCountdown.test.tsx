import { act, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { REFRESH_MS } from '../api/hooks'
import { RefreshCountdown } from './RefreshCountdown'

describe('RefreshCountdown', () => {
  beforeEach(() => vi.useFakeTimers())
  afterEach(() => vi.useRealTimers())

  it('counts down from the last fetch', () => {
    render(<RefreshCountdown active dataUpdatedAt={Date.now() - 18_000} isFetching={false} />)

    expect(screen.getByText('Refreshing in 42 s')).toBeInTheDocument()
    act(() => vi.advanceTimersByTime(1000))
    expect(screen.getByText('Refreshing in 41 s')).toBeInTheDocument()
  })

  it('clamps to "Refreshing…" once the interval has passed', () => {
    render(
      <RefreshCountdown active dataUpdatedAt={Date.now() - REFRESH_MS - 5000} isFetching={false} />,
    )

    expect(screen.getByText('Refreshing…')).toBeInTheDocument()
  })

  it('shows "Refreshing…" while fetching', () => {
    render(<RefreshCountdown active dataUpdatedAt={Date.now()} isFetching />)

    expect(screen.getByText('Refreshing…')).toBeInTheDocument()
  })

  it('renders nothing and starts no timer when inactive', () => {
    const { container } = render(
      <RefreshCountdown active={false} dataUpdatedAt={Date.now()} isFetching={false} />,
    )

    expect(container).toBeEmptyDOMElement()
    expect(vi.getTimerCount()).toBe(0)
  })

  it('clears its timer on unmount', () => {
    const { unmount } = render(
      <RefreshCountdown active dataUpdatedAt={Date.now()} isFetching={false} />,
    )
    expect(vi.getTimerCount()).toBe(1)

    unmount()

    expect(vi.getTimerCount()).toBe(0)
  })
})
