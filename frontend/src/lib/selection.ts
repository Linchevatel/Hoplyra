/** Keep explicit selection (including offline servers), replacing only empty/chain-locked choices. */
export function resolveSelectedServer(
  selected: string,
  onlineServers: readonly { id: string }[],
  locked: ReadonlySet<string>,
): string {
  if (onlineServers.length === 0) return selected
  if (selected && !locked.has(selected)) return selected
  return onlineServers.find((server) => !locked.has(server.id))?.id ?? ''
}
