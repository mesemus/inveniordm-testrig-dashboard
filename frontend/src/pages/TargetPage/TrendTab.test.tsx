import { render, screen } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import type { TrendPoint } from '../../api/hooks'
import { useTrend } from '../../api/hooks'
import { queryResult } from '../../test/queryResult'
import { TrendTab } from './TrendTab'

vi.mock('../../api/hooks', () => ({ useTrend: vi.fn() }))

const POINTS: TrendPoint[] = [
  {
    uid: '00000000-0000-0000-0000-000000000001',
    created_at: '2026-11-13T10:00:00Z',
    tests_total: 10,
    tests_failed: 3,
    packages_failed: 1,
    duration_s: 120,
  },
  {
    uid: '00000000-0000-0000-0000-000000000002',
    created_at: '2026-11-14T10:00:00Z',
    tests_total: null,
    tests_failed: null,
    packages_failed: null,
    duration_s: null,
  },
]

describe('TrendTab', () => {
  beforeEach(() => {
    vi.mocked(useTrend).mockReturnValue(queryResult(POINTS))
  })

  it('renders both charts, including a run without counts', () => {
    render(<TrendTab branch="master" />)

    expect(screen.getByText('Failing tests and packages')).toBeInTheDocument()
    expect(screen.getByText('Run duration (minutes)')).toBeInTheDocument()
  })
})
