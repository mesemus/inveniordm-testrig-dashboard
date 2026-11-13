import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import { RelativeTime } from './RelativeTime'

const NOW = new Date('2026-11-14T12:00:00Z')

describe('RelativeTime', () => {
  it('renders a relative time for a fixed now', () => {
    render(<RelativeTime date="2026-11-14T09:00:00Z" now={NOW} />)

    expect(screen.getByText('3 hours ago')).toBeInTheDocument()
  })

  it('renders a dash without a date', () => {
    render(<RelativeTime date={null} now={NOW} />)

    expect(screen.getByText('-')).toBeInTheDocument()
  })

  it('renders a dash for an unparsable date', () => {
    render(<RelativeTime date="not a date" now={NOW} />)

    expect(screen.getByText('-')).toBeInTheDocument()
  })
})
