import { screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { ApiError } from '../../api/client'
import type { RunDetail, TestListItem, TestResultDetail } from '../../api/hooks'
import { useRun, useRunTest, useRunTests } from '../../api/hooks'
import { queryError, queryResult } from '../../test/queryResult'
import { renderRoute } from '../../test/renderRoute'
import { RunTestsPage } from './RunTestsPage'

vi.mock('../../api/hooks', () => ({
  useRun: vi.fn(),
  useRunTests: vi.fn(),
  useRunTest: vi.fn(),
}))
vi.mock('@mui/x-data-grid', async () => {
  const { MockDataGrid } = await import('../../test/mockDataGrid')
  return { DataGrid: MockDataGrid }
})

const UID = '00000000-0000-0000-0000-000000000001'
const ROUTE = `/runs/${UID}/tests`

const RUN: RunDetail = {
  uid: UID,
  branch: 'master',
  status: 'completed',
  trigger: 'schedule',
  triggered_by: null,
  testrig_ref: 'v1.0.0',
  testrig_config: null,
  gh_run_id: null,
  gh_run_url: null,
  error: null,
  created_at: '2026-11-14T09:00:00Z',
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
  previous_completed_uid: null,
  packages: [
    {
      package: 'pkg',
      version: '1.0.0',
      git_ref: 'master',
      git_commit: 'abc',
      commits_ahead: null,
      status: 'failed',
      has_junit: true,
      duration_s: 1,
      tests_total: 1,
      tests_failed: 1,
      tests_skipped: 0,
      artifacts: { junit: '/api/junit' },
    },
  ],
}

const ITEM: TestListItem = {
  test_case_id: 7,
  package: 'pkg',
  classname: 'cls',
  name: 'test_a',
  status: 'failed',
  duration_s: 0.5,
  has_message: true,
  flaky: false,
}

const DETAIL: TestResultDetail = {
  test_case_id: 7,
  package: 'pkg',
  classname: 'cls',
  name: 'test_a',
  status: 'failed',
  duration_s: 0.5,
  message: 'AssertionError: boom',
}

describe('RunTestsPage', () => {
  beforeEach(() => {
    vi.mocked(useRun).mockReturnValue(queryResult(RUN))
    vi.mocked(useRunTests).mockReturnValue(
      queryResult({ items: [ITEM], total: 1, page: 0, page_size: 50 }),
    )
    vi.mocked(useRunTest).mockReturnValue(queryResult(DETAIL))
  })

  afterEach(() => {
    vi.useRealTimers()
  })

  it('passes the URL filters to the hook', () => {
    renderRoute(<RunTestsPage />, {
      route: `${ROUTE}?package=pkg&status=failed`,
      path: '/runs/:uid/tests',
    })

    expect(vi.mocked(useRunTests)).toHaveBeenLastCalledWith(UID, {
      page: 0,
      pageSize: 50,
      package: 'pkg',
      status: ['failed'],
      q: undefined,
    })
  })

  it('clamps out-of-range pagination from the URL', () => {
    renderRoute(<RunTestsPage />, {
      route: `${ROUTE}?page=-1&pageSize=999`,
      path: '/runs/:uid/tests',
    })

    expect(vi.mocked(useRunTests)).toHaveBeenLastCalledWith(
      UID,
      expect.objectContaining({ page: 0, pageSize: 100 }),
    )
  })

  it('debounces search into the URL, URL-encoding the value', async () => {
    const user = userEvent.setup()
    renderRoute(<RunTestsPage />, { route: ROUTE, path: '/runs/:uid/tests' })

    await user.type(screen.getByLabelText('Search name'), 'test#1')

    await waitFor(
      () => {
        expect(vi.mocked(useRunTests)).toHaveBeenLastCalledWith(
          UID,
          expect.objectContaining({ q: 'test#1' }),
        )
      },
      { timeout: 2000 },
    )
  })

  it('resets the page when a status filter changes', async () => {
    const user = userEvent.setup()
    renderRoute(<RunTestsPage />, { route: `${ROUTE}?page=3`, path: '/runs/:uid/tests' })

    await user.click(screen.getByRole('checkbox', { name: 'Failed' }))

    expect(vi.mocked(useRunTests)).toHaveBeenLastCalledWith(
      UID,
      expect.objectContaining({ page: 0, status: ['failed'] }),
    )
  })

  it('shows an error (not NotFound) for an invalid query', () => {
    vi.mocked(useRunTests).mockReturnValue(
      queryError(
        new ApiError(422, 'Invalid request: Input should be less than or equal to 100'),
      ) as unknown as ReturnType<typeof useRunTests>,
    )

    renderRoute(<RunTestsPage />, { route: `${ROUTE}?pageSize=999`, path: '/runs/:uid/tests' })

    expect(screen.getByText(/Invalid request/)).toBeInTheDocument()
    expect(screen.queryByText('Page not found')).not.toBeInTheDocument()
  })

  it('shows a pointer cursor and centres cell content on clickable rows', () => {
    renderRoute(<RunTestsPage />, { route: ROUTE, path: '/runs/:uid/tests' })

    const sx = JSON.parse(screen.getByTestId('data-grid').getAttribute('data-sx') ?? '{}')
    expect(sx['& .MuiDataGrid-row']?.cursor).toBe('pointer')
    expect(sx['& .MuiDataGrid-cell']?.display).toBe('flex')
    expect(sx['& .MuiDataGrid-cell']?.alignItems).toBe('center')
  })

  it('renders the name inline so it is vertically centred', () => {
    renderRoute(<RunTestsPage />, { route: ROUTE, path: '/runs/:uid/tests' })

    expect(screen.getByText('test_a').tagName).toBe('SPAN')
  })

  it('opens the drawer with the message and links when a row is clicked', async () => {
    const user = userEvent.setup()
    renderRoute(<RunTestsPage />, { route: ROUTE, path: '/runs/:uid/tests' })

    await user.click(screen.getByTestId('grid-row-0'))

    expect(await screen.findByText('AssertionError: boom')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'junit.xml' })).toHaveAttribute('href', '/api/junit')
    expect(screen.getByRole('link', { name: 'Test history' })).toHaveAttribute(
      'href',
      '/test-cases/7?target=master',
    )
  })

  it('opens the drawer with Enter', async () => {
    const user = userEvent.setup()
    renderRoute(<RunTestsPage />, { route: ROUTE, path: '/runs/:uid/tests' })

    const row = screen.getByTestId('grid-row-0')
    row.focus()
    await user.keyboard('{Enter}')

    expect(await screen.findByText('AssertionError: boom')).toBeInTheDocument()
  })

  it('shows the grid loading while a new filter is fetched', () => {
    vi.mocked(useRunTests).mockReturnValue(
      queryResult(
        { items: [ITEM], total: 1, page: 0, page_size: 50 },
        { isFetching: true },
      ),
    )

    renderRoute(<RunTestsPage />, { route: ROUTE, path: '/runs/:uid/tests' })

    expect(screen.getByTestId('data-grid')).toHaveAttribute('data-loading', 'true')
  })
})
