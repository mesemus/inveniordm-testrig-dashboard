import { describe, expect, it } from 'vitest'

import type { MatrixOut } from '../../api/hooks'
import { orderPackages, shownFailures } from './matrix'

type Cells = MatrixOut['packages'][number]['cells']

function matrix(packages: Record<string, Cells>): MatrixOut {
  return {
    runs: [],
    packages: Object.entries(packages).map(([name, cells]) => ({ package: name, cells })),
  }
}

function names(data: MatrixOut): string[] {
  return orderPackages(data).map((pkg) => pkg.package)
}

describe('orderPackages', () => {
  it('puts the packages with the most shown failures first', () => {
    const data = matrix({
      'invenio-a': ['failed', 'passed', 'passed'],
      'invenio-b': ['failed', 'error', 'failed'],
      'invenio-c': ['passed', 'failed', 'error'],
      'invenio-d': ['passed', 'passed', 'passed'],
    })

    // 3, 2, 1 and 0 failures.
    expect(names(data)).toEqual(['invenio-b', 'invenio-c', 'invenio-a', 'invenio-d'])
  })

  it('orders packages with the same number of failures by name', () => {
    const data = matrix({
      'invenio-z': ['failed', 'passed'],
      'invenio-m': ['passed', 'passed'],
      'invenio-a': ['passed', 'error'],
      'invenio-b': ['passed', 'passed'],
    })

    expect(names(data)).toEqual(['invenio-a', 'invenio-z', 'invenio-b', 'invenio-m'])
  })

  it('counts only failed and error cells, not skipped, not run or absent', () => {
    const cells: Cells = ['failed', 'error', 'skipped', 'not_run', null, 'passed']
    expect(shownFailures({ package: 'p', cells })).toBe(2)
  })

  it('does not reorder the input', () => {
    const data = matrix({ b: ['passed'], a: ['failed'] })
    orderPackages(data)
    expect(data.packages.map((pkg) => pkg.package)).toEqual(['b', 'a'])
  })
})
