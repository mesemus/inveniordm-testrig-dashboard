import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import type { StatusValue } from '../api/hooks'
import { Status } from './Status'

const CASES: Array<[StatusValue, string]> = [
  ['passed', 'Passed'],
  ['failed', 'Failed'],
  ['error', 'Error'],
  ['skipped', 'Skipped'],
  ['flaky', 'Flaky'],
  ['requested', 'Requested'],
  ['queued', 'Queued'],
  ['running', 'Running'],
  ['infra_error', 'Infra error'],
  ['lost', 'Lost'],
  ['not_run', 'Not run'],
]

describe('Status', () => {
  it.each(CASES)('renders the label for %s', (status, label) => {
    render(<Status status={status} />)

    expect(screen.getByText(label)).toBeInTheDocument()
  })

  it('shows "Passed" for a completed run without failures', () => {
    render(<Status status="completed" failing={0} />)

    expect(screen.getByText('Passed')).toBeInTheDocument()
  })

  it('shows the failing count for a completed run', () => {
    render(<Status status="completed" failing={3} />)

    expect(screen.getByText('3 failing')).toBeInTheDocument()
  })

  it('gives icon-only statuses an accessible label', () => {
    render(<Status status="failed" iconOnly />)

    expect(screen.getByLabelText('Failed')).toBeInTheDocument()
    expect(screen.queryByText('Failed')).not.toBeInTheDocument()
  })
})
