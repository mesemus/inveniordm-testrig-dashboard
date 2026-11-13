import { describe, expect, it } from 'vitest'

import type { HistoryItem } from './streak'
import { lastFailing, streakText } from './streak'

function item(status: HistoryItem['status'], day = 1): HistoryItem {
  return {
    uid: `00000000-0000-0000-0000-00000000010${day}`,
    created_at: `2026-11-0${day}T10:00:00Z`,
    status,
  }
}

describe('streakText', () => {
  it('says there are no completed runs', () => {
    expect(streakText([])).toBe('no completed runs for this target')
  })

  it('handles a test that was absent from the latest run', () => {
    expect(streakText([item('passed', 1), item(null, 2)])).toBe('not run in the latest run')
  })

  it('handles a test skipped in the latest run', () => {
    expect(streakText([item('failed', 1), item('skipped', 2)])).toBe('skipped in the latest run')
  })

  it('reports a single passing run', () => {
    expect(streakText([item('passed', 3)])).toBe('passing since 11-03')
  })

  it('counts the consecutive failing runs', () => {
    expect(streakText([item('failed', 1), item('error', 2)])).toBe('failing in the last 2 runs')
  })
})

describe('lastFailing', () => {
  it('returns the newest failing entry', () => {
    const failing = item('failed', 2)
    expect(lastFailing([item('passed', 1), failing, item('skipped', 3)])).toBe(failing)
  })

  it('returns undefined when nothing failed', () => {
    expect(lastFailing([item('passed', 1), item(null, 2)])).toBeUndefined()
  })
})
