import type { StatusValue } from '../api/hooks'

/** The fields every progress view needs; `RunDetail`, `RunListItem` and `RunSummary` all have them. */
export interface ProgressRun {
  status: StatusValue
  progress_total: number | null
  progress_done: number | null
  progress_failed: string[] | null
  progress_at: string | null
}

/** "34/58" for a running run with a snapshot; null when there is nothing to show. */
export function progressSummary(run: ProgressRun): string | null {
  if (run.status !== 'running' || run.progress_at === null) {
    return null
  }
  if (run.progress_done === null || run.progress_total === null) {
    return null
  }
  return `${run.progress_done}/${run.progress_total}`
}

/** Packages that have failed so far in a running run (0 when unknown). */
export function failedSoFar(run: ProgressRun): number {
  return run.status === 'running' ? (run.progress_failed?.length ?? 0) : 0
}
