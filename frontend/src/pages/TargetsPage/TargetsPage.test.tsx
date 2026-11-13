import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import type { RunSummary, TargetOut } from '../../api/hooks'
import { useTargets, useTrend } from '../../api/hooks'
import { queryResult } from '../../test/queryResult'
import { renderRoute } from '../../test/renderRoute'
import { TargetsPage } from './TargetsPage'

vi.mock('../../api/hooks', () => ({ useTargets: vi.fn(), useTrend: vi.fn() }))
vi.mock('@mui/x-data-grid', async () => {
  const { MockDataGrid } = await import('../../test/mockDataGrid')
  return { DataGrid: MockDataGrid }
})

function summary(overrides: Partial<RunSummary> = {}): RunSummary {
  return {
    uid: '00000000-0000-0000-0000-000000000001',
    status: 'completed',
    created_at: '2026-11-14T09:00:00Z',
    tests_total: 100,
    tests_failed: 3,
    packages_total: 55,
    packages_failed: 1,
    progress_total: null,
    progress_done: null,
    progress_failed: null,
    progress_at: null,
    ...overrides,
  }
}

const COMPLETED = summary({ tests_total: 120, tests_failed: 7 })
const QUEUED = summary({ status: 'queued', tests_total: null, tests_failed: null })

const TARGETS: TargetOut[] = [
  {
    branch: 'master',
    enabled: false,
    schedule: '0 1 * * *',
    latest_run: null,
    latest_completed_run: null,
  },
  {
    branch: '14.x',
    enabled: true,
    schedule: '0 2 * * *',
    latest_run: QUEUED,
    latest_completed_run: COMPLETED,
  },
  {
    branch: 'empty',
    enabled: true,
    schedule: '0 3 * * *',
    latest_run: null,
    latest_completed_run: null,
  },
]

describe('TargetsPage', () => {
  beforeEach(() => {
    vi.mocked(useTargets).mockReturnValue(queryResult(TARGETS))
    vi.mocked(useTrend).mockReturnValue(queryResult([]))
  })

  it('renders one row per target, enabled first and then by branch', () => {
    render(
      <MemoryRouter>
        <TargetsPage />
      </MemoryRouter>,
    )

    expect(screen.getByRole('heading', { level: 1 })).toHaveTextContent('Targets')
    const links = screen.getAllByRole('link').map((link) => link.textContent)
    expect(links).toEqual(['14.x', 'empty', 'master'])
  })

  it('shows the last completed run in Result while Latest run says Queued', () => {
    render(
      <MemoryRouter>
        <TargetsPage />
      </MemoryRouter>,
    )

    expect(screen.getByText('7 failing')).toBeInTheDocument()
    expect(screen.getByText('120')).toBeInTheDocument()
    expect(screen.getByText('Queued')).toBeInTheDocument()
    expect(screen.queryByText(/last completed/)).not.toBeInTheDocument()
  })

  it('shows a Disabled chip for a disabled target', () => {
    render(
      <MemoryRouter>
        <TargetsPage />
      </MemoryRouter>,
    )

    expect(screen.getByText('Disabled')).toBeInTheDocument()
  })

  it('shows a dash for a target without runs', () => {
    render(
      <MemoryRouter>
        <TargetsPage />
      </MemoryRouter>,
    )

    // Result, Tests and Latest run: all empty (the History cell has its own dash).
    expect(within(screen.getByTestId('grid-row-1')).getAllByText('—')).toHaveLength(3)
  })

  it('navigates to the target page from the branch link', async () => {
    const user = userEvent.setup()
    render(
      <MemoryRouter initialEntries={['/']}>
        <Routes>
          <Route path="/" element={<TargetsPage />} />
          <Route path="/targets/:branch" element={<div>target page</div>} />
        </Routes>
      </MemoryRouter>,
    )

    await user.click(screen.getByRole('link', { name: '14.x' }))

    expect(screen.getByText('target page')).toBeInTheDocument()
  })

  it('navigates when another cell in the row is clicked', async () => {
    const user = userEvent.setup()
    renderRoute(<TargetsPage />, { path: '/', destination: <div>target page</div> })

    await user.click(screen.getByText('7 failing'))

    expect(screen.getByText('target page')).toBeInTheDocument()
  })

  it('navigates from a disabled row too', async () => {
    const user = userEvent.setup()
    renderRoute(<TargetsPage />, { path: '/', destination: <div>target page</div> })

    await user.click(screen.getByText('Disabled'))

    expect(screen.getByText('target page')).toBeInTheDocument()
  })

  it('shows the live progress and failed count of a running target', () => {
    vi.mocked(useTargets).mockReturnValue(
      queryResult([
        {
          branch: '14.x',
          enabled: true,
          schedule: '0 2 * * *',
          latest_run: summary({
            status: 'running',
            tests_total: null,
            tests_failed: null,
            progress_total: 58,
            progress_done: 34,
            progress_failed: ['invenio-records-rest'],
            progress_at: '2026-11-14T09:10:00Z',
          }),
          latest_completed_run: COMPLETED,
        },
      ]),
    )

    render(
      <MemoryRouter>
        <TargetsPage />
      </MemoryRouter>,
    )

    expect(screen.getByText(/34\/58/)).toBeInTheDocument()
    expect(screen.getByText(/1 failed/)).toBeInTheDocument()
  })
})
