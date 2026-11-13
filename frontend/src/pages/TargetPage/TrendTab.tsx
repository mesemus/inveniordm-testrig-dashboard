import { Typography } from '@mui/material'
import { BarChart } from '@mui/x-charts/BarChart'
import { LineChart } from '@mui/x-charts/LineChart'

import { isNotFound } from '../../api/client'
import { useTrend } from '../../api/hooks'
import { dateTimeLabel } from '../../components/format'
import { PageError, PageLoading } from '../../components/PageState'
import { NotFoundPage } from '../NotFoundPage/NotFoundPage'

export function TrendTab({ branch }: { branch: string }) {
  const { data, isLoading, error } = useTrend(branch)

  if (isLoading) {
    return <PageLoading />
  }
  if (isNotFound(error)) {
    return <NotFoundPage />
  }
  if (error) {
    return <PageError error={error} />
  }
  if (!data || data.length === 0) {
    return <Typography>No completed runs yet.</Typography>
  }

  const labels = data.map((point) => dateTimeLabel(point.created_at).slice(5))
  // null (no counts) is a gap, never 0: infra_error runs must not look like "all fixed".
  const failingTests = data.map((point) => point.tests_failed ?? null)
  const failingPackages = data.map((point) => point.packages_failed ?? null)
  const minutes = data.map((point) =>
    point.duration_s === null ? null : Math.round((point.duration_s / 60) * 10) / 10,
  )

  return (
    <>
      <Typography variant="h6" gutterBottom>
        Failing tests and packages
      </Typography>
      <LineChart
        height={300}
        xAxis={[{ scaleType: 'point', data: labels }]}
        series={[
          { data: failingTests, label: 'Failing tests' },
          { data: failingPackages, label: 'Failing packages' },
        ]}
      />
      <Typography variant="h6" gutterBottom sx={{ mt: 3 }}>
        Run duration (minutes)
      </Typography>
      <BarChart
        height={240}
        xAxis={[{ scaleType: 'band', data: labels }]}
        series={[{ data: minutes, label: 'Minutes' }]}
      />
    </>
  )
}
