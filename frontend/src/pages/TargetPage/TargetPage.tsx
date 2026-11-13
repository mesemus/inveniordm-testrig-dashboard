import { Box, Stack, Tab, Tabs, Typography } from '@mui/material'
import { DataGrid } from '@mui/x-data-grid'
import type { GridColDef, GridPaginationModel } from '@mui/x-data-grid'
import { Suspense, lazy } from 'react'
import { useNavigate, useParams } from 'react-router'

import { isNotFound } from '../../api/client'
import type { RunListItem } from '../../api/hooks'
import { REFRESH_MS, isActive, isActiveStatus, useRuns, useTargets } from '../../api/hooks'
import { clip, durationLabel, testCounts } from '../../components/format'
import { CLICKABLE_ROWS_SX, rowActivation } from '../../components/grid'
import { PageError, PageLoading } from '../../components/PageState'
import { PageHeader } from '../../components/PageHeader'
import { RefreshCountdown } from '../../components/RefreshCountdown'
import { RelativeTime } from '../../components/RelativeTime'
import { RerunButton } from '../../components/RerunButton'
import { progressSummary } from '../../components/progress'
import { Status } from '../../components/Status'
import { PAGE_SIZES, readPagination, useQueryParams } from '../../hooks/useQueryParams'
import { NotFoundPage } from '../NotFoundPage/NotFoundPage'

const MatrixTab = lazy(() =>
  import('./MatrixTab').then((module) => ({ default: module.MatrixTab })),
)
const TrendTab = lazy(() => import('./TrendTab').then((module) => ({ default: module.TrendTab })))

const TABS = ['runs', 'matrix', 'trend'] as const
type TabValue = (typeof TABS)[number]

const columns: GridColDef<RunListItem>[] = [
  {
    field: 'status',
    headerName: 'Status',
    width: 170,
    renderCell: (params) => {
      const summary = progressSummary(params.row)
      return (
        <Stack direction="row" spacing={1} sx={{ alignItems: 'center' }}>
          <Status status={params.row.status} failing={params.row.tests_failed} />
          {summary && (
            <Typography variant="caption" color="text.secondary">
              {summary}
            </Typography>
          )}
        </Stack>
      )
    },
  },
  {
    field: 'created_at',
    headerName: 'Created',
    width: 140,
    renderCell: (params) => <RelativeTime date={params.row.created_at} />,
  },
  {
    field: 'trigger',
    headerName: 'Trigger',
    width: 160,
    renderCell: (params) =>
      params.row.trigger === 'manual'
        ? `manual${params.row.triggered_by ? ` (${params.row.triggered_by})` : ''}`
        : 'schedule',
  },
  {
    field: 'duration',
    headerName: 'Duration',
    width: 110,
    renderCell: (params) => durationLabel(params.row.started_at, params.row.finished_at),
  },
  {
    field: 'tests',
    headerName: 'Tests (failing / total)',
    width: 180,
    renderCell: (params) => testCounts(params.row.tests_failed, params.row.tests_total),
  },
  {
    field: 'error',
    headerName: 'Error',
    flex: 1,
    minWidth: 200,
    renderCell: (params) =>
      params.row.error ? <span title={clip(params.row.error)}>{params.row.error}</span> : '-',
  },
]

const rowsActive = (rows?: RunListItem[]) =>
  rows?.some((row) => isActiveStatus(row.status)) ?? false

type RunsQuery = ReturnType<typeof useRuns>

function RunsTab({
  runs,
  page,
  pageSize,
  onPageChange,
}: {
  runs: RunsQuery
  page: number
  pageSize: number
  onPageChange: (model: GridPaginationModel) => void
}) {
  const navigate = useNavigate()
  const { data, isLoading, isFetching, error } = runs

  if (isNotFound(error)) {
    return <NotFoundPage />
  }
  if (error && !data) {
    return <PageError error={error} />
  }
  if (isLoading && !data) {
    return <PageLoading />
  }

  return (
    <Box sx={{ height: 620, width: '100%' }}>
      <DataGrid
        rows={data?.items ?? []}
        columns={columns}
        getRowId={(row) => row.uid}
        loading={isFetching}
        paginationMode="server"
        rowCount={data?.total ?? 0}
        paginationModel={{ page, pageSize }}
        onPaginationModelChange={onPageChange}
        pageSizeOptions={[...PAGE_SIZES]}
        disableRowSelectionOnClick
        sx={CLICKABLE_ROWS_SX}
        {...rowActivation<RunListItem>((row) => navigate(`/runs/${encodeURIComponent(row.uid)}`))}
      />
    </Box>
  )
}

export function TargetPage() {
  const { branch = '' } = useParams()
  const { get, update } = useQueryParams()
  const tabParam = get('tab')
  const tab: TabValue = TABS.includes(tabParam as TabValue) ? (tabParam as TabValue) : 'runs'
  const { page, pageSize } = readPagination(get)

  // Both queries must refresh: polling with a stale latest_run.status would never stop.
  const targets = useTargets({
    refetchInterval: (query) => (isActive(query.state.data, branch) ? REFRESH_MS : false),
    refetchOnWindowFocus: (query) => isActive(query.state.data, branch),
  })
  const active = !targets.error && isActive(targets.data, branch)
  // The runs query also polls while one of its own rows is active. Otherwise, if the targets
  // query sees the run finish first, `active` turns false before the table ever fetched the
  // final status, and the table freezes showing `running`.
  const runs = useRuns(
    branch,
    { page, pageSize },
    {
      enabled: branch !== '' && tab === 'runs',
      refetchInterval: (query) =>
        active || rowsActive(query.state.data?.items) ? REFRESH_MS : false,
      refetchOnWindowFocus: (query) => active || rowsActive(query.state.data?.items),
    },
  )
  const polling = active || rowsActive(runs.data?.items)

  const changePage = (model: GridPaginationModel) => {
    update({ page: String(model.page), pageSize: String(model.pageSize) })
  }

  return (
    <>
      <PageHeader
        breadcrumbs={[{ label: 'Targets', to: '/' }]}
        title={branch}
        actions={
          <>
            <RefreshCountdown
              active={tab === 'runs' && polling && !runs.error}
              dataUpdatedAt={runs.dataUpdatedAt}
              isFetching={runs.isFetching}
            />
            <RerunButton branch={branch} />
          </>
        }
      />
      <Tabs
        value={tab}
        onChange={(_event, value: TabValue) =>
          update({ tab: value === 'runs' ? null : value })
        }
        sx={{ mb: 2, borderBottom: 1, borderColor: 'divider' }}
      >
        <Tab value="runs" label="Runs" />
        <Tab value="matrix" label="Matrix" />
        <Tab value="trend" label="Trend" />
      </Tabs>

      {tab === 'runs' && (
        <RunsTab runs={runs} page={page} pageSize={pageSize} onPageChange={changePage} />
      )}
      {tab === 'matrix' && (
        <Suspense fallback={<PageLoading />}>
          <MatrixTab branch={branch} />
        </Suspense>
      )}
      {tab === 'trend' && (
        <Suspense fallback={<PageLoading />}>
          <TrendTab branch={branch} />
        </Suspense>
      )}
    </>
  )
}
