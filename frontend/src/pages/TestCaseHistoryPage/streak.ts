import type { HistoryOut } from '../../api/hooks'
import { dateLabel } from '../../components/format'

export type HistoryItem = HistoryOut['items'][number]

export function lastFailing(items: HistoryItem[]): HistoryItem | undefined {
  for (let index = items.length - 1; index >= 0; index -= 1) {
    const status = items[index].status
    if (status === 'failed' || status === 'error') {
      return items[index]
    }
  }
  return undefined
}

/** "failing in the last N runs" / "passing since MM-DD" / "not run in the latest run". */
export function streakText(items: HistoryItem[]): string {
  if (items.length === 0) {
    return 'no completed runs for this target'
  }
  const latest = items[items.length - 1].status
  if (latest === null) {
    return 'not run in the latest run'
  }
  if (latest === 'failed' || latest === 'error') {
    let count = 0
    for (let index = items.length - 1; index >= 0; index -= 1) {
      const status = items[index].status
      if (status === 'failed' || status === 'error') {
        count += 1
      } else {
        break
      }
    }
    return `failing in the last ${count} run${count === 1 ? '' : 's'}`
  }
  if (latest === 'passed') {
    let firstPassing = items[items.length - 1]
    for (let index = items.length - 1; index >= 0; index -= 1) {
      if (items[index].status !== 'passed') {
        break
      }
      firstPassing = items[index]
    }
    return `passing since ${dateLabel(firstPassing.created_at)}`
  }
  return 'skipped in the latest run'
}
