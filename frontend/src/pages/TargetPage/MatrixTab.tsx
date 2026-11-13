import { Box, Link as MuiLink, Typography } from '@mui/material'
import { Fragment } from 'react'
import { Link as RouterLink } from 'react-router'

import { isNotFound } from '../../api/client'
import { useMatrix } from '../../api/hooks'
import { dateTimeLabel } from '../../components/format'
import { PageError, PageLoading } from '../../components/PageState'
import { Status } from '../../components/Status'
import { NotFoundPage } from '../NotFoundPage/NotFoundPage'
import { orderPackages } from './matrix'

const CELL_SIZE = 76
const PACKAGE_COLUMN = 'minmax(200px, max-content)'

function testsHref(uid: string, packageName: string): string {
  return `/runs/${encodeURIComponent(uid)}/tests?${new URLSearchParams({ package: packageName })}`
}

export function MatrixTab({ branch }: { branch: string }) {
  const { data, isLoading, error } = useMatrix(branch)

  if (isLoading) {
    return <PageLoading />
  }
  if (isNotFound(error)) {
    return <NotFoundPage />
  }
  if (error) {
    return <PageError error={error} />
  }
  if (!data || data.runs.length === 0) {
    return <Typography>No completed runs yet.</Typography>
  }

  const packages = orderPackages(data)
  const columns = `${PACKAGE_COLUMN} repeat(${data.runs.length}, ${CELL_SIZE}px)`

  return (
    <Box sx={{ overflow: 'auto', maxHeight: '70vh' }}>
      <Box
        sx={{
          display: 'grid',
          gridTemplateColumns: columns,
          gridAutoRows: 34,
          alignItems: 'center',
          minWidth: 'max-content',
        }}
      >
        <Box sx={{ position: 'sticky', left: 0, zIndex: 2, bgcolor: 'background.paper', px: 1 }}>
          <Typography variant="caption" color="text.secondary">
            Package
          </Typography>
        </Box>
        {data.runs.map((run) => (
          <Box key={run.uid} sx={{ textAlign: 'center' }}>
            <MuiLink
              component={RouterLink}
              to={`/runs/${encodeURIComponent(run.uid)}`}
              variant="caption"
              title={dateTimeLabel(run.created_at)}
            >
              {dateTimeLabel(run.created_at).slice(5)}
            </MuiLink>
          </Box>
        ))}

        {packages.map((pkg) => (
          <Fragment key={pkg.package}>
            <Box
              sx={{
                position: 'sticky',
                left: 0,
                zIndex: 1,
                bgcolor: 'background.paper',
                px: 1,
                minWidth: 0,
              }}
            >
              <Typography variant="body2" noWrap title={pkg.package}>
                {pkg.package}
              </Typography>
            </Box>
            {data.runs.map((run, index) => {
              const status = pkg.cells[index] ?? null
              return (
                <Box key={run.uid} sx={{ display: 'flex', justifyContent: 'center' }}>
                  {status === null ? null : (
                    <MuiLink
                      component={RouterLink}
                      to={testsHref(run.uid, pkg.package)}
                      title={`${pkg.package}, ${dateTimeLabel(run.created_at)}: ${status}`}
                      aria-label={`${pkg.package}, ${dateTimeLabel(run.created_at)}: ${status}`}
                      sx={{ display: 'inline-flex' }}
                    >
                      <Status status={status} iconOnly size="small" noTooltip />
                    </MuiLink>
                  )}
                </Box>
              )
            })}
          </Fragment>
        ))}
      </Box>
    </Box>
  )
}
