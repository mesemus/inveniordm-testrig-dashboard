import { describe, expect, it } from 'vitest'

import { theme } from './index'

interface ChipVariant {
  props?: { color?: string; size?: string }
}

/** The theme's chip root is a function (the template's, wrapped); call it as MUI does. */
function chipVariants(): ChipVariant[] {
  const root = theme.components?.MuiChip?.styleOverrides?.root as unknown as (params: {
    theme: typeof theme
  }) => { variants?: ChipVariant[] }
  return root({ theme }).variants ?? []
}

describe('theme', () => {
  it('keeps the template chips and adds warning/info', () => {
    const colours = chipVariants().map((variant) => variant.props?.color)

    expect(colours).toEqual(
      expect.arrayContaining(['default', 'success', 'error', 'warning', 'info']),
    )
  })

  it('keeps the template chip default and the bundled Inter family', () => {
    expect(theme.components?.MuiChip?.defaultProps).toMatchObject({ size: 'small' })
    expect(theme.typography.fontFamily).toContain('Inter Variable')
  })

  it('points the elevation-1 shadow at our css var prefix', () => {
    expect(theme.shadows[1]).toBe('var(--itd-palette-baseShadow)')
  })
})
