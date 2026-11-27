export type DeployLogEvent = { seq: number; ts: number; level: 'info' | 'warn' | 'error' | 'success'; stage: string | null; message: string }
export type DeployLogStatus = { active: boolean; finished: boolean; ok: boolean | null; seq: number }
export type DeployLogPayload = { events?: DeployLogEvent[]; status: DeployLogStatus }
export type DeployLogState = { events: DeployLogEvent[]; status: DeployLogStatus }
export const emptyDeployLog: DeployLogState = { events: [], status: { active: false, finished: false, ok: null, seq: 0 } }

export function mergeDeployLog(state: DeployLogState, payload: DeployLogPayload): DeployLogState {
  const bySeq = new Map(state.events.map((event) => [event.seq, event]))
  for (const event of payload.events ?? []) bySeq.set(event.seq, event)
  return { events: [...bySeq.values()].sort((a, b) => a.seq - b.seq).slice(-2000), status: payload.status }
}

export function deployLogOutcome(state: DeployLogState): 'deploying' | 'ok' | 'error' | 'cancelled' {
  if (!state.status.finished) return 'deploying'
  if (state.status.ok) return 'ok'
  return state.events.some((event) => event.stage === 'cancelled') ? 'cancelled' : 'error'
}
