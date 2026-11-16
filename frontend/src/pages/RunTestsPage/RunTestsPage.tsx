import Close from '@mui/icons-material/Close'
import {
  Alert,
  Box,
  Checkbox,
  CircularProgress,
  Divider,
  Drawer,
  FormControlLabel,
  FormGroup,
  IconButton,
  Link as MuiLink,
  MenuItem,
  Stack,
  TextField,
  Tooltip,
  Typography,
} from '@mui/material'
import { DataGrid } from '@mui/x-data-grid'
import type { GridColDef, GridPaginationModel } from '@mui/x-data-grid'
import { useEffect, useState } from 'react'
import { Link as RouterLink, useParams } from 'react-router'

import { errorText, isNotFound, isUuid } from '../../api/client'
import type { PackageOut, TestListItem, TestStatusName } from '../../api/hooks'
import { useRun, useRunTest, useRunTests } from '../../api/hooks'
import { clip, formatDurationSeconds, safeApiUrl } from '../../components/format'
import { CLICKABLE_ROWS_SX, rowActivation } from '../../components/grid'
import { PageError, PageLoading } from '../../components/PageState'
import { PageHeader } from '../../components/PageHeader'
import { Status, statusLabel } from '../../components/Status'
import { PAGE_SIZES, readPagination, useQueryParams } from '../../hooks/useQueryParams'
import { NotFoundPage } from '../NotFoundPage/NotFoundPage'

const STATUS_OPTIONS: TestStatusName[] = ['failed', 'error', 'passed', 'skipped']
const STATUS_SET = new Set<string>(STATUS_OPTIONS)
const SEARCH_DELAY_MS = 300
const MAX_QUERY = 200

const COLUMNS: GridColDef<TestListItem>[] = [
  {
    field: 'status',
    headerName: 'Status',
    width: 170,
    renderCell: (params) => (
      <Stack direction="row" spacing={0.5} sx={{ alignItems: 'center' }}>
        <Status status={params.row.status} />
        {params.row.flaky && <Status status="flaky" iconOnly />}
      </Stack>
    ),
  },
  { field: 'package', headerName: 'Package', width: 190 },
  { field: 'classname', headerName: 'Module', width: 200 },
  {
    field: 'name',
    headerName: 'Name',
    flex: 1,
    minWidth: 260,
    renderCell: (params) => (
      <Tooltip title={clip(params.row.name)}>
        <span>{params.row.name}</span>
      </Tooltip>
    ),
  },
  {
    field: 'duration_s',
    headerName: 'Duration',
    width: 110,
    renderCell: (params) => formatDurationSeconds(params.row.duration_s),
  },
]

function parseStatuses(values: string[]): TestStatusName[] {
  return values.filter((value): value is TestStatusName => STATUS_SET.has(value))
}

function RunTestDrawer({
  uid,
  testCaseId,
  branch,
  packages,
  onClose,
}: {
  uid: string
  testCaseId: number | null
  branch: string
  packages: PackageOut[]
  onClose: () => void
}) {
  const { data, isLoading, error } = useRunTest(uid, testCaseId)
  const selectedPackage = data ? packages.find((item) => item.package === data.package) : undefined
  const junitUrl = safeApiUrl(selectedPackage?.artifacts.junit)
  const historyHref = data
    ? `/test-cases/${data.test_case_id}?${new URLSearchParams({ target: branch }).toString()}`
    : '#'

  return (
    <>
      <Box sx={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', p: 2 }}>
        <Typography variant="h6">Test detail</Typography>
        <IconButton aria-label="Close test detail" onClick={onClose}>
          <Close />
        </IconButton>
      </Box>
      <Divider />
      <Box sx={{ p: 2 }}>
        {isLoading && <CircularProgress size={24} />}
        {error && <Alert severity="error">{errorText(error)}</Alert>}
        {data && (
          <>
            <Stack direction="row" spacing={1} sx={{ mb: 1, alignItems: 'center' }}>
              <Status status={data.status} />
              <Typography variant="body2">{data.package}</Typography>
              <Typography variant="body2" color="text.secondary">
                {formatDurationSeconds(data.duration_s)}
              </Typography>
            </Stack>
            <Typography variant="subtitle2" sx={{ wordBreak: 'break-all' }}>
              {data.classname ? `${data.classname}::${data.name}` : data.name}
            </Typography>
            <Typography variant="subtitle2" sx={{ mt: 2 }}>
              Message
            </Typography>
            <Box
              component="pre"
              sx={{
                m: 0,
                p: 1,
                whiteSpace: 'pre-wrap',
                wordBreak: 'break-all',
                fontFamily: 'monospace',
                fontSize: 12,
                bgcolor: 'action.hover',
                maxHeight: '50vh',
                overflow: 'auto',
              }}
            >
              {data.message || '(no message)'}
            </Box>
            <Stack direction="row" spacing={2} sx={{ mt: 2 }}>
              {junitUrl && (
                <MuiLink href={junitUrl} target="_blank" rel="noopener noreferrer">
                  junit.xml
                </MuiLink>
              )}
              <MuiLink component={RouterLink} to={historyHref}>
                Test history
              </MuiLink>
            </Stack>
          </>
        )}
      </Box>
    </>
  )
}

/** A malformed id never reaches the API; a 404 from it is "no such run". */
export function RunTestsPage() {
  const { uid = '' } = useParams()
  if (!isUuid(uid)) {
    return <NotFoundPage />
  }
  return <RunTestsContent uid={uid} />
}

function RunTestsContent({ uid }: { uid: string }) {
  const { get, getAll, update } = useQueryParams()
  const { page, pageSize } = readPagination(get)
  const urlQ = get('q') ?? ''
  const packageFilter = get('package') ?? ''
  const selectedStatuses = parseStatuses(getAll('status'))
  const [draftSearch, setDraftSearch] = useState<string | null>(null)
  const [previousUrlQ, setPreviousUrlQ] = useState(urlQ)
  const [selectedTest, setSelectedTest] = useState<number | null>(null)

  // React's "adjust state when props change" pattern: the URL is the source of truth, so when it
  // changes (back button, shared link) the draft is dropped. Deliberately during render, not in
  // an effect, which would cause a second fetch.
  if (urlQ !== previousUrlQ) {
    setPreviousUrlQ(urlQ)
    if (draftSearch !== null) {
      setDraftSearch(null)
    }
  }
  const search = draftSearch ?? urlQ

  const runQuery = useRun(uid)
  const testsQuery = useRunTests(uid, {
    page,
    pageSize,
    package: packageFilter || undefined,
    status: selectedStatuses.length > 0 ? selectedStatuses : undefined,
    q: urlQ || undefined,
  })

  // Debounce typing into the URL.
  useEffect(() => {
    if (draftSearch === null || draftSearch === urlQ) {
      return
    }
    const timer = window.setTimeout(() => {
      update({ q: draftSearch === '' ? null : draftSearch.slice(0, MAX_QUERY), page: '0' })
    }, SEARCH_DELAY_MS)
    return () => window.clearTimeout(timer)
  }, [draftSearch, urlQ, update])

  if (isNotFound(runQuery.error) || isNotFound(testsQuery.error)) {
    return <NotFoundPage />
  }
  if (runQuery.error && !runQuery.data) {
    return <PageError error={runQuery.error} />
  }
  if (testsQuery.error && !testsQuery.data) {
    return <PageError error={testsQuery.error} />
  }
  if (!testsQuery.data) {
    return <PageLoading />
  }

  const run = runQuery.data
  const packageNames = (run?.packages ?? []).map((item) => item.package)

  const toggleStatus = (status: TestStatusName, checked: boolean) => {
    const next = checked
      ? [...selectedStatuses, status]
      : selectedStatuses.filter((item) => item !== status)
    update({ status: next.length > 0 ? next : null, page: '0' })
  }

  const changePage = (model: GridPaginationModel) => {
    update({ page: String(model.page), pageSize: String(model.pageSize) })
  }

  return (
    <>
      <PageHeader
        breadcrumbs={[
          { label: 'Targets', to: '/' },
          ...(run
            ? [{ label: run.branch, to: `/targets/${encodeURIComponent(run.branch)}` }]
            : []),
          { label: `Run ${uid.slice(0, 8)}`, to: `/runs/${encodeURIComponent(uid)}` },
        ]}
        title="Tests"
      />

      <Stack direction="row" spacing={2} useFlexGap sx={{ mb: 2, flexWrap: 'wrap' }}>
        <TextField
          select
          size="small"
          label="Package"
          value={packageFilter}
          onChange={(event) =>
            update({ package: event.target.value === '' ? null : event.target.value, page: '0' })
          }
          sx={{ minWidth: 220 }}
        >
          <MenuItem value="">All packages</MenuItem>
          {packageNames.map((name) => (
            <MenuItem key={name} value={name}>
              {name}
            </MenuItem>
          ))}
        </TextField>
        <TextField
          size="small"
          label="Search name"
          value={search}
          onChange={(event) => setDraftSearch(event.target.value)}
          slotProps={{ htmlInput: { maxLength: MAX_QUERY } }}
          sx={{ minWidth: 260 }}
        />
        <FormGroup row>
          {STATUS_OPTIONS.map((status) => (
            <FormControlLabel
              key={status}
              control={
                <Checkbox
                  checked={selectedStatuses.includes(status)}
                  onChange={(event) => toggleStatus(status, event.target.checked)}
                />
              }
              label={statusLabel(status)}
            />
          ))}
        </FormGroup>
      </Stack>

      <Box sx={{ height: 620, width: '100%' }}>
        <DataGrid
          rows={testsQuery.data.items}
          columns={COLUMNS}
          getRowId={(row) => row.test_case_id}
          loading={testsQuery.isFetching}
          paginationMode="server"
          rowCount={testsQuery.data.total}
          paginationModel={{ page, pageSize }}
          onPaginationModelChange={changePage}
          pageSizeOptions={[...PAGE_SIZES]}
          disableRowSelectionOnClick
          sx={CLICKABLE_ROWS_SX}
          {...rowActivation<TestListItem>((row) => setSelectedTest(row.test_case_id))}
        />
      </Box>

      <Drawer anchor="right" open={selectedTest !== null} onClose={() => setSelectedTest(null)}>
        <Box sx={{ width: { xs: '92vw', sm: 640 } }}>
          <RunTestDrawer
            uid={uid}
            testCaseId={selectedTest}
            branch={run?.branch ?? ''}
            packages={run?.packages ?? []}
            onClose={() => setSelectedTest(null)}
          />
        </Box>
      </Drawer>
    </>
  )
}
