import { LinearProgress, Stack } from '@mui/material'

import { Fact } from './Fact'
import type { ProgressRun } from './progress'

/** How many package names the run page lists before it says "+N more". */
const MAX_NAMES = 10

/**
 * The run page's live progress facts (`Progress`, then `Failed so far`). Renders nothing for a
 * run that is not running: the final results are the truth, and progress is a hint.
 */
export function RunProgress({ run }: { run: ProgressRun }) {
  if (run.status !== 'running') {
    return null
  }
  const total = run.progress_total
  const done = run.progress_done
  const hasSnapshot = run.progress_at !== null && total !== null && done !== null
  const failed = run.progress_failed ?? []

  return (
    <>
      <Fact label="Progress">
        <Stack spacing={0.5} sx={{ minWidth: 180 }}>
          <span>{hasSnapshot ? `${done} / ${total} packages` : 'Preparing…'}</span>
          {hasSnapshot && total > 0 && (
            <LinearProgress
              variant="determinate"
              value={(done / total) * 100}
              aria-label="Packages finished"
              aria-valuetext={`${done} of ${total} packages`}
              sx={{ height: 4, borderRadius: 1 }}
            />
          )}
        </Stack>
      </Fact>
      {failed.length > 0 && (
        <Fact label="Failed so far">
          {failed.slice(0, MAX_NAMES).join(', ')}
          {failed.length > MAX_NAMES ? ` +${failed.length - MAX_NAMES} more` : ''}
        </Fact>
      )}
    </>
  )
}
