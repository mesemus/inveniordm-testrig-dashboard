// Copied from mui/material-ui@476b86779d62c937e36253db29c9d7475861889f
// docs/data/material/getting-started/templates/dashboard/theme/customizations/charts.ts (MIT).
// Do not edit; put changes in ../overrides.ts.
// edited: the themePrimitives import path adjusted from `../../../shared-theme/themePrimitives`
// to `./themePrimitives`.
import type { Theme } from '@mui/material/styles';
import { axisClasses, legendClasses, chartsGridClasses } from '@mui/x-charts';
import type { ChartsComponents } from '@mui/x-charts/themeAugmentation';
import { gray } from './themePrimitives';

/* eslint-disable import/prefer-default-export */
export const chartsCustomizations: ChartsComponents<Theme> = {
  MuiChartsAxis: {
    styleOverrides: {
      root: ({ theme }) => ({
        [`& .${axisClasses.line}`]: {
          stroke: gray[300],
        },
        [`& .${axisClasses.tick}`]: { stroke: gray[300] },
        [`& .${axisClasses.tickLabel}`]: {
          fill: gray[500],
          fontWeight: 500,
        },
        ...theme.applyStyles('dark', {
          [`& .${axisClasses.line}`]: {
            stroke: gray[700],
          },
          [`& .${axisClasses.tick}`]: { stroke: gray[700] },
          [`& .${axisClasses.tickLabel}`]: {
            fill: gray[300],
            fontWeight: 500,
          },
        }),
      }),
    },
  },
  MuiChartsTooltip: {
    styleOverrides: {
      mark: ({ theme }) => ({
        ry: 6,
        boxShadow: 'none',
        border: `1px solid ${(theme.vars || theme).palette.divider}`,
      }),
      table: ({ theme }) => ({
        border: `1px solid ${(theme.vars || theme).palette.divider}`,
        borderRadius: theme.shape.borderRadius,
        background: 'hsl(0, 0%, 100%)',
        ...theme.applyStyles('dark', {
          background: gray[900],
        }),
      }),
    },
  },
  MuiChartsLegend: {
    styleOverrides: {
      root: {
        [`& .${legendClasses.mark}`]: {
          ry: 6,
        },
      },
    },
  },
  MuiChartsGrid: {
    styleOverrides: {
      root: ({ theme }) => ({
        [`& .${chartsGridClasses.line}`]: {
          stroke: gray[200],
          strokeDasharray: '4 2',
          strokeWidth: 0.8,
        },
        ...theme.applyStyles('dark', {
          [`& .${chartsGridClasses.line}`]: {
            stroke: gray[700],
            strokeDasharray: '4 2',
            strokeWidth: 0.8,
          },
        }),
      }),
    },
  },
};
