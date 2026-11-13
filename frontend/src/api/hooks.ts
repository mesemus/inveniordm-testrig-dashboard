import { keepPreviousData, useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { ApiError, client, detailText } from './client'
import type { components } from './schema'

export type TargetOut = components['schemas']['TargetOut']
export type RunSummary = components['schemas']['RunSummary']
export type RunListItem = components['schemas']['RunListItem']
export type RunDetail = components['schemas']['RunDetail']
export type PackageOut = components['schemas']['PackageOut']
export type TestListItem = components['schemas']['TestListItem']
export type TestResultDetail = components['schemas']['TestResultDetail']
export type RunStatus = components['schemas']['RunStatus']
export type PackageStatus = components['schemas']['PackageStatus']
export type TestStatusName = components['schemas']['TestStatusName']
export type MatrixOut = components['schemas']['MatrixOut']
export type TrendPoint = components['schemas']['TrendPoint']
export type TestCaseOut = components['schemas']['TestCaseOut']
export type HistoryOut = components['schemas']['HistoryOut']
export type CompareOut = components['schemas']['CompareOut']
export type CompareTestItem = components['schemas']['CompareTestItem']
export type CompareKind = 'flipped' | 'new' | 'gone'
export type RunListPage = components['schemas']['Page_RunListItem_']
export type MeOut = components['schemas']['MeOut']
export type RerunOut = components['schemas']['RerunOut']
export type HealthOut = components['schemas']['HealthOut']
/** Every status the UI can render: run/package/test enums plus the synthetic "flaky". */
export type StatusValue = RunStatus | PackageStatus | TestStatusName | 'flaky'

/** How often a page polls while a run is active (and the countdown's period). */
export const REFRESH_MS = 60_000

const ACTIVE_STATUSES: readonly StatusValue[] = ['requested', 'queued', 'running']

/** A run that a dispatch or ingest tick can still move forward. */
export function isActiveStatus(status: StatusValue | null | undefined): boolean {
  return status !== null && status !== undefined && ACTIVE_STATUSES.includes(status)
}

/** Whether this branch has an active run. The targets answer is exact: one active run max. */
export function isActive(targets: TargetOut[] | undefined, branch: string): boolean {
  return isActiveStatus(targets?.find((target) => target.branch === branch)?.latest_run?.status)
}

/** Polling options a page passes to a hook; the function forms re-check activity per fetch. */
export interface PollOptions<T> {
  /** Only fetch when true (a page can keep an inactive tab's query idle). */
  enabled?: boolean
  refetchInterval?:
    | number
    | false
    | ((query: { state: { data?: T } }) => number | false | undefined)
  refetchOnWindowFocus?:
    | boolean
    | 'always'
    | ((query: { state: { data?: T } }) => boolean | 'always')
}

type FetchResult<T> = { data?: T; error?: unknown; response: Response }

async function unwrap<T>(result: FetchResult<T>): Promise<T> {
  if (!result.response.ok || result.data === undefined) {
    throw new ApiError(result.response.status, detailText(result.error), result.error)
  }
  return result.data
}

export function useTargets(options: PollOptions<TargetOut[]> = {}) {
  return useQuery({
    queryKey: ['targets'],
    queryFn: async () => unwrap(await client.GET('/api/targets')),
    ...options,
  })
}

export interface RunListParams {
  page: number
  pageSize: number
  status?: RunStatus[]
}

export function useRuns(
  branch: string,
  params: RunListParams,
  options: PollOptions<RunListPage> = {},
) {
  return useQuery({
    queryKey: ['runs', branch, params.page, params.pageSize, params.status],
    queryFn: async () =>
      unwrap(
        await client.GET('/api/targets/{branch}/runs', {
          params: {
            path: { branch },
            query: {
              page: params.page,
              page_size: params.pageSize,
              status: params.status,
            },
          },
        }),
      ),
    placeholderData: keepPreviousData,
    enabled: branch !== '',
    ...options,
  })
}

export function useRun(uid: string, options: PollOptions<RunDetail> = {}) {
  return useQuery({
    queryKey: ['run', uid],
    queryFn: async () =>
      unwrap(await client.GET('/api/runs/{uid}', { params: { path: { uid } } })),
    enabled: uid !== '',
    ...options,
  })
}

export interface RunTestsParams {
  page: number
  pageSize: number
  package?: string
  status?: TestStatusName[]
  q?: string
}

export function useRunTests(uid: string, params: RunTestsParams) {
  return useQuery({
    queryKey: ['run-tests', uid, params],
    queryFn: async () =>
      unwrap(
        await client.GET('/api/runs/{uid}/tests', {
          params: {
            path: { uid },
            query: {
              page: params.page,
              page_size: params.pageSize,
              package: params.package,
              status: params.status,
              q: params.q,
            },
          },
        }),
      ),
    placeholderData: keepPreviousData,
  })
}

export function useRunTest(uid: string, testCaseId: number | null) {
  return useQuery({
    queryKey: ['run-test', uid, testCaseId],
    queryFn: async () => {
      if (testCaseId === null) {
        throw new Error('no test selected')
      }
      return unwrap(
        await client.GET('/api/runs/{uid}/tests/{test_case_id}', {
          params: { path: { uid, test_case_id: testCaseId } },
        }),
      )
    },
    enabled: testCaseId !== null,
  })
}

export function useMatrix(branch: string, limit = 30) {
  return useQuery({
    queryKey: ['matrix', branch, limit],
    queryFn: async () =>
      unwrap(
        await client.GET('/api/targets/{branch}/matrix', {
          params: { path: { branch }, query: { limit } },
        }),
      ),
    enabled: branch !== '',
  })
}

export function useTrend(branch: string, limit = 90) {
  return useQuery({
    queryKey: ['trend', branch, limit],
    queryFn: async () =>
      unwrap(
        await client.GET('/api/targets/{branch}/trend', {
          params: { path: { branch }, query: { limit } },
        }),
      ),
    enabled: branch !== '',
  })
}

export function useTestCase(caseId: number) {
  return useQuery({
    queryKey: ['test-case', caseId],
    queryFn: async () =>
      unwrap(
        await client.GET('/api/test-cases/{case_id}', {
          params: { path: { case_id: caseId } },
        }),
      ),
  })
}

export function useTestCaseHistory(caseId: number, target: string, limit = 50) {
  return useQuery({
    queryKey: ['test-case-history', caseId, target, limit],
    queryFn: async () =>
      unwrap(
        await client.GET('/api/test-cases/{case_id}/history', {
          params: { path: { case_id: caseId }, query: { target, limit } },
        }),
      ),
    enabled: target !== '',
  })
}

export function useCompare(base: string, head: string) {
  return useQuery({
    queryKey: ['compare', base, head],
    queryFn: async () =>
      unwrap(await client.GET('/api/compare', { params: { query: { base, head } } })),
    enabled: base !== '' && head !== '',
  })
}

export function useCompareTests(
  base: string,
  head: string,
  kind: CompareKind,
  page: number,
  pageSize: number,
) {
  return useQuery({
    queryKey: ['compare-tests', base, head, kind, page, pageSize],
    queryFn: async () =>
      unwrap(
        await client.GET('/api/compare/tests', {
          params: {
            query: { base, head, kind, page, page_size: pageSize },
          },
        }),
      ),
    enabled: base !== '' && head !== '',
    placeholderData: keepPreviousData,
  })
}

/** The heartbeat: the running backend's version (pyproject.toml = the image tag), for the footer. */
export function useHealth() {
  return useQuery({
    queryKey: ['health'],
    queryFn: async () => unwrap(await client.GET('/api/health')),
    staleTime: 10 * 60_000,
    retry: false,
  })
}

export function useMe() {
  return useQuery({
    queryKey: ['me'],
    queryFn: async () => unwrap(await client.GET('/api/auth/me')),
    staleTime: 5 * 60_000,
  })
}

export function useLogout() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: async () => {
      const { error, response } = await client.POST('/api/auth/logout')
      if (!response.ok) {
        throw new ApiError(response.status, detailText(error), error)
      }
    },
    onSuccess: () => {
      // Every cached page may hold data only a logged-in user may see; drop it all and let
      // the active queries refetch (now unauthenticated) instead of showing stale data.
      queryClient.clear()
    },
  })
}

export function useRerun(branch: string) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: async () => {
      // A JSON content type is required by the endpoint (its CSRF protection).
      const { data, error, response } = await client.POST('/api/targets/{branch}/runs', {
        params: { path: { branch } },
        headers: { 'Content-Type': 'application/json' },
      })
      if (!response.ok || data === undefined) {
        throw new ApiError(response.status, detailText(error), error)
      }
      return data
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['runs', branch] })
      void queryClient.invalidateQueries({ queryKey: ['targets'] })
    },
  })
}
