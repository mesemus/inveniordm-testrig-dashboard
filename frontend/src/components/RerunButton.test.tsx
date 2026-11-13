import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { ApiError } from '../api/client'
import { useMe, useRerun } from '../api/hooks'
import { queryResult } from '../test/queryResult'
import { RerunButton } from './RerunButton'

vi.mock('../api/hooks', () => ({ useMe: vi.fn(), useRerun: vi.fn() }))

const UID = '00000000-0000-0000-0000-000000000001'

function renderButton() {
  return render(
    <MemoryRouter>
      <RerunButton branch="master" />
    </MemoryRouter>,
  )
}

describe('RerunButton', () => {
  beforeEach(() => {
    vi.mocked(useMe).mockReturnValue(
      queryResult({ login: 'octocat', is_maintainer: true, login_enabled: true }),
    )
    vi.mocked(useRerun).mockReturnValue({
      mutateAsync: vi.fn().mockResolvedValue({ uid: UID }),
    } as unknown as ReturnType<typeof useRerun>)
  })

  it('is hidden for anonymous visitors', () => {
    vi.mocked(useMe).mockReturnValue(
      queryResult({ login: null, is_maintainer: false, login_enabled: true }),
    )

    renderButton()

    expect(screen.queryByRole('button', { name: 'Re-run now' })).not.toBeInTheDocument()
  })

  it('is hidden for non-maintainers', () => {
    vi.mocked(useMe).mockReturnValue(
      queryResult({ login: 'someone', is_maintainer: false, login_enabled: true }),
    )

    renderButton()

    expect(screen.queryByRole('button', { name: 'Re-run now' })).not.toBeInTheDocument()
  })

  it('confirms and then posts', async () => {
    const user = userEvent.setup()
    const mutateAsync = vi.fn().mockResolvedValue({ uid: UID })
    vi.mocked(useRerun).mockReturnValue({
      mutateAsync,
    } as unknown as ReturnType<typeof useRerun>)

    renderButton()
    await user.click(screen.getByRole('button', { name: 'Re-run now' }))
    expect(screen.getByText(/It starts in a minute or two/)).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Start' }))

    await waitFor(() => expect(mutateAsync).toHaveBeenCalled())
  })

  it('shows a 409 with a link to the active run', async () => {
    const user = userEvent.setup()
    vi.mocked(useRerun).mockReturnValue({
      mutateAsync: vi
        .fn()
        .mockRejectedValue(
          new ApiError(409, "target 'master' already has an active run", { uid: UID }),
        ),
    } as unknown as ReturnType<typeof useRerun>)

    renderButton()
    await user.click(screen.getByRole('button', { name: 'Re-run now' }))
    await user.click(screen.getByRole('button', { name: 'Start' }))

    expect(await screen.findByText(/already has an active run/)).toBeInTheDocument()
    expect(await screen.findByRole('link', { name: 'Open run' })).toHaveAttribute(
      'href',
      `/runs/${UID}`,
    )
  })
})
