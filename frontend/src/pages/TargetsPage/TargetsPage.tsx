import { Chip, Link as MuiLink, Stack, Typography } from '@mui/material'
import { DataGrid } from '@mui/x-data-grid'
import type { GridColDef } from '@mui/x-data-grid'
import type { ReactNode } from 'react'
import { Suspense, lazy } from 'react'
import { Link as RouterLink, useNavigate } from 'react-router'

import type { RunStatus, TargetOut } from '../../api/hooks'
import { useTargets } from '../../api/hooks'
import { CLICKABLE_ROWS_SX, rowActivation } from '../../components/grid'
import { PageError, PageLoading } from '../../components/PageState'
import { PageHeader } from '../../components/PageHeader'
import { RelativeTime } from '../../components/RelativeTime'
import { failedSoFar, progressSummary } from '../../components/progress'
import { Status, statusLabel } from '../../components/Status'

const HistorySparkline = lazy(() =>
  import('../../components/HistorySparkline').then((module) => ({
    default: module.HistorySparkline,
  })),
)

const DASH = '—'

// The words "last completed" are gone: Result *is* the last completed run.
const LATEST_LABELS: Partial<Record<RunStatus, string>> = { completed: 'Completed' }

/** A quiet separator segment in the "Latest run" cell. */
function Caption({ children }: { children: ReactNode }) {
  return (
    <Typography component="span" variant="caption" color="text.secondary" noWrap>
      {children}
    </Typography>
  )
}

/** What happened last, as text (not a chip): icon, label, live progress and when. */
function LatestRun({ target }: { target: TargetOut }) {
  if (!target.enabled) {
    return <Chip label="Disabled" />
  }
  const latest = target.latest_run
  if (!latest) {
    return <>{DASH}</>
  }
  const summary = progressSummary(latest)
  const failed = failedSoFar(latest)
  return (
    <Stack direction="row" spacing={0.5} sx={{ alignItems: 'center', minWidth: 0 }}>
      <Status status={latest.status} failing={latest.tests_failed} iconOnly />
      <span>{LATEST_LABELS[latest.status] ?? statusLabel(latest.status)}</span>
      {summary && <Caption>· {summary}</Caption>}
      {failed > 0 && <Caption>· {failed} failed</Caption>}
      <Caption>
        · <RelativeTime date={latest.created_at} />
      </Caption>
    </Stack>
  )
}

const columns: GridColDef<TargetOut>[] = [
  {
    field: 'branch',
    headerName: 'Branch',
    width: 200,
    renderCell: (params) => (
      <MuiLink
        component={RouterLink}
        to={`/targets/${encodeURIComponent(params.row.branch)}`}
        onClick={(event) => event.stopPropagation()}
        sx={{ color: params.row.enabled ? undefined : 'text.secondary' }}
      >
        {params.row.branch}
      </MuiLink>
    ),
  },
  {
    field: 'result',
    headerName: 'Result',
    width: 150,
    renderCell: ({ row }) =>
      row.latest_completed_run ? (
        <Status status="completed" failing={row.latest_completed_run.tests_failed} />
      ) : (
        DASH
      ),
  },
  {
    field: 'tests',
    headerName: 'Tests',
    width: 110,
    align: 'right',
    headerAlign: 'right',
    renderCell: ({ row }) => (
      <span style={{ fontVariantNumeric: 'tabular-nums' }}>
        {row.latest_completed_run?.tests_total ?? DASH}
      </span>
    ),
  },
  {
    field: 'latest_run',
    headerName: 'Latest run',
    flex: 1,
    minWidth: 240,
    renderCell: ({ row }) => <LatestRun target={row} />,
  },
  {
    field: 'history',
    headerName: 'History',
    description: 'last 20 completed runs',
    width: 180,
    // The bars are right-aligned (newest = now), so the header sits above them.
    align: 'right',
    headerAlign: 'right',
    sortable: false,
    renderCell: ({ row }) => (
      <Suspense fallback={null}>
        <HistorySparkline branch={row.branch} />
      </Suspense>
    ),
  },
]

function byEnabledThenBranch(a: TargetOut, b: TargetOut): number {
  return Number(b.enabled) - Number(a.enabled) || a.branch.localeCompare(b.branch)
}

export function TargetsPage() {
  const navigate = useNavigate()
  const { data, isLoading, error } = useTargets()

  if (isLoading) {
    return <PageLoading />
  }
  if (error) {
    return <PageError error={error} />
  }

  const targets = [...(data ?? [])].sort(byEnabledThenBranch)

  return (
    <>
      <PageHeader title="Targets" />
      {targets.length === 0 ? (
        <Typography>No targets are configured.</Typography>
      ) : (
        <DataGrid
          rows={targets}
          columns={columns}
          getRowId={(row) => row.branch}
          density="compact"
          hideFooter
          disableColumnMenu
          disableColumnSorting
          disableRowSelectionOnClick
          autoHeight
          sx={CLICKABLE_ROWS_SX}
          {...rowActivation<TargetOut>((row) =>
            navigate(`/targets/${encodeURIComponent(row.branch)}`),
          )}
        />
      )}
    </>
  )
}
