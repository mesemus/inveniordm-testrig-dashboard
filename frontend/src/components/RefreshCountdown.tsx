import { Typography } from '@mui/material'
import { useEffect, useState } from 'react'

import { REFRESH_MS } from '../api/hooks'

export interface RefreshCountdownProps {
  /** False (no active run, or a query error) renders nothing and starts no timer. */
  active: boolean
  /** The active query's `dataUpdatedAt`; the countdown restarts after every fetch. */
  dataUpdatedAt: number
  isFetching: boolean
}

/**
 * "Refreshing in 42 s" for a page that polls every `REFRESH_MS`.
 *
 * Deliberately not `aria-live`: a screen reader would announce every second.
 */
export function RefreshCountdown({ active, dataUpdatedAt, isFetching }: RefreshCountdownProps) {
  const [now, setNow] = useState(() => Date.now())

  useEffect(() => {
    if (!active) {
      return
    }
    const timer = window.setInterval(() => setNow(Date.now()), 1000)
    return () => window.clearInterval(timer)
  }, [active])

  if (!active) {
    return null
  }
  const remaining = Math.max(0, Math.ceil((REFRESH_MS - (now - dataUpdatedAt)) / 1000))
  return (
    <Typography variant="caption" color="text.secondary">
      {isFetching || remaining === 0 ? 'Refreshing…' : `Refreshing in ${remaining} s`}
    </Typography>
  )
}
