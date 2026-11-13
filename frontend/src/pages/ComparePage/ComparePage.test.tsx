import { screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { ApiError } from '../../api/client'
import type { CompareOut, RunDetail } from '../../api/hooks'
import {
  useCompare,
  useCompareTests,
  useRun,
} from '../../api/hooks'
import { queryError, queryResult } from '../../test/queryResult'
import { renderRoute } from '../../test/renderRoute'
import { ComparePage } from './ComparePage'

vi.mock('../../api/hooks', () => ({
  useCompare: vi.fn(),
  useCompareTests: vi.fn(),
  useRun: vi.fn(),
}))
vi.mock('@mui/x-data-grid', async () => {
  const { MockDataGrid } = await import('../../test/mockDataGrid')
  return { DataGrid: MockDataGrid }
})

const BASE = '00000000-0000-0000-0000-000000000001'
const HEAD = '00000000-0000-0000-0000-000000000002'
const PREVIOUS = '00000000-0000-0000-0000-000000000003'

const HEAD_RUN: RunDetail = {
  uid: HEAD,
  branch: 'master',
  status: 'completed',
  trigger: 'schedule',
  triggered_by: null,
  testrig_ref: 'v1.0.0',
  testrig_config: null,
  gh_run_id: null,
  gh_run_url: null,
  error: null,
  created_at: '2026-11-14T10:00:00Z',
  dispatched_at: null,
  started_at: null,
  finished_at: null,
  ingested_at: null,
  packages_total: 1,
  packages_failed: 0,
  tests_total: 1,
  tests_failed: 1,
  tests_skipped: 0,
  progress_total: null,
  progress_done: null,
  progress_failed: null,
  progress_at: null,
  previous_completed_uid: PREVIOUS,
  packages: [],
}

const BASE_RUN: RunDetail = {
  ...HEAD_RUN,
  uid: BASE,
  created_at: '2026-11-12T10:00:00Z',
  previous_completed_uid: null,
}

const COMPARISON: CompareOut = {
  base: BASE,
  head: HEAD,
  packages: [
    {
      package: 'pkg',
      base_status: 'passed',
      head_status: 'failed',
      base_version: '1.0.0',
      head_version: '1.0.1',
      base_commit: 'aaa',
      head_commit: 'bbb',
      changed: true,
    },
    {
      package: 'steady-pkg',
      base_status: 'passed',
      head_status: 'passed',
      base_version: '2.0.0',
      head_version: '2.0.0',
      base_commit: 'ccc',
      head_commit: 'ccc',
      changed: false,
    },
  ],
  counts: { flipped: 2, new: 1, gone: 0 },
}

function renderCompare(route: string) {
  return renderRoute(<ComparePage />, { route, path: '/compare' })
}

describe('ComparePage', () => {
  beforeEach(() => {
    vi.mocked(useRun).mockReturnValue(queryResult(HEAD_RUN))
    vi.mocked(useCompare).mockReturnValue(queryResult(COMPARISON))
    vi.mocked(useCompareTests).mockReturnValue(
      queryResult({ items: [], total: 0, page: 0, page_size: 50 }),
    )
  })

  it('defaults the base to the head run’s previous completed run', async () => {
    renderCompare(`/compare?head=${HEAD}&tab=changed`)

    await waitFor(() =>
      expect(vi.mocked(useCompare)).toHaveBeenLastCalledWith(PREVIOUS, HEAD),
    )
    expect(screen.getByRole('table', { name: 'Changed packages' })).toBeInTheDocument()
    // The commit column explains a change that the version/status columns do not.
    expect(screen.getByText('aaa')).toBeInTheDocument()
    expect(screen.getByText('bbb')).toBeInTheDocument()
  })

  it('shows only the comparison: the two runs as facts, no pickers', () => {
    vi.mocked(useRun).mockImplementation((uid) =>
      queryResult(uid === BASE ? BASE_RUN : HEAD_RUN),
    )

    renderCompare(`/compare?base=${BASE}&head=${HEAD}`)

    expect(screen.queryByRole('combobox')).not.toBeInTheDocument()
    const runLinks = screen.getAllByRole('link', { name: /^Run / })
    expect(runLinks.map((link) => link.getAttribute('href'))).toEqual([`/runs/${BASE}`, `/runs/${HEAD}`])
    expect(screen.getByText('Base')).toBeInTheDocument()
    expect(screen.getByText('Head')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'master' })).toHaveAttribute('href', '/targets/master')
  })

  it('shows NotFound for an invalid base id without a request', () => {
    renderCompare(`/compare?base=nope&head=${HEAD}`)

    expect(screen.getByText('Page not found')).toBeInTheDocument()
    expect(vi.mocked(useCompare)).not.toHaveBeenCalled()
  })

  it('keeps the active tab in the URL', async () => {
    const user = userEvent.setup()
    renderCompare(`/compare?base=${BASE}&head=${HEAD}`)

    await user.click(screen.getByRole('tab', { name: 'New tests (1)' }))

    expect(vi.mocked(useCompareTests)).toHaveBeenLastCalledWith(BASE, HEAD, 'new', 0, 50)
    // A test tab shows the test list, not the package table.
    expect(screen.queryByRole('table', { name: 'Changed packages' })).not.toBeInTheDocument()
  })

  it('has one row of tabs: the three test lists, then the two package views', async () => {
    const user = userEvent.setup()
    renderCompare(`/compare?base=${BASE}&head=${HEAD}`)

    expect(screen.getAllByRole('tab').map((tab) => tab.textContent)).toEqual([
      'Flipped tests (2)',
      'New tests (1)',
      'Gone tests (0)',
      'Changed packages (1)',
      'All packages (2)',
    ])
    // The default tab is the flipped tests, not a package table.
    expect(screen.getByRole('tab', { name: 'Flipped tests (2)' })).toHaveAttribute('aria-selected', 'true')
    expect(screen.queryByRole('table', { name: 'Changed packages' })).not.toBeInTheDocument()

    await user.click(screen.getByRole('tab', { name: 'Changed packages (1)' }))
    expect(screen.queryByText('steady-pkg')).not.toBeInTheDocument()

    await user.click(screen.getByRole('tab', { name: 'All packages (2)' }))

    expect(screen.getByText('steady-pkg')).toBeInTheDocument()
  })

  it('shows an API 400 as an alert, not as NotFound', () => {
    vi.mocked(useCompare).mockReturnValue(
      queryError(
        new ApiError(400, 'both runs must belong to the same target'),
      ) as unknown as ReturnType<typeof useCompare>,
    )

    renderCompare(`/compare?base=${BASE}&head=${HEAD}`)

    expect(screen.getByText('both runs must belong to the same target')).toBeInTheDocument()
    expect(screen.queryByText('Page not found')).not.toBeInTheDocument()
  })
})
