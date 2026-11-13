import { Stack, Typography } from '@mui/material'
import type { ReactNode } from 'react'

/** A row of facts (a `<dl>`, so screen readers pair each label with its value). */
export function Facts({ children }: { children: ReactNode }) {
  return (
    <Stack
      component="dl"
      direction="row"
      spacing={4}
      useFlexGap
      sx={{ mt: 0, mb: 3, flexWrap: 'wrap', rowGap: 1.5 }}
    >
      {children}
    </Stack>
  )
}

/** One quiet label/value pair: facts about a page's subject, not actions. */
export function Fact({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div>
      <Typography component="dt" variant="caption" color="text.secondary">
        {label}
      </Typography>
      <Typography component="dd" variant="body2" sx={{ m: 0 }}>
        {children}
      </Typography>
    </div>
  )
}
