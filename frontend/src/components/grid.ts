import type { SxProps, Theme } from '@mui/material'
import type { GridCellParams, GridRowParams, GridValidRowModel } from '@mui/x-data-grid'
import type { KeyboardEvent } from 'react'

/**
 * Shared styles for our clickable DataGrids.
 *
 * Cells are block containers that centre *inline* content with `line-height`; a block element
 * returned by `renderCell` (e.g. MUI Typography's <p>) then sits at the top. Centring the cell
 * itself with flexbox works for every kind of content.
 */
export const CLICKABLE_ROWS_SX: SxProps<Theme> = {
  '& .MuiDataGrid-row': { cursor: 'pointer' },
  '& .MuiDataGrid-cell': {
    display: 'flex',
    alignItems: 'center',
  },
  // Direct text spans (commit, error, test name) shrink and ellipsize instead of overflowing.
  '& .MuiDataGrid-cell > span': {
    minWidth: 0,
    maxWidth: '100%',
    overflow: 'hidden',
    textOverflow: 'ellipsis',
    whiteSpace: 'nowrap',
  },
}

/** Mouse click and Enter on a focused cell do the same thing (the row's single action). */
export function rowActivation<R extends GridValidRowModel>(activate: (row: R) => void) {
  return {
    onRowClick: (params: GridRowParams<R>) => activate(params.row),
    onCellKeyDown: (params: GridCellParams<R>, event: KeyboardEvent) => {
      if (event.key !== 'Enter') {
        return
      }
      // Links and buttons inside a cell handle Enter themselves.
      if ((event.target as HTMLElement | null)?.closest('a, button')) {
        return
      }
      activate(params.row)
    },
  }
}
