import type { HopDeployStatus } from './types'

export function normalizeHopDeployStatus(statuses: HopDeployStatus[]): HopDeployStatus[] {
  if (statuses.length === 0) return statuses

  if (statuses.every((s) => s === 'done' || s === 'error')) {
    return [...statuses]
  }

  let activeIdx = statuses.findIndex((s) => s === 'deploying')
  if (activeIdx < 0) {
    activeIdx = statuses.findIndex((s) => s !== 'done' && s !== 'error')
  }
  if (activeIdx < 0) {
    return [...statuses]
  }

  return statuses.map((status, index) => {
    if (status === 'error') return 'error'
    if (index < activeIdx) return 'done'
    if (index === activeIdx) return 'deploying'
    return 'waiting'
  })
}

export function initialHopDeployStatus(count: number): HopDeployStatus[] {
  if (count <= 0) return []
  return normalizeHopDeployStatus([
    'deploying',
    ...Array.from({ length: count - 1 }, () => 'waiting' as HopDeployStatus),
  ])
}

export function advanceHopDeployStatus(
  statuses: HopDeployStatus[],
  completedIndex: number,
): HopDeployStatus[] {
  const next = statuses.map((_, i) => {
    if (i <= completedIndex) return 'done' as HopDeployStatus
    if (i === completedIndex + 1) return 'deploying' as HopDeployStatus
    return 'waiting' as HopDeployStatus
  })
  return normalizeHopDeployStatus(next)
}
