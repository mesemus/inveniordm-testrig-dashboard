import { Suspense, lazy } from 'react'
import type { ReactNode } from 'react'
import { Route, Routes } from 'react-router'

import { Layout } from './components/Layout'
import { PageLoading } from './components/PageState'
import { NotFoundPage } from './pages/NotFoundPage/NotFoundPage'
import { RunDetailPage } from './pages/RunDetailPage/RunDetailPage'
import { RunTestsPage } from './pages/RunTestsPage/RunTestsPage'
import { TargetPage } from './pages/TargetPage/TargetPage'
import { TargetsPage } from './pages/TargetsPage/TargetsPage'

// The chart-heavy pages load on demand (the bundle is already large).
const TestCaseHistoryPage = lazy(() =>
  import('./pages/TestCaseHistoryPage/TestCaseHistoryPage').then((module) => ({
    default: module.TestCaseHistoryPage,
  })),
)
const ComparePage = lazy(() =>
  import('./pages/ComparePage/ComparePage').then((module) => ({ default: module.ComparePage })),
)

function lazyPage(page: ReactNode) {
  return <Suspense fallback={<PageLoading />}>{page}</Suspense>
}

export function App() {
  return (
    <Routes>
      <Route element={<Layout />}>
        <Route index element={<TargetsPage />} />
        <Route path="targets/:branch" element={<TargetPage />} />
        <Route path="runs/:uid" element={<RunDetailPage />} />
        <Route path="runs/:uid/tests" element={<RunTestsPage />} />
        <Route path="test-cases/:id" element={lazyPage(<TestCaseHistoryPage />)} />
        <Route path="compare" element={lazyPage(<ComparePage />)} />
        <Route path="*" element={<NotFoundPage />} />
      </Route>
    </Routes>
  )
}
