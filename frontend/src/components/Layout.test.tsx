import { ThemeProvider } from '@mui/material'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { useHealth, useLogout, useMe } from '../api/hooks'
import { queryResult } from '../test/queryResult'
import { theme } from '../theme'
import { Layout } from './Layout'

vi.mock('../api/hooks', () => ({ useMe: vi.fn(), useLogout: vi.fn(), useHealth: vi.fn() }))

function renderLayout(initialEntries = ['/']) {
  return render(
    <ThemeProvider theme={theme} defaultMode="system">
      <MemoryRouter initialEntries={initialEntries}>
        <Routes>
          <Route element={<Layout />}>
            <Route index element={<div>home</div>} />
          </Route>
        </Routes>
      </MemoryRouter>
    </ThemeProvider>,
  )
}

describe('Layout', () => {
  beforeEach(() => {
    vi.mocked(useMe).mockReturnValue(
      queryResult({ login: null, is_maintainer: false, login_enabled: false }),
    )
    vi.mocked(useLogout).mockReturnValue({
      mutate: vi.fn(),
    } as unknown as ReturnType<typeof useLogout>)
    vi.mocked(useHealth).mockReturnValue(queryResult({ status: 'ok', version: '1.2.3' }))
  })

  it('shows the backend version in the footer', () => {
    renderLayout()

    expect(screen.getByRole('contentinfo')).toHaveTextContent('Invenio Testrig Dashboard v1.2.3')
  })

  it('shows the contact address in the footer', () => {
    renderLayout()

    const contact = screen.getByRole('link', { name: 'cesnet-invenio@eosc.cz' })
    expect(contact).toHaveAttribute('href', 'mailto:cesnet-invenio@eosc.cz')
  })

  it('leaves the version out while the heartbeat is unavailable', () => {
    vi.mocked(useHealth).mockReturnValue(
      queryResult(undefined) as unknown as ReturnType<typeof useHealth>,
    )
    renderLayout()

    expect(screen.getByRole('contentinfo')).toHaveTextContent(
      /^Invenio Testrig Dashboard · Contact: cesnet-invenio@eosc\.cz$/,
    )
  })
  it('toggles the colour mode manually', async () => {
    const user = userEvent.setup()
    renderLayout()

    // The jsdom matchMedia mock reports a light system preference.
    expect(screen.getByTestId('Brightness4Icon')).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: 'Toggle colour mode' }))

    // A regression guard for the default 'media' selector, where setMode() is a no-op.
    expect(screen.getByTestId('Brightness7Icon')).toBeInTheDocument()
    expect(document.documentElement).toHaveAttribute('data-mui-color-scheme', 'dark')
  })

  it('keeps the AppBar and shows the error when a page throws', () => {
    function Boom(): never {
      throw new Error('boom')
    }

    render(
      <ThemeProvider theme={theme} defaultMode="system">
        <MemoryRouter initialEntries={['/boom']}>
          <Routes>
            <Route element={<Layout />}>
              <Route path="boom" element={<Boom />} />
            </Route>
          </Routes>
        </MemoryRouter>
      </ThemeProvider>,
    )

    expect(screen.getByText('Invenio Testrig Dashboard')).toBeInTheDocument()
    expect(screen.getByText('Something went wrong')).toBeInTheDocument()
    expect(screen.getByText('boom')).toBeInTheDocument()
  })

  it('shows the login link when anonymous and login is enabled', () => {
    vi.mocked(useMe).mockReturnValue(
      queryResult({ login: null, is_maintainer: false, login_enabled: true }),
    )

    renderLayout()

    expect(screen.getByRole('link', { name: 'Log in with GitHub' })).toHaveAttribute(
      'href',
      '/api/auth/login?next=%2F',
    )
  })

  it('shows the username and a logout item when logged in', async () => {
    const user = userEvent.setup()
    const mutate = vi.fn()
    vi.mocked(useMe).mockReturnValue(
      queryResult({ login: 'octocat', is_maintainer: false, login_enabled: true }),
    )
    vi.mocked(useLogout).mockReturnValue({ mutate } as unknown as ReturnType<typeof useLogout>)

    renderLayout()
    await user.click(screen.getByRole('button', { name: 'octocat' }))
    await user.click(screen.getByRole('menuitem', { name: 'Log out' }))

    expect(mutate).toHaveBeenCalled()
  })
})
