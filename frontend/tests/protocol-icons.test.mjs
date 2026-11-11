import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'

const names = [
  'dashboard', 'servers', 'add', 'settings2', 'activity2', 'latency2',
  'bandwidth2', 'security2', 'deploy2', 'global2', 'link2',
  'wireguard', 'amneziawg', 'openvpn', 'tor', 'xray', 'vless', 'socks', 'multihop',
  'xray2', 'socks2', 'chain', 'global', 'add-server', 'deploy', 'link', 'settings',
  'activity', 'latency', 'bandwidth', 'security', 'server', 'lock',
]

test('app icons are square RGBA PNGs, including all alternative assets', () => {
  for (const name of names) {
    const png = readFileSync(new URL(`../src/assets/icons/${name}.png`, import.meta.url))
    assert.equal(png.subarray(0, 8).toString('hex'), '89504e470d0a1a0a', name)
    assert.equal(png.readUInt32BE(16), png.readUInt32BE(20), `${name} must be square`)
    assert.ok(png.readUInt32BE(16) >= 500, name)
    assert.equal(png[25], 6, `${name} must preserve an alpha channel`)
  }
})


test('navigation uses distinct icon artwork for every destination', () => {
  const source = readFileSync(new URL('../src/components/layout/DashboardLayout.tsx', import.meta.url), 'utf8')
  const icons = [...source.matchAll(/icon: '([^']+)'/g)].map((match) => match[1])
  assert.deepEqual(icons, ['dashboard', 'servers', 'latency2', 'lock', 'link', 'socks', 'global2', 'settings2'])
  assert.equal(new Set(icons).size, icons.length)
})
