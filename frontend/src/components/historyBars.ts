import type { TrendPoint } from '../api/hooks'
import { dateTimeLabel } from './format'

export interface HistoryBars {
  /** Bar heights in 0..1: failingHeight() for a failing run, PASSED_HEIGHT for a passing one. */
  values: number[]
  /** One unique label per bar; the tooltip header. */
  labels: string[]
  colors: ('error' | 'success')[]
}

/** How tall a passing run's bar is (the full height is 1). */
const PASSED_HEIGHT = 0.15

/**
 * A fixed log scale, the same for every target: 1 failing = 0.3, 10 = 0.65, 100+ = 1.
 * Not relative to the worst run, so one spike doesn't flatten the others; the tooltip has the count.
 */
export function failingHeight(failing: number): number {
  return Math.min(1, 0.3 + 0.35 * Math.log10(failing))
}

/**
 * Bars for the History sparkline: failing runs as red bars, passing runs as short green ones
 * (an empty cell would look like "no data" and hide how often the target runs).
 */
export function historyBars(points: TrendPoint[]): HistoryBars {
  const completed = points.filter((point) => point.tests_failed !== null)
  return {
    values: completed.map((point) =>
      point.tests_failed ? failingHeight(point.tests_failed) : PASSED_HEIGHT,
    ),
    labels: completed.map(
      (point) =>
        `${dateTimeLabel(point.created_at)}: ${
          point.tests_failed ? `${point.tests_failed} failing` : 'passed'
        }`,
    ),
    colors: completed.map((point) => (point.tests_failed ? 'error' : 'success')),
  }
}
