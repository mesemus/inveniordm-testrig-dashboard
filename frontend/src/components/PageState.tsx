import { Alert, Box, CircularProgress } from '@mui/material'

import { errorText } from '../api/client'

export function PageLoading() {
  return (
    <Box sx={{ display: 'flex', justifyContent: 'center', py: 6 }}>
      <CircularProgress />
    </Box>
  )
}

export function PageError({ error }: { error: unknown }) {
  return <Alert severity="error">{errorText(error)}</Alert>
}
