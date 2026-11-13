import Cancel from '@mui/icons-material/Cancel'
import CheckCircle from '@mui/icons-material/CheckCircle'
import ErrorOutlined from '@mui/icons-material/ErrorOutlined'
import HelpOutlined from '@mui/icons-material/HelpOutlined'
import RemoveCircleOutlined from '@mui/icons-material/RemoveCircleOutlined'
import ReportProblem from '@mui/icons-material/ReportProblem'
import Schedule from '@mui/icons-material/Schedule'
import Shuffle from '@mui/icons-material/Shuffle'
import { Box, Chip, CircularProgress, Tooltip } from '@mui/material'
import type { ReactElement } from 'react'

import type { StatusValue } from '../api/hooks'
import { STATUS_INK } from '../theme/overrides'

type ChipColor = keyof typeof STATUS_INK

interface StatusStyle {
  label: string
  color: ChipColor
  icon: ReactElement
}

export interface StatusProps {
  status: StatusValue
  size?: 'small' | 'medium'
  iconOnly?: boolean
  /** Used by `completed`: 0 failures is "Passed", anything else is "N failing". */
  failing?: number | null
  /** Matrices render hundreds of icons; use the parent's native `title` instead of a Tooltip. */
  noTooltip?: boolean
}

const TOOLTIPS: Partial<Record<StatusValue, string>> = {
  requested: 'Requested; waiting for the next dispatch tick',
  queued: 'Claimed and sent to GitHub, waiting for the workflow to appear',
  running: 'The workflow is running on GitHub',
  infra_error: 'The workflow failed before producing a usable report',
  lost: 'No GitHub run found within the configured hours (often an interrupted dispatch)',
  not_run: 'This package was not run in this configuration',
}

function styleFor(status: StatusValue, failing?: number | null): StatusStyle {
  switch (status) {
    case 'passed':
      return { label: 'Passed', color: 'success', icon: <CheckCircle /> }
    case 'failed':
      return { label: 'Failed', color: 'error', icon: <Cancel /> }
    case 'error':
      return { label: 'Error', color: 'error', icon: <ErrorOutlined /> }
    case 'skipped':
      return { label: 'Skipped', color: 'default', icon: <RemoveCircleOutlined /> }
    case 'flaky':
      return { label: 'Flaky', color: 'warning', icon: <Shuffle /> }
    case 'requested':
      return { label: 'Requested', color: 'default', icon: <Schedule /> }
    case 'queued':
      return { label: 'Queued', color: 'default', icon: <Schedule /> }
    case 'running':
      return {
        label: 'Running',
        color: 'info',
        icon: <CircularProgress size={12} color="inherit" />,
      }
    case 'completed':
      return failing !== null && failing !== undefined && failing > 0
        ? { label: `${failing} failing`, color: 'error', icon: <Cancel /> }
        : { label: 'Passed', color: 'success', icon: <CheckCircle /> }
    case 'infra_error':
      return { label: 'Infra error', color: 'warning', icon: <ReportProblem /> }
    case 'lost':
      return { label: 'Lost', color: 'default', icon: <HelpOutlined /> }
    case 'not_run':
      return { label: 'Not run', color: 'default', icon: <RemoveCircleOutlined /> }
  }
  // All union members are handled above; this makes a future addition a compile error.
  const unreachable: never = status
  throw new Error(`Unknown status: ${String(unreachable)}`)
}

function statusIcon(
  style: StatusStyle,
  size: 'small' | 'medium',
  tooltip: string,
  withTooltip: boolean,
): ReactElement {
  // Just the coloured icon, in the chips' colours (no filled disc: on the template's light
  // greens MUI's computed contrast text turned the icon black).
  const [light, dark] = STATUS_INK[style.color]
  const icon = (
    <Box
      component="span"
      role="img"
      aria-label={style.label}
      sx={(theme) => ({
        display: 'inline-flex',
        fontSize: size === 'medium' ? 28 : 22,
        color: light,
        '& > svg': { fontSize: 'inherit' },
        ...theme.applyStyles('dark', { color: dark }),
      })}
    >
      {style.icon}
    </Box>
  )
  return withTooltip ? <Tooltip title={tooltip}>{icon}</Tooltip> : icon
}

// oxlint-disable-next-line react/only-export-components -- also used for the filter labels
export function statusLabel(status: StatusValue): string {
  return styleFor(status).label
}

/** The semaphore used everywhere. Colour is never the only signal: an icon and a label too. */
export function Status({ status, size = 'small', iconOnly = false, failing, noTooltip = false }: StatusProps) {
  const style = styleFor(status, failing)
  const tooltip = TOOLTIPS[status] ?? style.label

  if (iconOnly) {
    return statusIcon(style, size, tooltip, !noTooltip)
  }

  return (
    <Tooltip title={tooltip}>
      <Chip size={size} color={style.color} icon={style.icon} label={style.label} />
    </Tooltip>
  )
}
