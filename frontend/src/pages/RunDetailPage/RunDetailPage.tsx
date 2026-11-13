import OpenInNew from '@mui/icons-material/OpenInNew'
import {
  Alert,
  Box,
  Button,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  Link as MuiLink,
  Typography,
} from '@mui/material'
import { DataGrid } from '@mui/x-data-grid'
import type { GridColDef } from '@mui/x-data-grid'
import { useMemo, useState } from 'react'
import { Link as RouterLink, useNavigate, useParams } from 'react-router'

import { isMissingRecord, isUuid } from '../../api/client'
import type { PackageOut, PackageStatus } from '../../api/hooks'
import { REFRESH_MS, isActiveStatus, useRun } from '../../api/hooks'
import { ArtifactsMenu } from '../../components/ArtifactsMenu'
import { CompareMenu } from '../../components/CompareMenu'
import { Fact, Facts } from '../../components/Fact'
import { durationLabel, formatDurationSeconds, testCounts } from '../../components/format'
import { CLICKABLE_ROWS_SX, rowActivation } from '../../components/grid'
import { PageError, PageLoading } from '../../components/PageState'
import { PageHeader } from '../../components/PageHeader'
import { RefreshCountdown } from '../../components/RefreshCountdown'
import { RelativeTime } from '../../components/RelativeTime'
import { RerunButton } from '../../components/RerunButton'
import { RunProgress } from '../../components/RunProgress'
import { Status } from '../../components/Status'
import { PAGE_SIZES } from '../../hooks/useQueryParams'
import { NotFoundPage } from '../NotFoundPage/NotFoundPage'

function testsHref(uid: string, packageName?: string): string {
  const params = new URLSearchParams()
  if (packageName) {
    params.set('package', packageName)
  }
  return `/runs/${encodeURIComponent(uid)}/tests?${params.toString()}`
}

// Failing packages lead the table; everything else is alphabetical. 'repo' is always last.
const PACKAGE_STATUS_ORDER: Record<PackageStatus, number> = {
  failed: 0,
  error: 1,
  passed: 2,
  skipped: 2,
  not_run: 2,
}

function sortPackages(packages: PackageOut[]): PackageOut[] {
  const hasFailure = packages.some(
    (pkg) => pkg.status === 'failed' || pkg.status === 'error',
  )
  return [...packages].sort((a, b) => {
    const aRepo = a.package === 'repo'
    const bRepo = b.package === 'repo'
    if (aRepo !== bRepo) {
      return aRepo ? 1 : -1
    }
    if (hasFailure) {
      const byStatus = PACKAGE_STATUS_ORDER[a.status] - PACKAGE_STATUS_ORDER[b.status]
      if (byStatus !== 0) {
        return byStatus
      }
    }
    return a.package.localeCompare(b.package)
  })
}

/** A malformed id never reaches the API; a 404/422 from it is "no such run". */
export function RunDetailPage() {
  const { uid = '' } = useParams()
  if (!isUuid(uid)) {
    return <NotFoundPage />
  }
  return <RunDetailContent uid={uid} />
}

function RunDetailContent({ uid }: { uid: string }) {
  const navigate = useNavigate()
  // Re-run now navigates here, so this page is where a fresh run is watched.
  const { data: run, error, dataUpdatedAt, isFetching } = useRun(uid, {
    refetchInterval: (query) =>
      query.state.data !== undefined && isActiveStatus(query.state.data.status) ? REFRESH_MS : false,
    refetchOnWindowFocus: (query) =>
      query.state.data !== undefined && isActiveStatus(query.state.data.status),
  })
  const active = !error && isActiveStatus(run?.status)

  const packageColumns = useMemo<GridColDef<PackageOut>[]>(
    () => [
      {
        field: 'status',
        headerName: 'Status',
        width: 150,
        renderCell: (params) => <Status status={params.row.status} />,
      },
      {
        field: 'package',
        headerName: 'Package',
        width: 230,
        renderCell: (params) => (
          <MuiLink
            component={RouterLink}
            to={testsHref(uid, params.row.package)}
            onClick={(event) => event.stopPropagation()}
          >
            {params.row.package}
          </MuiLink>
        ),
      },
      { field: 'version', headerName: 'Version', width: 150 },
      {
        field: 'git_commit',
        headerName: 'Commit',
        width: 110,
        renderCell: (params) =>
          params.row.git_commit ? (
            <span title={params.row.git_commit}>{params.row.git_commit.slice(0, 7)}</span>
          ) : (
            '-'
          ),
      },
      {
        field: 'tests',
        headerName: 'Tests (failing / total)',
        width: 180,
        renderCell: (params) => testCounts(params.row.tests_failed, params.row.tests_total),
      },
      {
        field: 'duration',
        headerName: 'Duration',
        width: 110,
        renderCell: (params) => formatDurationSeconds(params.row.duration_s),
      },
      {
        field: 'artifacts',
        headerName: 'Artifacts',
        width: 110,
        sortable: false,
        renderCell: (params) => <ArtifactsMenu artifacts={params.row.artifacts} />,
      },
    ],
    [uid],
  )

  if (isMissingRecord(error)) {
    return <NotFoundPage />
  }
  if (error && !run) {
    return <PageError error={error} />
  }
  if (!run) {
    return <PageLoading />
  }

  const packages = sortPackages(run.packages)

  const failingLink = [
    `/runs/${encodeURIComponent(uid)}/tests?`,
    new URLSearchParams([
      ['status', 'failed'],
      ['status', 'error'],
    ]).toString(),
  ].join('')

  return (
    <>
      <PageHeader
        breadcrumbs={[
          { label: 'Targets', to: '/' },
          { label: run.branch, to: `/targets/${encodeURIComponent(run.branch)}` },
        ]}
        title={`Run ${run.uid.slice(0, 8)}`}
        titleAttribute={run.uid}
        status={<Status status={run.status} failing={run.tests_failed} size="medium" />}
        actions={
          <>
            <RefreshCountdown
              active={active}
              dataUpdatedAt={dataUpdatedAt}
              isFetching={isFetching}
            />
            {(run.tests_failed ?? 0) > 0 && (
              <Button component={RouterLink} to={failingLink} variant="outlined" size="small">
                Failing tests
              </Button>
            )}
            <CompareMenu run={run} />
            <RerunButton branch={run.branch} />
          </>
        }
      />

      {/* Facts about the run, not actions: quiet label/value pairs. The only link is external. */}
      <Facts>
        <Fact label="Created">
          <RelativeTime date={run.created_at} />
        </Fact>
        <Fact label="Started">
          <RelativeTime date={run.started_at} />
        </Fact>
        <Fact label="Finished">
          <RelativeTime date={run.finished_at} />
        </Fact>
        <Fact label="Duration">{durationLabel(run.started_at, run.finished_at)}</Fact>
        <RunProgress run={run} />
        <Fact label="testrig">{run.testrig_ref}</Fact>
        {run.testrig_config && (
          <Fact label="Config">
            <ConfigLink config={run.testrig_config} />
          </Fact>
        )}
        {run.gh_run_url?.startsWith('https://github.com/') && (
          <Fact label="Workflow">
            <MuiLink href={run.gh_run_url} target="_blank" rel="noopener noreferrer">
              GitHub Actions run
              <OpenInNew aria-hidden sx={{ fontSize: '0.9em', ml: 0.5, verticalAlign: '-0.1em' }} />
            </MuiLink>
          </Fact>
        )}
      </Facts>

      {run.error && (
        <Alert severity="error" sx={{ mb: 2 }}>
          {run.error}
        </Alert>
      )}

      {packages.length === 0 ? (
        <Typography>
          No package results. A run that is not <code>completed</code> keeps no results.
        </Typography>
      ) : (
        <Box sx={{ height: 620, width: '100%' }}>
          <DataGrid
            rows={packages}
            columns={packageColumns}
            getRowId={(row) => row.package}
            initialState={{ pagination: { paginationModel: { pageSize: 100 } } }}
            pageSizeOptions={[...PAGE_SIZES]}
            disableRowSelectionOnClick
            sx={CLICKABLE_ROWS_SX}
            {...rowActivation<PackageOut>((row) => navigate(testsHref(uid, row.package)))}
          />
        </Box>
      )}
    </>
  )
}


/** The run's testrig config: rarely needed, so a dialog instead of a block on the page. */
function ConfigLink({ config }: { config: string }) {
  const [open, setOpen] = useState(false)
  return (
    <>
      <MuiLink component="button" type="button" variant="body2" onClick={() => setOpen(true)}>
        View
      </MuiLink>
      <Dialog open={open} onClose={() => setOpen(false)} maxWidth="md" fullWidth>
        <DialogTitle>testrig config</DialogTitle>
        <DialogContent dividers>
          <Box component="pre" sx={{ m: 0, overflow: 'auto', fontFamily: 'monospace', fontSize: 12 }}>
            {config}
          </Box>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setOpen(false)}>Close</Button>
        </DialogActions>
      </Dialog>
    </>
  )
}
