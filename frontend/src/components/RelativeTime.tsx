import { Tooltip } from '@mui/material'
import { useEffect, useState } from 'react'

const UNITS: Array<[Intl.RelativeTimeFormatUnit, number]> = [
  ['year', 365 * 24 * 3600],
  ['month', 30 * 24 * 3600],
  ['day', 24 * 3600],
  ['hour', 3600],
  ['minute', 60],
  ['second', 1],
]

const formatter = new Intl.RelativeTimeFormat('en', { numeric: 'auto' })

function relativeText(date: Date, now: Date): string {
  const seconds = (date.getTime() - now.getTime()) / 1000
  const magnitude = Math.abs(seconds)
  for (const [unit, size] of UNITS) {
    if (magnitude >= size || unit === 'second') {
      return formatter.format(Math.round(seconds / size), unit)
    }
  }
  return formatter.format(0, 'second')
}

export interface RelativeTimeProps {
  date: string | null | undefined
  /** Injectable for tests; defaults to a clock that ticks once a minute. */
  now?: Date
}

function useNow(intervalMs = 60_000): Date {
  const [now, setNow] = useState(() => new Date())
  useEffect(() => {
    const timer = window.setInterval(() => setNow(new Date()), intervalMs)
    return () => window.clearInterval(timer)
  }, [intervalMs])
  return now
}

/** "3 hours ago", with the exact UTC time as a tooltip. Ticks once a minute. */
export function RelativeTime({ date, now }: RelativeTimeProps) {
  const ticking = useNow()
  const current = now ?? ticking
  if (!date) {
    return <>-</>
  }
  const parsed = new Date(date)
  if (Number.isNaN(parsed.getTime())) {
    return <>-</>
  }
  return (
    <Tooltip title={parsed.toISOString()}>
      <span>{relativeText(parsed, current)}</span>
    </Tooltip>
  )
}
