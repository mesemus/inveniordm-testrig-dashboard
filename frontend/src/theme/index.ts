import type {} from '@mui/x-charts/themeAugmentation'
import type {} from '@mui/x-data-grid/themeAugmentation'
import { createTheme } from '@mui/material/styles'

import { overrides } from './overrides'
import { chartsCustomizations } from './template/charts'
import { dataDisplayCustomizations } from './template/customizations/dataDisplay'
import { feedbackCustomizations } from './template/customizations/feedback'
import { inputsCustomizations } from './template/customizations/inputs'
import { navigationCustomizations } from './template/customizations/navigation'
import { surfacesCustomizations } from './template/customizations/surfaces'
import { dataGridCustomizations } from './template/dataGrid'
import { colorSchemes, shadows, shape, typography } from './template/themePrimitives'

export const theme = createTheme({
  // The default selector is 'media', where useColorScheme().setMode() is ignored. A data
  // attribute lets the AppBar button toggle the mode manually; it is written on <html>.
  cssVariables: { colorSchemeSelector: 'data-mui-color-scheme', cssVarPrefix: 'itd' },
  colorSchemes,
  // The template names 'Inter'; @fontsource-variable/inter registers 'Inter Variable'.
  typography: { ...typography, fontFamily: "'Inter Variable', sans-serif" },
  shadows,
  shape,
  components: {
    ...inputsCustomizations,
    ...dataDisplayCustomizations,
    ...feedbackCustomizations,
    ...navigationCustomizations,
    ...surfacesCustomizations,
    ...dataGridCustomizations,
    ...chartsCustomizations,
    ...overrides,
  },
})
