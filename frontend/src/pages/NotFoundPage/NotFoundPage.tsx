import { Box, Link as MuiLink, Typography } from '@mui/material'
import { Link as RouterLink } from 'react-router'

export function NotFoundPage() {
  return (
    <Box sx={{ textAlign: 'center', py: 6 }}>
      <Typography variant="h4" gutterBottom>
        Page not found
      </Typography>
      <Typography sx={{ mb: 2 }}>The page or record you asked for does not exist.</Typography>
      <MuiLink component={RouterLink} to="/">
        Back to the targets overview
      </MuiLink>
    </Box>
  )
}
