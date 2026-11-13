import { screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import type { RunListItem, TargetOut } from '../../api/hooks'
import { useMe, useRerun, useRuns, useTargets } from '../../api/hooks'
import { queryResult } from '../../test/queryResult'
import { renderRoute } from '../../test/renderRoute'
import { TargetPage } from './TargetPage'

// The real module supplies REFRESH_MS/isActive/isActiveStatus; the hooks are mocked.
vi.mock('../../api/hooks', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../../api/hooks')>()),
  useRuns: vi.fn(),
  useTargets: vi.fn(),
  useMe: vi.fn(),
  useRerun: vi.fn(),
}))
vi.mock('@mui/x-data-grid', async () => {
  const { MockDataGrid } = await import('../../test/mockDataGrid')
  return { DataGrid: MockDataGrid }
})

const RUN: RunListItem = {
  uid: '00000000-0000-0000-0000-000000000001',
  status: 'completed',
  trigger: 'schedule',
  triggered_by: null,
  created_at: '2026-11-14T09:00:00Z',
  started_at: '2026-11-14T09:01:00Z',
  finished_at: '2026-11-14T09:21:00Z',
  tests_total: 100,
  tests_failed: 3,
  packages_total: 55,
  packages_failed: 1,
  error: null,
  progress_total: null,
  progress_done: null,
  progress_failed: null,
  progress_at: null,
}

interface Progress {
  progress_total: number | null
  progress_done: number | null
  progress_failed: string[] | null
  progress_at: string | null
}

const NO_PROGRESS: Progress = {
  progress_total: null,
  progress_done: null,
  progress_failed: null,
  progress_at: null,
}

function target(status: RunListItem['status'] | null, progress: Progress = NO_PROGRESS): TargetOut {
  return {
    branch: 'master',
    enabled: true,
    schedule: '0 1 * * *',
    latest_run:
      status === null
        ? null
        : {
            uid: RUN.uid,
            status,
            created_at: RUN.created_at,
            tests_total: null,
            tests_failed: null,
            packages_total: null,
            packages_failed: null,
            ...progress,
          },
    latest_completed_run: null,
  }
}

describe('TargetPage', () => {
  beforeEach(() => {
    vi.mocked(useRuns).mockReturnValue(
      queryResult({ items: [RUN], total: 60, page: 0, page_size: 50 }),
    )
    vi.mocked(useTargets).mockReturnValue(queryResult([target('completed')]))
    vi.mocked(useMe).mockReturnValue(
      queryResult({ login: null, is_maintainer: false, login_enabled: false }),
    )
    vi.mocked(useRerun).mockReturnValue({
      mutateAsync: vi.fn(),
    } as unknown as ReturnType<typeof useRerun>)
  })

  it('passes the total to the grid and the page to the hook', async () => {
    const user = userEvent.setup()
    renderRoute(<TargetPage />, { route: '/targets/master', path: '/targets/:branch' })

    expect(screen.getByTestId('row-count')).toHaveTextContent('60')
    expect(vi.mocked(useRuns)).toHaveBeenLastCalledWith(
      'master',
      { page: 0, pageSize: 50 },
      expect.any(Object),
    )

    await user.click(screen.getByRole('button', { name: 'next page' }))

    expect(vi.mocked(useRuns)).toHaveBeenLastCalledWith(
      'master',
      { page: 1, pageSize: 50 },
      expect.any(Object),
    )
  })

  it('clamps out-of-range pagination from a shared URL', () => {
    renderRoute(<TargetPage />, {
      route: '/targets/master?page=-1&pageSize=999',
      path: '/targets/:branch',
    })

    expect(vi.mocked(useRuns)).toHaveBeenLastCalledWith(
      'master',
      { page: 0, pageSize: 100 },
      expect.any(Object),
    )
  })

  it('navigates to the run when a row is clicked', async () => {
    const user = userEvent.setup()
    renderRoute(<TargetPage />, {
      route: '/targets/master',
      path: '/targets/:branch',
      destination: <div>run page</div>,
    })

    await user.click(screen.getByTestId('grid-row-0'))

    expect(screen.getByText('run page')).toBeInTheDocument()
  })

  it('activates a row with Enter', async () => {
    const user = userEvent.setup()
    renderRoute(<TargetPage />, {
      route: '/targets/master',
      path: '/targets/:branch',
      destination: <div>run page</div>,
    })

    const row = screen.getByTestId('grid-row-0')
    row.focus()
    await user.keyboard('{Enter}')

    expect(screen.getByText('run page')).toBeInTheDocument()
  })

  it('shows the grid loading while a new page is fetched', () => {
    vi.mocked(useRuns).mockReturnValue(
      queryResult({ items: [RUN], total: 60, page: 0, page_size: 50 }, { isFetching: true }),
    )

    renderRoute(<TargetPage />, { route: '/targets/master', path: '/targets/:branch' })

    expect(screen.getByTestId('data-grid')).toHaveAttribute('data-loading', 'true')
  })

  it('shows a pointer cursor and centres cell content on clickable rows', () => {
    renderRoute(<TargetPage />, { route: '/targets/master', path: '/targets/:branch' })

    const sx = JSON.parse(screen.getByTestId('data-grid').getAttribute('data-sx') ?? '{}')
    expect(sx['& .MuiDataGrid-row']?.cursor).toBe('pointer')
    expect(sx['& .MuiDataGrid-cell']?.display).toBe('flex')
    expect(sx['& .MuiDataGrid-cell']?.alignItems).toBe('center')
  })

  it('polls the runs table and shows a countdown while a run is active', () => {
    vi.mocked(useTargets).mockReturnValue(queryResult([target('running')]))
    vi.mocked(useRuns).mockReturnValue(
      queryResult({ items: [RUN], total: 60, page: 0, page_size: 50 }, { dataUpdatedAt: Date.now() }),
    )

    renderRoute(<TargetPage />, { route: '/targets/master', path: '/targets/:branch' })

    const options = vi.mocked(useRuns).mock.lastCall?.[2]
    const interval = options?.refetchInterval as (query: unknown) => unknown
    const onFocus = options?.refetchOnWindowFocus as (query: unknown) => unknown
    expect(interval({ state: { data: { items: [RUN] } } })).toBe(60_000)
    expect(onFocus({ state: { data: { items: [RUN] } } })).toBe(true)
    expect(screen.getByText(/Refreshing in/)).toBeInTheDocument()
  })

  it('keeps polling while one of its own rows is still active', () => {
    // The targets query saw the run finish first; the table has not fetched the final status yet.
    vi.mocked(useTargets).mockReturnValue(queryResult([target('completed')]))
    vi.mocked(useRuns).mockReturnValue(
      queryResult(
        { items: [{ ...RUN, status: 'running' }], total: 1, page: 0, page_size: 50 },
        { dataUpdatedAt: Date.now() },
      ),
    )

    renderRoute(<TargetPage />, { route: '/targets/master', path: '/targets/:branch' })

    const options = vi.mocked(useRuns).mock.lastCall?.[2]
    const interval = options?.refetchInterval as (query: unknown) => unknown
    expect(interval({ state: { data: { items: [{ ...RUN, status: 'running' }] } } })).toBe(60_000)
    expect(interval({ state: { data: { items: [RUN] } } })).toBe(false)
    // One more fetch is coming, so the countdown stays until it lands.
    expect(screen.getByText(/Refreshing in/)).toBeInTheDocument()
  })

  it('does not poll or count down when the latest run is finished', () => {
    renderRoute(<TargetPage />, { route: '/targets/master', path: '/targets/:branch' })

    const options = vi.mocked(useRuns).mock.lastCall?.[2]
    const interval = options?.refetchInterval as (query: unknown) => unknown
    expect(interval({ state: { data: { items: [RUN] } } })).toBe(false)
    expect(screen.queryByText(/Refreshing/)).not.toBeInTheDocument()
  })

  it('shows the live progress in a running row', () => {
    vi.mocked(useTargets).mockReturnValue(queryResult([target('running')]))
    vi.mocked(useRuns).mockReturnValue(
      queryResult({
        items: [
          {
            ...RUN,
            status: 'running',
            progress_total: 58,
            progress_done: 34,
            progress_at: '2026-11-14T09:10:00Z',
          },
        ],
        total: 1,
        page: 0,
        page_size: 50,
      }),
    )

    renderRoute(<TargetPage />, { route: '/targets/master', path: '/targets/:branch' })

    expect(screen.getByText('34/58')).toBeInTheDocument()
  })

  it('shows no progress for a finished row', () => {
    renderRoute(<TargetPage />, { route: '/targets/master', path: '/targets/:branch' })

    expect(screen.queryByText(/\d+\/\d+/)).not.toBeInTheDocument()
  })
})
