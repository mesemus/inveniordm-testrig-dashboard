import ArrowDropDown from '@mui/icons-material/ArrowDropDown'
import { Button, ListItemText, Menu, MenuItem } from '@mui/material'
import { useId, useState } from 'react'
import { Link as RouterLink } from 'react-router'

import type { RunDetail } from '../api/hooks'
import { useRuns } from '../api/hooks'
import { dateTimeLabel } from './format'
import { RelativeTime } from './RelativeTime'

/** How many earlier runs the menu offers. */
const CHOICES = 10

function compareHref(base: string, head: string): string {
  return `/compare?${new URLSearchParams({ base, head }).toString()}`
}

/**
 * "Compare with…": this target's earlier completed runs, newest first, labelled "2 days ago".
 * Hidden when the run has no earlier completed run.
 */
export function CompareMenu({ run }: { run: RunDetail }) {
  const [anchor, setAnchor] = useState<HTMLElement | null>(null)
  const menuId = useId()
  // ponytail: the newest 50 completed runs of the target. A run older than those offers only
  // its previous run (below); add a `before=` filter to the runs API if that ever matters.
  const runs = useRuns(run.branch, { page: 0, pageSize: 50, status: ['completed'] })

  if (!run.previous_completed_uid) {
    return null
  }
  const createdAt = Date.parse(run.created_at)
  const earlier = (runs.data?.items ?? [])
    .filter((item) => item.uid !== run.uid && Date.parse(item.created_at) < createdAt)
    .slice(0, CHOICES)
  const close = () => setAnchor(null)

  return (
    <>
      <Button
        variant="outlined"
        size="small"
        endIcon={<ArrowDropDown />}
        aria-haspopup="menu"
        aria-controls={anchor ? menuId : undefined}
        aria-expanded={anchor ? 'true' : undefined}
        onClick={(event) => setAnchor(event.currentTarget)}
      >
        Compare with…
      </Button>
      <Menu id={menuId} anchorEl={anchor} open={anchor !== null} onClose={close}>
        {earlier.length === 0 ? (
          <MenuItem
            component={RouterLink}
            to={compareHref(run.previous_completed_uid, run.uid)}
            onClick={close}
          >
            <ListItemText primary="Previous completed run" />
          </MenuItem>
        ) : (
          earlier.map((item) => (
            <MenuItem
              key={item.uid}
              component={RouterLink}
              to={compareHref(item.uid, run.uid)}
              onClick={close}
            >
              <ListItemText
                primary={<RelativeTime date={item.created_at} />}
                secondary={`${
                  item.tests_failed ? `${item.tests_failed} failing` : 'passed'
                } · ${dateTimeLabel(item.created_at)}`}
              />
            </MenuItem>
          ))
        )}
      </Menu>
    </>
  )
}
