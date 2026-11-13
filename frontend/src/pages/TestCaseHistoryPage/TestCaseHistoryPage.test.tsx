import { render, screen } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { ApiError } from '../../api/client'
import type { HistoryOut, TestCaseOut, TestResultDetail, TargetOut } from '../../api/hooks'
import { useRunTest, useTestCase, useTestCaseHistory, useTargets } from '../../api/hooks'
import { queryError, queryResult } from '../../test/queryResult'
import { TestCaseHistoryPage } from './TestCaseHistoryPage'

vi.mock('../../api/hooks', () => ({
  useTestCase: vi.fn(),
  useTestCaseHistory: vi.fn(),
  useTargets: vi.fn(),
  useRunTest: vi.fn(),
}))

const CASE: TestCaseOut = {
  id: 7,
  package: 'invenio-records-rest',
  classname: 'tests.test_indexing',
  name: 'test_index_creation',
  first_seen_run_uid: '00000000-0000-0000-0000-000000000001',
  first_seen_at: '2026-11-01T10:00:00Z',
}

const TARGET: TargetOut = {
  branch: 'master',
  enabled: true,
  schedule: '0 1 * * *',
  latest_run: null,
  latest_completed_run: null,
}

const FAILURE: TestResultDetail = {
  test_case_id: 7,
  package: 'invenio-records-rest',
  classname: 'tests.test_indexing',
  name: 'test_index_creation',
  status: 'failed',
  duration_s: 1,
  message: 'AssertionError: boom',
}

function history(statuses: Array<'passed' | 'failed' | 'error' | 'skipped' | null>): HistoryOut {
  return {
    flaky: false,
    items: statuses.map((status, index) => ({
      uid: `00000000-0000-0000-0000-00000000010${index}`,
      created_at: `2026-11-0${index + 1}T10:00:00Z`,
      status,
    })),
  }
}

function renderPage(route: string) {
  return render(
    <MemoryRouter initialEntries={[route]}>
      <Routes>
        <Route path="/test-cases/:id" element={<TestCaseHistoryPage />} />
      </Routes>
    </MemoryRouter>,
  )
}

describe('TestCaseHistoryPage', () => {
  beforeEach(() => {
    vi.mocked(useTestCase).mockReturnValue(queryResult(CASE))
    vi.mocked(useTargets).mockReturnValue(queryResult([TARGET]))
    vi.mocked(useRunTest).mockReturnValue(queryResult(FAILURE))
  })

  it('says how long a test has been failing', () => {
    vi.mocked(useTestCaseHistory).mockReturnValue(
      queryResult(history(['passed', 'failed', 'failed'])),
    )

    renderPage('/test-cases/7?target=master')

    expect(screen.getByText('failing in the last 2 runs')).toBeInTheDocument()
    expect(screen.getByText('AssertionError: boom')).toBeInTheDocument()
  })

  it('says since when a test has been passing', () => {
    vi.mocked(useTestCaseHistory).mockReturnValue(
      queryResult(history(['failed', 'passed', 'passed'])),
    )

    renderPage('/test-cases/7?target=master')

    expect(screen.getByText('passing since 11-02')).toBeInTheDocument()
  })

  it('shows a target picker when the URL has no target', () => {
    vi.mocked(useTestCaseHistory).mockReturnValue(queryResult(history(['passed'])))

    renderPage('/test-cases/7')

    expect(screen.getByLabelText('Target')).toBeInTheDocument()
    expect(vi.mocked(useTestCaseHistory)).toHaveBeenLastCalledWith(7, 'master', 50)
  })

  it('shows a dash for an absent history cell', () => {
    vi.mocked(useTestCaseHistory).mockReturnValue(
      queryResult(history(['passed', null, 'failed'])),
    )

    renderPage('/test-cases/7?target=master')

    expect(screen.getAllByTestId('history-absent')).toHaveLength(1)
  })

  it('shows NotFound for a non-numeric id without a request', () => {
    renderPage('/test-cases/abc')

    expect(screen.getByText('Page not found')).toBeInTheDocument()
    expect(vi.mocked(useTestCase)).not.toHaveBeenCalled()
  })

  it('shows a spinner instead of a false streak while the history loads', () => {
    vi.mocked(useTestCaseHistory).mockReturnValue({
      data: undefined,
      isLoading: true,
    } as unknown as ReturnType<typeof useTestCaseHistory>)

    renderPage('/test-cases/7?target=master')

    expect(screen.getByRole('progressbar')).toBeInTheDocument()
    expect(screen.queryByText('no completed runs for this target')).not.toBeInTheDocument()
  })

  it('reports an unknown target next to the picker, not as NotFound', () => {
    vi.mocked(useTestCaseHistory).mockReturnValue(
      queryError(
        new ApiError(404, 'target not found'),
      ) as unknown as ReturnType<typeof useTestCaseHistory>,
    )

    renderPage('/test-cases/7?target=renamed')

    expect(screen.getByText(/Unknown target/)).toBeInTheDocument()
    expect(screen.getByText(/test_index_creation/)).toBeInTheDocument()
    expect(screen.queryByText('Page not found')).not.toBeInTheDocument()
  })
})
