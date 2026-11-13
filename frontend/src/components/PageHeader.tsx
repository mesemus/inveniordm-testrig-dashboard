import NavigateNextRounded from '@mui/icons-material/NavigateNextRounded'
import { Breadcrumbs, Link as MuiLink, Stack, Typography } from '@mui/material'
import type { ReactNode } from 'react'
import { Link as RouterLink } from 'react-router'

export interface Crumb {
  label: string
  to: string
}

export interface PageHeaderProps {
  /** The trail *above* the title; the current page is the title, not a crumb. */
  breadcrumbs?: Crumb[]
  title: string
  /** The full value behind a truncated `title` (e.g. a run uid). */
  titleAttribute?: string
  /** Optional chip next to the title (run pages). */
  status?: ReactNode
  /** Right-aligned actions; the container is not rendered when this is empty. */
  actions?: ReactNode
}

/** One header for every page: breadcrumbs, one `h1`, an optional status, and the page actions. */
export function PageHeader({
  breadcrumbs,
  title,
  titleAttribute,
  status,
  actions,
}: PageHeaderProps) {
  return (
    <Stack
      direction="row"
      sx={{
        justifyContent: 'space-between',
        alignItems: 'flex-end',
        flexWrap: 'wrap',
        rowGap: 1,
        mb: 2,
      }}
    >
      <div>
        {breadcrumbs && breadcrumbs.length > 0 && (
          <Breadcrumbs
            separator={<NavigateNextRounded fontSize="small" />}
            sx={{ mb: 0.5 }}
            aria-label="Breadcrumb"
          >
            {breadcrumbs.map((crumb) => (
              <MuiLink
                key={crumb.to}
                component={RouterLink}
                to={crumb.to}
                variant="body2"
                underline="hover"
                color="text.secondary"
              >
                {crumb.label}
              </MuiLink>
            ))}
          </Breadcrumbs>
        )}
        <Stack direction="row" spacing={1} sx={{ alignItems: 'center', flexWrap: 'wrap' }}>
          <Typography variant="h5" component="h1" title={titleAttribute}>
            {title}
          </Typography>
          {status}
        </Stack>
      </div>
      {actions && (
        <Stack
          direction="row"
          spacing={1}
          sx={{ alignItems: 'center' }}
          data-testid="page-header-actions"
        >
          {actions}
        </Stack>
      )}
    </Stack>
  )
}
