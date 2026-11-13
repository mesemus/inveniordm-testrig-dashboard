import { render } from '@testing-library/react'
import type { ReactElement } from 'react'
import { MemoryRouter, Route, Routes } from 'react-router'

/** Render one page at the given route, with an optional extra route to observe navigation. */
export function renderRoute(
  element: ReactElement,
  { route = '/', path = '*', destination }: { route?: string; path?: string; destination?: ReactElement } = {},
) {
  return render(
    <MemoryRouter initialEntries={[route]}>
      <Routes>
        <Route path={path} element={element} />
        {destination && <Route path="*" element={destination} />}
      </Routes>
    </MemoryRouter>,
  )
}
