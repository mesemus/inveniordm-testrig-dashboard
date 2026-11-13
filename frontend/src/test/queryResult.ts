import type { UseQueryResult } from '@tanstack/react-query'

/** A successful query result for a mocked hook (`vi.mocked(useTargets).mockReturnValue(...)`). */
export function queryResult<T>(
  data: T,
  overrides: Partial<UseQueryResult<T, Error>> = {},
): UseQueryResult<T, Error> {
  return {
    data,
    error: null,
    isLoading: false,
    isPending: false,
    isError: false,
    isSuccess: true,
    isFetching: false,
    isFetched: true,
    status: 'success',
    ...overrides,
  } as unknown as UseQueryResult<T, Error>
}

/** A failed query result for a mocked hook. */
export function queryError(error: Error): UseQueryResult<never, Error> {
  return {
    data: undefined,
    error,
    isLoading: false,
    isPending: false,
    isError: true,
    isSuccess: false,
    isFetching: false,
    isFetched: true,
    status: 'error',
  } as unknown as UseQueryResult<never, Error>
}
