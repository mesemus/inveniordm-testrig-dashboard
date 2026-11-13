import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router'
import { describe, expect, it } from 'vitest'

import { PageHeader } from './PageHeader'

describe('PageHeader', () => {
  it('renders one h1, the breadcrumb links and the actions', () => {
    render(
      <MemoryRouter>
        <PageHeader
          breadcrumbs={[{ label: 'Targets', to: '/' }]}
          title="master"
          status={<span>a status chip</span>}
          actions={<button type="button">Re-run now</button>}
        />
      </MemoryRouter>,
    )

    expect(screen.getAllByRole('heading', { level: 1 })).toHaveLength(1)
    expect(screen.getByRole('heading', { level: 1 })).toHaveTextContent('master')
    expect(screen.getByRole('link', { name: 'Targets' })).toHaveAttribute('href', '/')
    expect(screen.getByText('a status chip')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Re-run now' })).toBeInTheDocument()
  })

  it('shows the full value in the title attribute of a truncated title', () => {
    render(
      <MemoryRouter>
        <PageHeader title="Run 1a2b3c4d" titleAttribute="1a2b3c4d-0000-0000-0000-000000000000" />
      </MemoryRouter>,
    )

    expect(screen.getByRole('heading', { level: 1 })).toHaveAttribute(
      'title',
      '1a2b3c4d-0000-0000-0000-000000000000',
    )
  })

  it('renders no actions container when there are no actions', () => {
    render(
      <MemoryRouter>
        <PageHeader title="Targets" />
      </MemoryRouter>,
    )

    expect(screen.queryByTestId('page-header-actions')).not.toBeInTheDocument()
    expect(screen.queryByRole('navigation')).not.toBeInTheDocument()
  })
})
