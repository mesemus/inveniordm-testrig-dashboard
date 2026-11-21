import {
  Alert,
  Box,
  Chip,
  CircularProgress,
  Link as MuiLink,
  MenuItem,
  Stack,
  TextField,
  Typography,
} from '@mui/material'
import { Link as RouterLink, useParams } from 'react-router'

import { errorText, isMissingRecord, isNotFound } from '../../api/client'
import { useRunTest, useTestCase, useTestCaseHistory, useTargets } from '../../api/hooks'
import { dateLabel } from '../../components/format'
import { PageError, PageLoading } from '../../components/PageState'
import { PageHeader } from '../../components/PageHeader'
import { RelativeTime } from '../../components/RelativeTime'
import { Status } from '../../components/Status'
import { useQueryParams } from '../../hooks/useQueryParams'
import { NotFoundPage } from '../NotFoundPage/NotFoundPage'
import { lastFailing, streakText } from './streak'

function testsHref(uid: string, packageName: string): string {
  return `/runs/${encodeURIComponent(uid)}/tests?${new URLSearchParams({ package: packageName })}`
}

export function TestCaseHistoryPage() {
  const { id = '' } = useParams()
  if (!/^\d+$/.test(id)) {
    return <NotFoundPage />
  }
  return <TestCaseContent caseId={Number(id)} />
}

function TestCaseContent({ caseId }: { caseId: number }) {
  const { get, update } = useQueryParams()
  const targetsQuery = useTargets()
  const targetParam = get('target') ?? ''
  const defaultTarget =
    targetsQuery.data?.find((target) => target.enabled)?.branch ??
    targetsQuery.data?.[0]?.branch ??
    ''
  const target = targetParam || defaultTarget
  const targetBranches = (targetsQuery.data ?? []).map((item) => item.branch)
  const targetOptions =
    target !== '' && !targetBranches.includes(target)
      ? [...targetBranches, target]
      : targetBranches

  const testCase = useTestCase(caseId)
  const history = useTestCaseHistory(caseId, target, 50)
  const items = history.data?.items ?? []
  const historyLoading = history.isLoading || target === ''
  const unknownTarget = isNotFound(history.error)
  const newestFailing = lastFailing(items)
  const failure = useRunTest(newestFailing?.uid ?? '', newestFailing ? caseId : null)

  if (isMissingRecord(testCase.error)) {
    return <NotFoundPage />
  }
  if (testCase.error && !testCase.data) {
    return <PageError error={testCase.error} />
  }
  if (!testCase.data) {
    return <PageLoading />
  }
  if (history.error && !unknownTarget) {
    return <PageError error={history.error} />
  }

  const detail = testCase.data

  return (
    <>
      <PageHeader
        breadcrumbs={[
          { label: 'Targets', to: '/' },
          ...(target
            ? [{ label: target, to: `/targets/${encodeURIComponent(target)}` }]
            : []),
        ]}
        title={detail.name}
        titleAttribute={detail.classname ? `${detail.classname}::${detail.name}` : undefined}
        status={
          <>
            <Chip size="small" label={detail.package} />
            {history.data?.flaky && <Status status="flaky" />}
          </>
        }
      />
      <Typography variant="body2" sx={{ mt: 1, mb: 2 }}>
        first seen <RelativeTime date={detail.first_seen_at} /> in{' '}
        <MuiLink component={RouterLink} to={`/runs/${encodeURIComponent(detail.first_seen_run_uid)}`}>
          {dateLabel(detail.first_seen_at)}
        </MuiLink>
      </Typography>

      <Stack direction="row" spacing={2} sx={{ alignItems: 'center', mb: 2 }}>
        <TextField
          select
          size="small"
          label="Target"
          value={target}
          onChange={(event) => update({ target: event.target.value })}
          sx={{ minWidth: 200 }}
        >
          {(targetOptions ?? []).map((branch) => (
            <MenuItem key={branch} value={branch}>
              {branch}
            </MenuItem>
          ))}
        </TextField>
        <Typography variant="subtitle1">
          {historyLoading ? (
            <CircularProgress size={18} />
          ) : unknownTarget ? null : (
            streakText(items)
          )}
        </Typography>
      </Stack>

      {unknownTarget && (
        <Alert severity="warning" sx={{ mb: 2 }}>
          Unknown target{targetParam ? `: ${targetParam}` : ''}.
        </Alert>
      )}

      {items.length > 0 && (
        <Box sx={{ display: 'flex', gap: 0.5, flexWrap: 'wrap', mb: 2 }}>
          {items.map((item) => (
            <Box key={item.uid} sx={{ width: 24, height: 24 }}>
              {item.status === null ? (
                <Box
                  data-testid="history-absent"
                  title="absent"
                  sx={{
                    width: 22,
                    height: 22,
                    border: '1px dashed',
                    borderColor: 'divider',
                    borderRadius: '50%',
                  }}
                />
              ) : (
                <MuiLink
                  component={RouterLink}
                  to={testsHref(item.uid, detail.package)}
                  title={`${dateLabel(item.created_at)} — ${item.status}`}
                  sx={{ display: 'inline-flex' }}
                >
                  <Status status={item.status} iconOnly size="small" noTooltip />
                </MuiLink>
              )}
            </Box>
          ))}
        </Box>
      )}

      {newestFailing ? (
        <>
          <Typography variant="h6" sx={{ mt: 2 }}>
            Latest failure
          </Typography>
          <Typography variant="body2" sx={{ mb: 1 }}>
            in{' '}
            <MuiLink
              component={RouterLink}
              to={`/runs/${encodeURIComponent(newestFailing.uid)}`}
            >
              {dateLabel(newestFailing.created_at)}
            </MuiLink>
          </Typography>
          {failure.isLoading ? (
            <CircularProgress size={24} />
          ) : failure.error ? (
            <Alert severity="error">{errorText(failure.error)}</Alert>
          ) : (
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
              }}
            >
              {failure.data?.message || '(no message)'}
            </Box>
          )}
        </>
      ) : (
        <Typography sx={{ mt: 2 }}>No failures in the recent history.</Typography>
      )}
    </>
  )
}
