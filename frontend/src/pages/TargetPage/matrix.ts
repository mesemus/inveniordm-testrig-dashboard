import type { MatrixOut, PackageStatus } from '../../api/hooks'

function isFailing(status: PackageStatus | null | undefined): boolean {
  return status === 'failed' || status === 'error'
}

/** How many of the shown runs (the matrix columns) the package failed in. */
export function shownFailures(pkg: MatrixOut['packages'][number]): number {
  return pkg.cells.filter(isFailing).length
}

/** Most shown failures first (problems at the top), then alphabetical. */
export function orderPackages(data: MatrixOut): MatrixOut['packages'] {
  return [...data.packages].sort(
    (a, b) => shownFailures(b) - shownFailures(a) || a.package.localeCompare(b.package),
  )
}
