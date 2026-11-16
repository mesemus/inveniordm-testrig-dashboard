import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import type { MatrixOut } from '../../api/hooks'
import { useMatrix } from '../../api/hooks'
import { queryResult } from '../../test/queryResult'
import { MatrixTab } from './MatrixTab'

vi.mock('../../api/hooks', () => ({ useMatrix: vi.fn() }))

const MATRIX: MatrixOut = {
  runs: [
    { uid: '00000000-0000-0000-0000-000000000001', created_at: '2026-11-13T10:00:00Z' },
    { uid: '00000000-0000-0000-0000-000000000002', created_at: '2026-11-14T10:00:00Z' },
  ],
  packages: [
    { package: 'pkg-a', cells: ['passed', 'failed'] },
    { package: 'pkg-b', cells: ['failed', 'error'] },
    { package: 'pkg-c', cells: [null, 'passed'] },
  ],
}

function renderMatrix() {
  return render(
    <MemoryRouter initialEntries={['/targets/master']}>
      <Routes>
        <Route path="/runs/:uid/tests" element={<div>tests page</div>} />
        <Route path="*" element={<MatrixTab branch="master" />} />
      </Routes>
    </MemoryRouter>,
  )
}

describe('MatrixTab', () => {
  beforeEach(() => {
    vi.mocked(useMatrix).mockReturnValue(queryResult(MATRIX))
  })

  it('renders cells, leaves absent packages empty and sorts the most failures first', () => {
    const { container } = renderMatrix()

    // pkg-b fails in both shown runs, pkg-a in one, pkg-c in none.
    const packageOrder = screen.getAllByText(/^pkg-/).map((element) => element.textContent)
    expect(packageOrder).toEqual(['pkg-b', 'pkg-a', 'pkg-c'])
    // Five statuses; pkg-c's first cell is absent.
    expect(container.querySelectorAll('[role="img"]')).toHaveLength(5)
  })

  it('shows the newest run first, with relative dates and the exact date as a tooltip', async () => {
    const user = userEvent.setup()
    renderMatrix()

    const runLinks = screen
      .getAllByRole('link')
      .filter((link) => /^\/runs\/[^/]+$/.test(link.getAttribute('href') ?? ''))
    expect(runLinks.map((link) => link.getAttribute('href'))).toEqual([
      '/runs/00000000-0000-0000-0000-000000000002',
      '/runs/00000000-0000-0000-0000-000000000001',
    ])
    expect(runLinks[0]).toHaveTextContent(/ago|yesterday/)

    await user.hover(runLinks[0].querySelector('span') as HTMLElement)
    expect(await screen.findByRole('tooltip')).toHaveTextContent('2026-11-14 10:00 UTC')
  })

  it('keeps every cell under its own run', () => {
    renderMatrix()

    // pkg-b's row: the newest run (error) first, then the older one (failed).
    const cells = screen.getAllByLabelText(/^pkg-b, /).map((cell) => cell.getAttribute('aria-label'))
    expect(cells).toEqual(['pkg-b, 2026-11-14 10:00: error', 'pkg-b, 2026-11-13 10:00: failed'])
  })

  it('navigates to the run tests filtered to the package', async () => {
    const user = userEvent.setup()
    renderMatrix()

    await user.click(screen.getByTitle('pkg-a, 2026-11-13 10:00: passed'))

    expect(screen.getByText('tests page')).toBeInTheDocument()
  })
})
