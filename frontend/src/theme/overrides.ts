// Our own additions on top of the copied template theme. The files under ./template stay
// unmodified, so they can be diffed against a newer mui/material-ui commit.
import { chipClasses } from '@mui/material/Chip'
import type { Components, CSSObject, Theme } from '@mui/material/styles'

import { dataDisplayCustomizations } from './template/customizations/dataDisplay'
import { brand, gray, green, orange, red } from './template/themePrimitives'

/**
 * The colour of a status's label/icon as [light mode, dark mode]: the chips and the matrix's
 * icon-only statuses use the same table, so a status looks the same everywhere.
 * The template's own chips use the success/error/default rows; warning/info are ours.
 */
export const STATUS_INK = {
  default: [gray[500], gray[300]],
  success: [green[500], green[300]],
  error: [red[500], red[300]],
  warning: [orange[600], orange[300]],
  info: [brand[500], brand[300]],
} as const

type ChipRootStyle = (params: { theme: Theme }) => CSSObject & { variants?: unknown[] }

// The template styles the chips in a `styleOverrides.root` *function*, and MUI replaces (not
// merges) such a function. So call the template's and append our two colours instead of
// restating its default/success/error rules.
const templateChipRoot = dataDisplayCustomizations.MuiChip?.styleOverrides
  ?.root as unknown as ChipRootStyle

// The template has no `warning`/`info` chip. These follow its `success`/`error` pattern; the
// label shades are one step darker than the template's `500` in light mode because that only
// reaches 3.47:1 on orange[50] (every other pair is >= 4.5:1 as measured).
export const overrides: Components<Theme> = {
  // Replaces the template's MuiLink: it draws its own underline as a `::before` line on every
  // link and ignores the `underline` prop, so even `underline="none"` (the AppBar title) and
  // the matrix's date headers got a line. Plain MUI underlines, on hover by default.
  MuiLink: {
    defaultProps: { underline: 'hover' },
    styleOverrides: {
      root: {
        fontWeight: 500,
        '&:focus-visible': { outlineOffset: '2px', borderRadius: '2px' },
      },
    },
  },
  MuiChip: {
    // Replaced together with the root function below; keep the template's default.
    defaultProps: { size: 'small' },
    styleOverrides: {
      root: (params) => {
        const { theme } = params
        const base = templateChipRoot(params)
        return {
          ...base,
          variants: [
            ...(base.variants ?? []),
            {
              props: { color: 'warning' },
              style: {
                borderColor: orange[200],
                backgroundColor: orange[50],
                [`& .${chipClasses.label}`]: { color: STATUS_INK.warning[0] },
                [`& .${chipClasses.icon}`]: { color: STATUS_INK.warning[0] },
                ...theme.applyStyles('dark', {
                  borderColor: orange[800],
                  backgroundColor: orange[900],
                  [`& .${chipClasses.label}`]: { color: STATUS_INK.warning[1] },
                  [`& .${chipClasses.icon}`]: { color: STATUS_INK.warning[1] },
                }),
              },
            },
            {
              props: { color: 'info' },
              style: {
                borderColor: brand[200],
                backgroundColor: brand[50],
                [`& .${chipClasses.label}`]: { color: STATUS_INK.info[0] },
                [`& .${chipClasses.icon}`]: { color: STATUS_INK.info[0] },
                ...theme.applyStyles('dark', {
                  borderColor: brand[800],
                  backgroundColor: brand[900],
                  [`& .${chipClasses.label}`]: { color: STATUS_INK.info[1] },
                  [`& .${chipClasses.icon}`]: { color: STATUS_INK.info[1] },
                }),
              },
            },
          ],
        } as CSSObject
      },
    },
  },
}
