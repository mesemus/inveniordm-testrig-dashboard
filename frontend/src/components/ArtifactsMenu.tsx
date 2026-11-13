import MoreVert from '@mui/icons-material/MoreVert'
import { IconButton, Menu, MenuItem } from '@mui/material'
import { useState } from 'react'

import { safeApiUrl } from './format'

const LABELS: Record<string, string> = {
  log: 'Log',
  simplified_log: 'Simplified log',
  junit: 'JUnit XML',
  freeze: 'Freeze',
  warnings: 'Warnings',
  top_commits: 'Top commits',
  playwright_report: 'Playwright report',
  server_log: 'Server log',
}

const ORDER = Object.keys(LABELS)

export interface ArtifactsMenuProps {
  artifacts: Record<string, string>
}

/** Only the keys the API returned are shown; any of them may answer 404 (retention). */
export function ArtifactsMenu({ artifacts }: ArtifactsMenuProps) {
  const [anchor, setAnchor] = useState<HTMLElement | null>(null)
  // Only links the API built are rendered; anything else in the data is ignored.
  const entries = Object.entries(artifacts)
    .map(([key, url]) => ({ key, url: safeApiUrl(url) }))
    .filter((entry): entry is { key: string; url: string } => entry.url !== null)
    .sort((a, b) => {
      const left = ORDER.indexOf(a.key)
      const right = ORDER.indexOf(b.key)
      return (left === -1 ? ORDER.length : left) - (right === -1 ? ORDER.length : right)
    })

  if (entries.length === 0) {
    return <>-</>
  }

  return (
    <>
      <IconButton
        size="small"
        aria-label="Artifacts"
        onClick={(event) => {
          event.stopPropagation()
          setAnchor(event.currentTarget)
        }}
      >
        <MoreVert />
      </IconButton>
      <Menu
        anchorEl={anchor}
        open={anchor !== null}
        onClose={() => setAnchor(null)}
        onClick={(event) => event.stopPropagation()}
      >
        {entries.map((entry) => (
          <MenuItem
            key={entry.key}
            component="a"
            href={entry.url}
            target="_blank"
            rel="noopener noreferrer"
            onClick={() => setAnchor(null)}
          >
            {LABELS[entry.key] ?? entry.key}
          </MenuItem>
        ))}
      </Menu>
    </>
  )
}
