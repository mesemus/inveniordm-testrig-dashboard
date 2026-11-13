import type { TrendPoint } from '../api/hooks'
import { dateTimeLabel } from './format'

export interface HistoryBars {
  /** Bar heights: the failing count, or a short bar for a passing run. */
  values: number[]
  /** One unique label per bar; the tooltip header. */
  labels: string[]
  colors: ('error' | 'success')[]
  max: number
}

/** How tall a passing run's bar is, relative to the worst failing count. */
const PASSED_HEIGHT = 0.15

/**
 * Bars for the History sparkline: failing runs as red bars, passing runs as short green ones
 * (an empty cell would look like "no data" and hide how often the target runs).
 */
export function historyBars(points: TrendPoint[]): HistoryBars {
  const completed = points.filter((point) => point.tests_failed !== null)
  const max = Math.max(1, ...completed.map((point) => point.tests_failed ?? 0))
  const passedHeight = max * PASSED_HEIGHT
  return {
    values: completed.map((point) =>
      point.tests_failed ? point.tests_failed : passedHeight,
    ),
    labels: completed.map(
      (point) =>
        `${dateTimeLabel(point.created_at)}: ${
          point.tests_failed ? `${point.tests_failed} failing` : 'passed'
        }`,
    ),
    colors: completed.map((point) => (point.tests_failed ? 'error' : 'success')),
    max,
  }
}
