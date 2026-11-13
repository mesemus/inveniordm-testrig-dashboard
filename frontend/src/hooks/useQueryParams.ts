import { useCallback } from 'react'
import { useSearchParams } from 'react-router'

export type QueryChanges = Record<string, string | string[] | null>

export interface UpdateOptions {
  replace?: boolean
}

/** The page sizes the DataGrids offer, and the API's limits. */
export const PAGE_SIZES = [25, 50, 100] as const
export const DEFAULT_PAGE_SIZE = 50
export const MAX_PAGE = 10_000

export interface Pagination {
  page: number
  pageSize: number
}

/** Parse and clamp URL pagination, so a hand-edited link cannot make the API answer 422. */
export function readPagination(get: (name: string) => string | null): Pagination {
  const rawPage = Number.parseInt(get('page') ?? '', 10)
  const rawSize = Number.parseInt(get('pageSize') ?? '', 10)

  const page = Number.isFinite(rawPage) ? Math.min(Math.max(rawPage, 0), MAX_PAGE) : 0
  const boundedSize = Number.isFinite(rawSize)
    ? Math.min(Math.max(rawSize, PAGE_SIZES[0]), PAGE_SIZES[PAGE_SIZES.length - 1])
    : DEFAULT_PAGE_SIZE
  const pageSize = PAGE_SIZES.reduce((best, size) =>
    Math.abs(size - boundedSize) < Math.abs(best - boundedSize) ? size : best,
  )

  return { page, pageSize }
}

/**
 * Filter and page state lives in the URL, so links are shareable and the back button works.
 * An empty string or `null` removes the key; a list becomes repeated keys.
 */
export function useQueryParams() {
  const [searchParams, setSearchParams] = useSearchParams()

  const get = useCallback((name: string) => searchParams.get(name), [searchParams])
  const getAll = useCallback((name: string) => searchParams.getAll(name), [searchParams])

  const update = useCallback(
    (changes: QueryChanges, options?: UpdateOptions) => {
      setSearchParams(
        (previous) => {
          const next = new URLSearchParams(previous)
          for (const [key, value] of Object.entries(changes)) {
            next.delete(key)
            if (value === null || value === '') {
              continue
            }
            if (Array.isArray(value)) {
              for (const item of value) {
                next.append(key, item)
              }
            } else {
              next.set(key, value)
            }
          }
          return next
        },
        { replace: options?.replace ?? false },
      )
    },
    [setSearchParams],
  )

  return { searchParams, get, getAll, update }
}
