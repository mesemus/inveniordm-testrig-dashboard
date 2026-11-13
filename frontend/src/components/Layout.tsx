import Brightness4 from '@mui/icons-material/Brightness4'
import Brightness7 from '@mui/icons-material/Brightness7'
import {
  AppBar,
  Box,
  Button,
  Container,
  IconButton,
  Link as MuiLink,
  Menu,
  MenuItem,
  Toolbar,
  Tooltip,
  Typography,
  useColorScheme,
} from '@mui/material'
import { useState } from 'react'
import { Link as RouterLink, Outlet, useLocation } from 'react-router'

import { loginUrl } from '../api/client'
import { useHealth, useLogout, useMe } from '../api/hooks'
import { ErrorBoundary } from './ErrorBoundary'

const CONTACT_EMAIL = 'cesnet-invenio@eosc.cz'

/** The AppBar + content container. The theme follows the system mode; the button overrides it. */
export function Layout() {
  const { mode, systemMode, setMode } = useColorScheme()
  const resolved = mode === 'system' ? systemMode : mode
  const location = useLocation()
  const me = useMe()
  const health = useHealth()
  const logout = useLogout()
  const [accountAnchor, setAccountAnchor] = useState<HTMLElement | null>(null)

  return (
    // A full-height column: the content grows, so on a short page the footer sits at the bottom.
    <Box sx={{ display: 'flex', flexDirection: 'column', minHeight: '100vh' }}>
      <AppBar position="static" color="inherit" elevation={0} sx={{ borderBottom: 1, borderColor: 'divider' }}>
        <Toolbar>
          <MuiLink
            component={RouterLink}
            to="/"
            color="text.primary"
            underline="none"
            sx={{ flexGrow: 1 }}
          >
            <Typography variant="h6" component="span">
              Invenio Testrig Dashboard
            </Typography>
          </MuiLink>
          {me.data?.login_enabled &&
            (me.data.login ? (
              <>
                <Button
                  color="inherit"
                  onClick={(event) => setAccountAnchor(event.currentTarget)}
                >
                  {me.data.login}
                </Button>
                <Menu
                  anchorEl={accountAnchor}
                  open={accountAnchor !== null}
                  onClose={() => setAccountAnchor(null)}
                >
                  <MenuItem
                    onClick={() => {
                      setAccountAnchor(null)
                      logout.mutate()
                    }}
                  >
                    Log out
                  </MenuItem>
                </Menu>
              </>
            ) : (
              <Button color="inherit" href={loginUrl()}>
                Log in with GitHub
              </Button>
            ))}
          <Tooltip title={resolved === 'dark' ? 'Switch to light mode' : 'Switch to dark mode'}>
            <IconButton
              color="inherit"
              aria-label="Toggle colour mode"
              onClick={() => setMode(resolved === 'dark' ? 'light' : 'dark')}
            >
              {resolved === 'dark' ? <Brightness7 /> : <Brightness4 />}
            </IconButton>
          </Tooltip>
        </Toolbar>
      </AppBar>
      <Container maxWidth="xl" sx={{ py: 3, flexGrow: 1 }}>
        <ErrorBoundary key={location.pathname}>
          <Outlet />
        </ErrorBoundary>
      </Container>
      <Container component="footer" maxWidth="xl" sx={{ pb: 2 }}>
        <Typography variant="caption" color="text.secondary">
          Invenio Testrig Dashboard{health.data ? ` v${health.data.version}` : ''} · Contact:{' '}
          <MuiLink href={`mailto:${CONTACT_EMAIL}`} color="inherit">
            {CONTACT_EMAIL}
          </MuiLink>
        </Typography>
      </Container>
    </Box>
  )
}
