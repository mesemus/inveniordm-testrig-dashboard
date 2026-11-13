/** Long names and messages are truncated in cells and tooltips (the drawer shows them in full). */
export function clip(text: string, max = 300): string {
  return text.length > max ? `${text.slice(0, max)}…` : text
}

export function formatDurationSeconds(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined || !Number.isFinite(seconds) || seconds < 0) {
    return '-'
  }
  const total = Math.round(seconds)
  const hours = Math.floor(total / 3600)
  const minutes = Math.floor((total % 3600) / 60)
  const rest = total % 60
  if (hours > 0) {
    return `${hours}h ${minutes}m`
  }
  if (minutes > 0) {
    return `${minutes}m ${rest}s`
  }
  return `${rest}s`
}

export function durationLabel(
  startedAt: string | null | undefined,
  finishedAt: string | null | undefined,
): string {
  if (!startedAt || !finishedAt) {
    return '-'
  }
  const seconds = (new Date(finishedAt).getTime() - new Date(startedAt).getTime()) / 1000
  return formatDurationSeconds(seconds)
}

/** Unknown counts (queued/running runs) are "-", not "0 / 0", which would read as "no failures". */
export function testCounts(
  failed: number | null | undefined,
  total: number | null | undefined,
): string {
  if (total === null || total === undefined) {
    return '-'
  }
  return `${failed ?? 0} / ${total}`
}

/** Only links the API itself built (the artifact redirect endpoints) are rendered. */
export function safeApiUrl(url: string | null | undefined): string | null {
  return url !== null && url !== undefined && url.startsWith('/api/') ? url : null
}

/** Short UTC date for compact labels and the matrix header (e.g. "11-13"). */
export function dateLabel(iso: string | null | undefined): string {
  if (!iso) {
    return '-'
  }
  const date = new Date(iso)
  return Number.isNaN(date.getTime()) ? '-' : date.toISOString().slice(5, 10)
}

/** UTC date and time for run pickers (e.g. "2026-11-13 16:08"). */
export function dateTimeLabel(iso: string | null | undefined): string {
  if (!iso) {
    return '-'
  }
  const date = new Date(iso)
  return Number.isNaN(date.getTime()) ? '-' : date.toISOString().slice(0, 16).replace('T', ' ')
}
