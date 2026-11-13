import { expect, test } from '@playwright/test'

/**
 * One pass through the whole stack: targets → a run → its failing tests → the failure message
 * → compare with the previous run. CI seeds `e2e-base` then `e2e-head` (see .github/workflows).
 */
test('browse a failing run and compare it with the previous one', async ({ page }) => {
  const consoleErrors: string[] = []
  const pageErrors: string[] = []
  page.on('console', (message) => {
    if (message.type() === 'error') {
      consoleErrors.push(message.text())
    }
  })
  page.on('pageerror', (error) => {
    pageErrors.push(error.message)
  })

  await page.goto('/')
  await page.getByRole('link', { name: 'master', exact: true }).click()

  // The history tabs render too (charts and the matrix must not crash the page).
  await page.getByRole('tab', { name: 'Matrix' }).click()
  await expect(page.getByText('pkg-e2e').first()).toBeVisible()
  await page.getByRole('tab', { name: 'Trend' }).click()
  await expect(page.getByText('Failing tests and packages')).toBeVisible()
  await page.getByRole('tab', { name: 'Runs' }).click()

  // Target page: the newest run (e2e-head) is the first grid row.
  await page.locator('.MuiDataGrid-row').first().click()
  await page.getByRole('link', { name: 'Failing tests' }).click()

  // Tests page: open the drawer with the failure message.
  await page.locator('.MuiDataGrid-row').first().click()
  await expect(page.getByText('Test detail')).toBeVisible()
  await expect(page.getByText('Message')).toBeVisible()
  await expect(page.getByText('AssertionError: e2e boom').first()).toBeVisible()

  // Back to the run, then compare it with the previous completed run (e2e-base).
  await page.goBack()
  await page.getByRole('button', { name: 'Compare with…' }).click()
  await page.getByRole('menuitem').first().click()
  await page.getByRole('tab', { name: /^Flipped/ }).click()
  await expect(page.locator('.MuiDataGrid-row').first()).toBeVisible()

  expect(consoleErrors).toEqual([])
  expect(pageErrors).toEqual([])
})
