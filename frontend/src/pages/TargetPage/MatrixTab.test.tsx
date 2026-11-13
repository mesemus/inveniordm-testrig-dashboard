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
    { package: 'pkg-b', cells: ['failed', null] },
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

  it('renders cells, leaves absent packages empty and sorts failures first', () => {
    const { container } = renderMatrix()

    const packageOrder = screen.getAllByText(/^pkg-/).map((element) => element.textContent)
    expect(packageOrder).toEqual(['pkg-a', 'pkg-b', 'pkg-c'])
    // pkg-a has two statuses, pkg-b and pkg-c one each; two cells are absent.
    expect(container.querySelectorAll('[role="img"]')).toHaveLength(4)
  })

  it('navigates to the run tests filtered to the package', async () => {
    const user = userEvent.setup()
    renderMatrix()

    await user.click(screen.getByTitle('pkg-a, 2026-11-13 10:00: passed'))

    expect(screen.getByText('tests page')).toBeInTheDocument()
  })
})
