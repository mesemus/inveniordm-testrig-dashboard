import { screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { ApiError } from '../../api/client'
import type { PackageOut, RunDetail } from '../../api/hooks'
import { useMe, useRerun, useRun, useRuns } from '../../api/hooks'
import { queryError, queryResult } from '../../test/queryResult'
import { renderRoute } from '../../test/renderRoute'
import { RunDetailPage } from './RunDetailPage'

vi.mock('../../api/hooks', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../../api/hooks')>()),
  useRun: vi.fn(),
  useRuns: vi.fn(),
  useMe: vi.fn(),
  useRerun: vi.fn(),
}))
vi.mock('@mui/x-data-grid', async () => {
  const { MockDataGrid } = await import('../../test/mockDataGrid')
  return { DataGrid: MockDataGrid }
})

const UID = '00000000-0000-0000-0000-000000000001'

function packageRow(overrides: Partial<PackageOut>): PackageOut {
  return {
    package: 'invenio-records-rest',
    version: '3.0.0',
    git_ref: 'master',
    git_commit: 'abcdef1234567890',
    commits_ahead: null,
    status: 'passed',
    has_junit: true,
    duration_s: 12.5,
    tests_total: 100,
    tests_failed: 0,
    tests_skipped: 0,
    artifacts: {},
    ...overrides,
  }
}

function runDetail(overrides: Partial<RunDetail>): RunDetail {
  return {
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
    started_at: '2026-11-14T09:01:00Z',
    finished_at: '2026-11-14T09:21:00Z',
    ingested_at: '2026-11-14T09:30:00Z',
    packages_total: 2,
    packages_failed: 0,
    tests_total: 200,
    tests_failed: 0,
    tests_skipped: 0,
    progress_total: null,
    progress_done: null,
    progress_failed: null,
    progress_at: null,
    previous_completed_uid: null,
    packages: [],
    ...overrides,
  }
}

describe('RunDetailPage', () => {
  beforeEach(() => {
    vi.mocked(useRun).mockReset()
    vi.mocked(useRuns).mockReturnValue(
      queryResult({ items: [], total: 0, page: 0, page_size: 50 }),
    )
    vi.mocked(useMe).mockReturnValue(
      queryResult({ login: null, is_maintainer: false, login_enabled: false }),
    )
    vi.mocked(useRerun).mockReturnValue({
      mutateAsync: vi.fn(),
    } as unknown as ReturnType<typeof useRerun>)
  })

  afterEach(() => {
    vi.mocked(useRun).mockReset()
  })

  it('shows the error instead of an empty package table for an infra_error run', () => {
    vi.mocked(useRun).mockReturnValue(
      queryResult(runDetail({ status: 'infra_error', error: 'workflow was cancelled' })),
    )

    renderRoute(<RunDetailPage />, { route: `/runs/${UID}`, path: '/runs/:uid' })

    expect(screen.getByText('workflow was cancelled')).toBeInTheDocument()
    expect(screen.getByText(/No package results/)).toBeInTheDocument()
    expect(screen.queryByRole('link', { name: 'Failing tests' })).not.toBeInTheDocument()
  })

  it('shows NotFound for a 422 from the run endpoint', () => {
    vi.mocked(useRun).mockReturnValue(
      queryError(
        new ApiError(422, 'Invalid request: Input should be a valid UUID'),
      ) as unknown as ReturnType<typeof useRun>,
    )

    renderRoute(<RunDetailPage />, { route: `/runs/${UID}`, path: '/runs/:uid' })

    expect(screen.getByText('Page not found')).toBeInTheDocument()
  })

  it('does not request a malformed run id', () => {
    renderRoute(<RunDetailPage />, { route: '/runs/not-a-uuid', path: '/runs/:uid' })

    expect(screen.getByText('Page not found')).toBeInTheDocument()
    expect(vi.mocked(useRun)).not.toHaveBeenCalled()
  })

  it('renders the failing-tests and GitHub links', () => {
    vi.mocked(useRun).mockReturnValue(
      queryResult(
        runDetail({
          tests_failed: 3,
          gh_run_url: 'https://github.com/oarepo/runner/actions/runs/1',
          previous_completed_uid: '00000000-0000-0000-0000-000000000002',
        }),
      ),
    )

    renderRoute(<RunDetailPage />, { route: `/runs/${UID}`, path: '/runs/:uid' })

    expect(screen.getByRole('link', { name: 'GitHub Actions run' })).toHaveAttribute(
      'href',
      'https://github.com/oarepo/runner/actions/runs/1',
    )
    expect(screen.getByRole('link', { name: 'Failing tests' })).toHaveAttribute(
      'href',
      `/runs/${UID}/tests?status=failed&status=error`,
    )
  })

  it('offers the earlier completed runs to compare with, newest first', async () => {
    const user = userEvent.setup()
    const listItem = (uid: string, created_at: string, tests_failed: number) => ({
      uid,
      status: 'completed' as const,
      trigger: 'schedule' as const,
      triggered_by: null,
      created_at,
      started_at: null,
      finished_at: null,
      tests_total: 10,
      tests_failed,
      packages_total: 1,
      packages_failed: 0,
      error: null,
      progress_total: null,
      progress_done: null,
      progress_failed: null,
      progress_at: null,
    })
    const NEWER = '00000000-0000-0000-0000-000000000009'
    const EARLIER = '00000000-0000-0000-0000-000000000002'
    const OLDEST = '00000000-0000-0000-0000-000000000003'
    vi.mocked(useRun).mockReturnValue(queryResult(runDetail({ previous_completed_uid: EARLIER })))
    vi.mocked(useRuns).mockReturnValue(
      queryResult({
        items: [
          listItem(NEWER, '2026-11-15T09:00:00Z', 0),
          listItem(UID, '2026-11-14T09:00:00Z', 0),
          listItem(EARLIER, '2026-11-13T09:00:00Z', 7),
          listItem(OLDEST, '2026-11-12T09:00:00Z', 0),
        ],
        total: 4,
        page: 0,
        page_size: 50,
      }),
    )

    renderRoute(<RunDetailPage />, { route: `/runs/${UID}`, path: '/runs/:uid' })
    await user.click(screen.getByRole('button', { name: 'Compare with…' }))

    const items = screen.getAllByRole('menuitem')
    // Neither the run itself nor a newer run; each item says how it went and when.
    expect(items).toHaveLength(2)
    expect(items[0]).toHaveTextContent('7 failing · 2026-11-13 09:00')
    expect(items[1]).toHaveTextContent('passed · 2026-11-12 09:00')
    expect(items[0]).toHaveAttribute('href', `/compare?base=${EARLIER}&head=${UID}`)
  })

  it('has no compare menu without an earlier completed run', () => {
    vi.mocked(useRun).mockReturnValue(queryResult(runDetail({ previous_completed_uid: null })))

    renderRoute(<RunDetailPage />, { route: `/runs/${UID}`, path: '/runs/:uid' })

    expect(screen.queryByRole('button', { name: 'Compare with…' })).not.toBeInTheDocument()
  })

  it('shows the testrig config in a dialog, not on the page', async () => {
    const user = userEvent.setup()
    vi.mocked(useRun).mockReturnValue(
      queryResult(runDetail({ testrig_config: 'seed_repository:\n  url: x' })),
    )

    renderRoute(<RunDetailPage />, { route: `/runs/${UID}`, path: '/runs/:uid' })
    expect(screen.queryByText(/seed_repository/)).not.toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'View' }))

    const dialog = screen.getByRole('dialog', { name: 'testrig config' })
    expect(within(dialog).getByText(/seed_repository/)).toBeInTheDocument()
  })

  it('shows the repo package last', () => {
    const packages = ['repo', 'aaa-package', 'e2e'].map((name) => packageRow({ package: name }))
    vi.mocked(useRun).mockReturnValue(queryResult(runDetail({ packages })))

    renderRoute(<RunDetailPage />, { route: `/runs/${UID}`, path: '/runs/:uid' })

    expect(screen.getByTestId('grid-row-2')).toHaveTextContent('repo')
    const sx = JSON.parse(screen.getByTestId('data-grid').getAttribute('data-sx') ?? '{}')
    expect(sx['& .MuiDataGrid-row']?.cursor).toBe('pointer')
    expect(sx['& .MuiDataGrid-cell']?.alignItems).toBe('center')
  })

  it('puts failing packages first, then alphabetical, with repo last', () => {
    const packages = [
      packageRow({ package: 'zeta', status: 'failed' }),
      packageRow({ package: 'alpha', status: 'passed' }),
      packageRow({ package: 'beta', status: 'error' }),
      packageRow({ package: 'repo', status: 'not_run' }),
    ]
    vi.mocked(useRun).mockReturnValue(queryResult(runDetail({ packages })))

    renderRoute(<RunDetailPage />, { route: `/runs/${UID}`, path: '/runs/:uid' })

    const rows = [0, 1, 2, 3].map(
      (index) => screen.getByTestId(`grid-row-${index}`).textContent ?? '',
    )
    expect(rows[0]).toContain('zeta')
    expect(rows[1]).toContain('beta')
    expect(rows[2]).toContain('alpha')
    expect(rows[3]).toContain('repo')
  })

  it('stays alphabetical when nothing failed', () => {
    const packages = [
      packageRow({ package: 'zeta', status: 'skipped' }),
      packageRow({ package: 'alpha', status: 'passed' }),
      packageRow({ package: 'e2e', status: 'passed' }),
    ]
    vi.mocked(useRun).mockReturnValue(queryResult(runDetail({ packages })))

    renderRoute(<RunDetailPage />, { route: `/runs/${UID}`, path: '/runs/:uid' })

    const rows = [0, 1, 2].map(
      (index) => screen.getByTestId(`grid-row-${index}`).textContent ?? '',
    )
    expect(rows[0]).toContain('alpha')
    expect(rows[1]).toContain('e2e')
    expect(rows[2]).toContain('zeta')
  })

  it('renders cell text inline so it is vertically centred', () => {
    vi.mocked(useRun).mockReturnValue(queryResult(runDetail({ packages: [packageRow({})] })))

    renderRoute(<RunDetailPage />, { route: `/runs/${UID}`, path: '/runs/:uid' })

    // DataGrid cells centre their *inline* content with line-height; a block element
    // (e.g. MUI Typography's <p>) would sit at the top of the cell.
    const commit = within(screen.getByTestId('grid-row-0')).getByTitle('abcdef1234567890')
    expect(commit.tagName).toBe('SPAN')
  })

  it('lists packages and links the expected artifact files', async () => {
    const normal = packageRow({
      artifacts: {
        junit: '/api/runs/u/artifacts/artifacts/invenio-records-rest/original_junit.xml',
      },
    })
    const e2e = packageRow({
      package: 'e2e',
      has_junit: false,
      artifacts: {
        playwright_report:
          '/api/runs/u/artifacts/artifacts/e2e/patched_playwright-report/index.html',
      },
    })
    vi.mocked(useRun).mockReturnValue(queryResult(runDetail({ packages: [normal, e2e] })))
    const user = userEvent.setup()

    renderRoute(<RunDetailPage />, { route: `/runs/${UID}`, path: '/runs/:uid' })

    expect(screen.getByText('invenio-records-rest')).toBeInTheDocument()
    expect(screen.getByText('e2e')).toBeInTheDocument()

    // Packages are sorted, so e2e is row 0 and invenio-records-rest row 1.
    await user.click(
      within(screen.getByTestId('grid-row-1')).getByRole('button', { name: 'Artifacts' }),
    )
    expect(screen.getByRole('menuitem', { name: 'JUnit XML' })).toHaveAttribute(
      'href',
      '/api/runs/u/artifacts/artifacts/invenio-records-rest/original_junit.xml',
    )
  })

  it('polls and counts down while the run is active', () => {
    vi.mocked(useRun).mockReturnValue(
      queryResult(runDetail({ status: 'queued' }), { dataUpdatedAt: Date.now() }),
    )

    renderRoute(<RunDetailPage />, { route: `/runs/${UID}`, path: '/runs/:uid' })

    const options = vi.mocked(useRun).mock.lastCall?.[1]
    const decide = options?.refetchInterval as (query: unknown) => unknown
    expect(decide).toBeTypeOf('function')
    expect(decide({ state: { data: runDetail({ status: 'queued' }) } })).toBe(60_000)
    expect(decide({ state: { data: runDetail({ status: 'completed' }) } })).toBe(false)
    expect(screen.getByText(/Refreshing in/)).toBeInTheDocument()
  })

  it('does not poll a finished run', () => {
    vi.mocked(useRun).mockReturnValue(queryResult(runDetail({ status: 'lost' })))

    renderRoute(<RunDetailPage />, { route: `/runs/${UID}`, path: '/runs/:uid' })

    expect(screen.queryByText(/Refreshing/)).not.toBeInTheDocument()
  })
})
