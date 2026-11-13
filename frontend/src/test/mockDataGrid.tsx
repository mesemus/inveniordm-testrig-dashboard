import type { KeyboardEvent, ReactNode } from 'react'

interface MockColumn {
  field: string
  renderCell?: (params: { row: Record<string, unknown> }) => ReactNode
}

interface MockDataGridProps {
  rows?: Record<string, unknown>[]
  columns?: MockColumn[]
  rowCount?: number
  loading?: boolean
  paginationModel?: { page: number; pageSize: number }
  onPaginationModelChange?: (model: { page: number; pageSize: number }) => void
  onRowClick?: (params: { row: Record<string, unknown> }) => void
  onCellKeyDown?: (
    params: { row: Record<string, unknown> },
    event: KeyboardEvent<HTMLDivElement>,
  ) => void
  sx?: unknown
}

/**
 * A tiny stand-in for MUI's DataGrid in tests: it renders the rows and a "next page" button,
 * which is all these page tests need (the real grid needs layout that jsdom doesn't provide).
 */
export function MockDataGrid(props: MockDataGridProps) {
  const rows = props.rows ?? []
  const columns = props.columns ?? []
  const page = props.paginationModel?.page ?? 0
  const pageSize = props.paginationModel?.pageSize ?? 50

  return (
    <div
      data-testid="data-grid"
      data-sx={JSON.stringify(props.sx ?? null)}
      data-loading={String(props.loading ?? false)}
    >
      <div data-testid="row-count">{props.rowCount ?? rows.length}</div>
      {rows.map((row, index) => (
        <div
          key={index}
          data-testid={`grid-row-${index}`}
          tabIndex={0}
          onClick={() => props.onRowClick?.({ row })}
          onKeyDown={(event) => props.onCellKeyDown?.({ row }, event)}
        >
          {columns.map((column) => (
            <span key={column.field} data-field={column.field}>
              {column.renderCell ? column.renderCell({ row }) : String(row[column.field] ?? '')}
            </span>
          ))}
        </div>
      ))}
      <button
        type="button"
        onClick={() => props.onPaginationModelChange?.({ page: page + 1, pageSize })}
      >
        next page
      </button>
    </div>
  )
}
