import {
  Box,
  Link as MuiLink,
  Paper,
  Tab,
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableRow,
  Tabs,
  Typography,
} from '@mui/material'
import { DataGrid } from '@mui/x-data-grid'
import type { GridColDef, GridPaginationModel } from '@mui/x-data-grid'
import { useEffect } from 'react'
import { Link as RouterLink, useNavigate } from 'react-router'

import { isMissingRecord, isUuid } from '../../api/client'
import type { CompareKind, CompareTestItem } from '../../api/hooks'
import type { RunDetail } from '../../api/hooks'
import { useCompare, useCompareTests, useRun } from '../../api/hooks'
import { Fact, Facts } from '../../components/Fact'
import { clip } from '../../components/format'
import { CLICKABLE_ROWS_SX, rowActivation } from '../../components/grid'
import { PageError, PageLoading } from '../../components/PageState'
import { PageHeader } from '../../components/PageHeader'
import { RelativeTime } from '../../components/RelativeTime'
import { Status } from '../../components/Status'
import { PAGE_SIZES, readPagination, useQueryParams } from '../../hooks/useQueryParams'
import { NotFoundPage } from '../NotFoundPage/NotFoundPage'

// One row of tabs for everything the comparison shows: the three test lists first, then the
// two package views. The first tab is the default.
type CompareTab = CompareKind | 'changed' | 'packages'

const TAB_LABELS: Record<CompareTab, string> = {
  flipped: 'Flipped tests',
  new: 'New tests',
  gone: 'Gone tests',
  changed: 'Changed packages',
  packages: 'All packages',
}

const TABS = Object.keys(TAB_LABELS) as CompareTab[]

function isTestTab(tab: CompareTab): tab is CompareKind {
  return tab !== 'changed' && tab !== 'packages'
}

const columns: GridColDef<CompareTestItem>[] = [
  { field: 'package', headerName: 'Package', width: 200 },
  {
    field: 'name',
    headerName: 'Test',
    flex: 1,
    minWidth: 260,
    renderCell: (params) => <span title={clip(params.row.name)}>{params.row.name}</span>,
  },
  {
    field: 'base_status',
    headerName: 'Base',
    width: 140,
    renderCell: (params) =>
      params.row.base_status ? <Status status={params.row.base_status} /> : '-',
  },
  {
    field: 'head_status',
    headerName: 'Head',
    width: 140,
    renderCell: (params) =>
      params.row.head_status ? <Status status={params.row.head_status} /> : '-',
  },
]

/** Validate the ids in the URL before any request; an invalid uuid is "not found". */
export function ComparePage() {
  const { get } = useQueryParams()
  const base = get('base') ?? ''
  const head = get('head') ?? ''
  if ((base !== '' && !isUuid(base)) || (head !== '' && !isUuid(head))) {
    return <NotFoundPage />
  }
  return <CompareContent base={base} head={head} />
}

function CompareContent({ base, head }: { base: string; head: string }) {
  const navigate = useNavigate()
  const { get, update } = useQueryParams()
  const tabParam = get('tab')
  const tab: CompareTab = TABS.includes(tabParam as CompareTab) ? (tabParam as CompareTab) : TABS[0]
  // A package tab keeps the (default) flipped query, so going back to it is instant.
  const kind: CompareKind = isTestTab(tab) ? tab : 'flipped'
  const { page, pageSize } = readPagination(get)
  const headRun = useRun(head)
  const baseRun = useRun(base)
  const target = headRun.data?.branch ?? baseRun.data?.branch ?? ''

  // A link may name only the head run: compare it with its previous completed run.
  useEffect(() => {
    if (base === '' && head !== '' && headRun.data?.previous_completed_uid) {
      update({ base: headRun.data.previous_completed_uid }, { replace: true })
    }
  }, [base, head, headRun.data, update])

  const compare = useCompare(base, head)
  const tests = useCompareTests(base, head, kind, page, pageSize)

  if (
    isMissingRecord(headRun.error) ||
    isMissingRecord(baseRun.error) ||
    isMissingRecord(compare.error) ||
    isMissingRecord(tests.error)
  ) {
    return <NotFoundPage />
  }
  const error = headRun.error ?? baseRun.error ?? compare.error ?? tests.error

  const changePage = (model: GridPaginationModel) => {
    update({ page: String(model.page), pageSize: String(model.pageSize) })
  }
  const changeTab = (value: CompareTab) => {
    update({ tab: value === TABS[0] ? null : value, page: '0' })
  }

  // No pickers: the runs are chosen on the run page ("Compare with…"); this page only compares.
  const header = (
    <>
      <PageHeader
        breadcrumbs={[
          { label: 'Targets', to: '/' },
          ...(target ? [{ label: target, to: `/targets/${encodeURIComponent(target)}` }] : []),
        ]}
        title="Compare runs"
      />
      {baseRun.data && headRun.data && (
        <Facts>
          <Fact label="Base">
            <RunSummaryLink run={baseRun.data} />
          </Fact>
          <Fact label="Head">
            <RunSummaryLink run={headRun.data} />
          </Fact>
        </Facts>
      )}
    </>
  )

  if (error) {
    return (
      <>
        {header}
        <PageError error={error} />
      </>
    )
  }

  if (!compare.data) {
    if (compare.isLoading || (base === '' && headRun.isLoading)) {
      return <PageLoading />
    }
    return (
      <>
        {header}
        <Typography>Nothing to compare: open a run and use “Compare with…”.</Typography>
      </>
    )
  }

  const comparison = compare.data
  const packages = comparison.packages
  const changed = packages.filter((pkg) => pkg.changed)
  const counts: Record<CompareTab, number> = {
    changed: changed.length,
    packages: packages.length,
    ...comparison.counts,
  }
  const shown = tab === 'changed' ? changed : packages

  return (
    <>
      {header}

      <Tabs
        value={tab}
        onChange={(_event, value: CompareTab) => changeTab(value)}
        variant="scrollable"
        sx={{ mb: 2, borderBottom: 1, borderColor: 'divider' }}
      >
        {TABS.map((value) => (
          <Tab key={value} value={value} label={`${TAB_LABELS[value]} (${counts[value]})`} />
        ))}
      </Tabs>

      {!isTestTab(tab) &&
        (shown.length === 0 ? (
          <Typography>No package changed between the two runs.</Typography>
        ) : (
          <Paper>
            <Table size="small" aria-label={TAB_LABELS[tab]}>
              <TableHead>
                <TableRow>
                  <TableCell>Package</TableCell>
                  <TableCell>Version (base → head)</TableCell>
                  <TableCell>Commit (base → head)</TableCell>
                  <TableCell>Status (base → head)</TableCell>
                </TableRow>
              </TableHead>
              <TableBody>
                {shown.map((pkg) => (
                  <TableRow key={pkg.package}>
                    <TableCell>{pkg.package}</TableCell>
                    <TableCell>
                      {pkg.base_version ?? '-'} → {pkg.head_version ?? '-'}
                    </TableCell>
                    <TableCell>
                      <span title={pkg.base_commit ?? undefined}>
                        {pkg.base_commit ? pkg.base_commit.slice(0, 7) : '-'}
                      </span>{' '}
                      →{' '}
                      <span title={pkg.head_commit ?? undefined}>
                        {pkg.head_commit ? pkg.head_commit.slice(0, 7) : '-'}
                      </span>
                    </TableCell>
                    <TableCell>
                      {pkg.base_status ? <Status status={pkg.base_status} /> : '-'} →{' '}
                      {pkg.head_status ? <Status status={pkg.head_status} /> : '-'}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </Paper>
        ))}

      {isTestTab(tab) && (
        <Box sx={{ height: 520, width: '100%' }}>
          <DataGrid
            rows={tests.data?.items ?? []}
            columns={columns}
            getRowId={(row) => row.test_case_id}
            loading={tests.isFetching}
            paginationMode="server"
            rowCount={tests.data?.total ?? 0}
            paginationModel={{ page, pageSize }}
            onPaginationModelChange={changePage}
            pageSizeOptions={[...PAGE_SIZES]}
            disableRowSelectionOnClick
            sx={CLICKABLE_ROWS_SX}
            {...rowActivation<CompareTestItem>((row) =>
              navigate(
                `/test-cases/${row.test_case_id}?${new URLSearchParams({ target })}`,
              ),
            )}
          />
        </Box>
      )}
    </>
  )
}

/** "Run f27a0bc9 · 2 days ago · 7 failing", the run id linking to the run page. */
function RunSummaryLink({ run }: { run: RunDetail }) {
  return (
    <>
      <MuiLink component={RouterLink} to={`/runs/${encodeURIComponent(run.uid)}`}>
        Run {run.uid.slice(0, 8)}
      </MuiLink>
      {' · '}
      <RelativeTime date={run.created_at} />
      {' · '}
      {run.tests_failed ? `${run.tests_failed} failing` : 'passed'}
    </>
  )
}
