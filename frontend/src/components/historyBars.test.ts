import { describe, expect, it } from 'vitest'

import type { TrendPoint } from '../api/hooks'
import { historyBars } from './historyBars'

const UID = '00000000-0000-0000-0000-000000000001'

function point(createdAt: string, testsFailed: number | null): TrendPoint {
  return {
    uid: UID,
    created_at: createdAt,
    tests_total: testsFailed === null ? null : 100,
    tests_failed: testsFailed,
    packages_failed: 0,
    duration_s: 60,
  }
}

describe('historyBars', () => {
  it('keeps a failing count as the bar height and colours it error', () => {
    const bars = historyBars([point('2026-11-12T01:00:00Z', 7)])

    expect(bars.values).toEqual([7])
    expect(bars.colors).toEqual(['error'])
    expect(bars.max).toBe(7)
    expect(bars.labels).toEqual(['2026-11-12 01:00: 7 failing'])
  })

  it('gives a passing run a short success bar', () => {
    const bars = historyBars([
      point('2026-11-12T01:00:00Z', 10),
      point('2026-11-12T02:00:00Z', 0),
    ])

    expect(bars.max).toBe(10)
    expect(bars.values[1]).toBeCloseTo(1.5)
    expect(bars.values[1]).toBeLessThan(bars.values[0])
    expect(bars.colors).toEqual(['error', 'success'])
    expect(bars.labels[1]).toBe('2026-11-12 02:00: passed')
  })

  it('drops runs without test counts', () => {
    const bars = historyBars([
      point('2026-11-12T01:00:00Z', null),
      point('2026-11-12T02:00:00Z', 3),
    ])

    expect(bars.values).toEqual([3])
    expect(bars.labels).toHaveLength(1)
  })

  it('labels two runs on the same day differently', () => {
    const bars = historyBars([
      point('2026-11-12T01:00:00Z', 1),
      point('2026-11-12T13:00:00Z', 2),
    ])

    expect(new Set(bars.labels).size).toBe(2)
  })

  it('has a max of at least 1 when everything passed', () => {
    const bars = historyBars([point('2026-11-12T01:00:00Z', 0)])

    expect(bars.max).toBe(1)
    expect(bars.values).toEqual([0.15])
  })

  it('returns empty arrays for no points', () => {
    expect(historyBars([])).toEqual({ values: [], labels: [], colors: [], max: 1 })
  })
})
