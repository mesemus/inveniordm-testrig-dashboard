import ReplayRounded from '@mui/icons-material/ReplayRounded'
import {
  Alert,
  Button,
  Dialog,
  DialogActions,
  DialogContent,
  DialogContentText,
  DialogTitle,
  Link as MuiLink,
  Snackbar,
} from '@mui/material'
import { useState } from 'react'
import { Link as RouterLink, useNavigate } from 'react-router'

import { ApiError } from '../api/client'
import { useMe, useRerun } from '../api/hooks'

/** Trigger a manual run; it starts within a minute or two. Maintainers only. */
export function RerunButton({ branch }: { branch: string }) {
  const navigate = useNavigate()
  const me = useMe()
  const rerun = useRerun(branch)
  const [confirming, setConfirming] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [activeUid, setActiveUid] = useState<string | null>(null)

  if (!me.data?.is_maintainer) {
    return null
  }

  const start = async () => {
    setConfirming(false)
    setError(null)
    setActiveUid(null)
    try {
      const result = await rerun.mutateAsync()
      navigate(`/runs/${encodeURIComponent(result.uid)}`)
    } catch (exc) {
      if (exc instanceof ApiError && exc.status === 409) {
        const detail = exc.detail as { uid?: unknown } | undefined
        setActiveUid(typeof detail?.uid === 'string' ? detail.uid : null)
        setError(exc.message)
      } else {
        setError(exc instanceof Error ? exc.message : 'The request failed')
      }
    }
  }

  return (
    <>
      <Button
        variant="contained"
        size="small"
        startIcon={<ReplayRounded />}
        onClick={() => setConfirming(true)}
      >
        Re-run now
      </Button>
      <Dialog open={confirming} onClose={() => setConfirming(false)}>
        <DialogTitle>Start a new run?</DialogTitle>
        <DialogContent>
          <DialogContentText>
            Start a new run of <strong>{branch}</strong>? It starts in a minute or two.
          </DialogContentText>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setConfirming(false)}>Cancel</Button>
          <Button onClick={start} variant="contained">
            Start
          </Button>
        </DialogActions>
      </Dialog>
      <Snackbar
        open={error !== null}
        autoHideDuration={10000}
        onClose={() => setError(null)}
        anchorOrigin={{ vertical: 'bottom', horizontal: 'center' }}
      >
        <Alert severity="error" onClose={() => setError(null)}>
          {error}
          {activeUid && (
            <>
              {' '}
              <MuiLink component={RouterLink} to={`/runs/${encodeURIComponent(activeUid)}`}>
                Open run
              </MuiLink>
            </>
          )}
        </Alert>
      </Snackbar>
    </>
  )
}
