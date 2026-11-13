import type { MatrixOut, PackageStatus } from '../../api/hooks'

function isFailing(status: PackageStatus | null | undefined): boolean {
  return status === 'failed' || status === 'error'
}

/** Packages failing in the latest run first (problems at the top), then alphabetical. */
export function orderPackages(data: MatrixOut): MatrixOut['packages'] {
  const latest = data.runs.length - 1
  return [...data.packages].sort((a, b) => {
    const aFailing = latest >= 0 && isFailing(a.cells[latest])
    const bFailing = latest >= 0 && isFailing(b.cells[latest])
    return Number(bFailing) - Number(aFailing) || a.package.localeCompare(b.package)
  })
}
