import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import { Facts } from './Fact'
import type { ProgressRun } from './progress'
import { failedSoFar, progressSummary } from './progress'
import { RunProgress } from './RunProgress'

function run(overrides: Partial<ProgressRun> = {}): ProgressRun {
  return {
    status: 'running',
    progress_total: 58,
    progress_done: 34,
    progress_failed: [],
    progress_at: '2026-11-14T09:10:00Z',
    ...overrides,
  }
}

function renderFacts(value: ProgressRun) {
  return render(
    <Facts>
      <RunProgress run={value} />
    </Facts>,
  )
}

describe('RunProgress', () => {
  it('shows the packages finished, a bar and the failures so far', () => {
    renderFacts(run({ progress_failed: ['invenio-records-rest', 'invenio-files'] }))

    expect(screen.getByText('Progress')).toBeInTheDocument()
    expect(screen.getByText('34 / 58 packages')).toBeInTheDocument()
    const bar = screen.getByRole('progressbar', { name: 'Packages finished' })
    expect(bar).toHaveAttribute('aria-valuetext', '34 of 58 packages')
    expect(screen.getByText('Failed so far')).toBeInTheDocument()
    expect(screen.getByText('invenio-records-rest, invenio-files')).toBeInTheDocument()
  })

  it('summarises more than ten failures', () => {
    const many = Array.from({ length: 12 }, (_, index) => `pkg-${index}`)

    renderFacts(run({ progress_failed: many }))

    expect(screen.getByText(/pkg-0, .*pkg-9 \+2 more/)).toBeInTheDocument()
  })

  it('says Preparing… before testrig has created the package jobs', () => {
    renderFacts(
      run({
        progress_total: null,
        progress_done: null,
        progress_failed: null,
        progress_at: null,
      }),
    )

    expect(screen.getByText('Preparing…')).toBeInTheDocument()
    expect(screen.queryByRole('progressbar')).not.toBeInTheDocument()
    expect(screen.queryByText('Failed so far')).not.toBeInTheDocument()
  })

  it('renders nothing once the run is not running', () => {
    const { container } = renderFacts(run({ status: 'completed' }))

    // The <dl> wrapper stays; nothing is rendered into it.
    expect(container).toHaveTextContent('')
    expect(screen.queryByText('Progress')).not.toBeInTheDocument()
    expect(screen.queryByRole('progressbar')).not.toBeInTheDocument()
  })
})

describe('progress helpers', () => {
  it('summarises a running run with a snapshot', () => {
    expect(progressSummary(run())).toBe('34/58')
    expect(failedSoFar(run({ progress_failed: ['a', 'b'] }))).toBe(2)
  })

  it('says nothing while there is no snapshot or the run is not running', () => {
    expect(progressSummary(run({ progress_at: null }))).toBeNull()
    expect(progressSummary(run({ status: 'queued' }))).toBeNull()
    expect(failedSoFar(run({ status: 'completed', progress_failed: ['a'] }))).toBe(0)
  })
})
