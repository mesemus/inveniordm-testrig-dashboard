import { Box, Typography, useColorScheme } from '@mui/material'
import { SparkLineChart } from '@mui/x-charts/SparkLineChart'

import { useTrend } from '../api/hooks'
import { STATUS_INK } from '../theme/overrides'
import { historyBars } from './historyBars'

const MAX_BARS = 20
const BAR_SLOT_PX = 8

/** The last 20 completed runs as bars: red = failing count (log scale), short green = passed. */
export function HistorySparkline({ branch }: { branch: string }) {
  const { colorScheme } = useColorScheme()
  const { data, isLoading, isError } = useTrend(branch, MAX_BARS)

  if (isLoading || isError || !data) {
    return <Dash />
  }
  const bars = historyBars(data)
  if (bars.values.length === 0) {
    return <Dash />
  }
  // Same colours as the chips and the matrix icons (the theme's error.main is too dark on dark).
  const ink = (color: 'error' | 'success') => STATUS_INK[color][colorScheme === 'dark' ? 1 : 0]
  // Bars are keyed by position, not by label: two runs in the same minute have the same label,
  // and a band axis merges equal categories into one wide bar.
  const keys = bars.values.map((_, index) => index)
  // Every bar gets the same slot, and the chart is right-aligned in a fixed-width box: a target
  // with two runs shows two thin bars at the right edge (newest = "now"), not two fat ones.
  return (
    <Box sx={{ width: MAX_BARS * BAR_SLOT_PX, display: 'flex', justifyContent: 'flex-end' }}>
      <SparkLineChart
        plotType="bar"
        data={bars.values}
        height={32}
        width={bars.values.length * BAR_SLOT_PX}
        margin={{ top: 4, bottom: 4, left: 0, right: 0 }}
        showTooltip
        showHighlight
        yAxis={{ min: 0, max: 1 }}
        xAxis={{
          scaleType: 'band',
          data: keys,
          valueFormatter: (index: number) => bars.labels[index] ?? '',
          colorMap: { type: 'ordinal', values: keys, colors: bars.colors.map(ink) },
        }}
        // The label already says "7 failing"/"passed"; a raw bar height would mislead.
        valueFormatter={() => ''}
      />
    </Box>
  )
}

function Dash() {
  return (
    <Typography variant="caption" color="text.secondary">
      -
    </Typography>
  )
}
