import test from 'node:test'
import assert from 'node:assert/strict'
import { createServer } from 'vite'

test('ShadowQUIC exports real server YAML and an isolated sq:// URI for QR/import', async () => {
  const vite = await createServer({ server: { middlewareMode: true }, logLevel: 'error' })
  try {
    const { buildClientConfigBundle } = await vite.ssrLoadModule('/src/lib/client-config.ts')
    const server = { name: 'Test Server', host: '192.0.2.1' }
    const config = { id: 'id', protocol: 'shadowquic', status: 'active' }
    const missing = buildClientConfigBundle(config, server)
    assert.equal(missing.fileConfig, '')
    assert.equal(missing.shareLink, undefined)
    const uri = 'sq://primary:secret@192.0.2.1:444?sni=cloudflare.com&alpn=h3&udp_mode=datagram&congestion=bbr#Hoplyra-SQ'
    const yaml = `# ${uri}\ninbounds:\n  - tag: socks-inbound\n`
    const bundle = buildClientConfigBundle({ ...config, clientConfig: yaml }, server)
    assert.equal(bundle.fileConfig, yaml)
    assert.equal(bundle.filename, 'hoplyra-shadowquic-test-server.yaml')
    assert.deepEqual(bundle.shareLink, { scheme: 'shadowquic', uri })
    assert.equal(bundle.qrPayload, uri)
    assert.equal(bundle.supportsQr, true)
    assert.equal(buildClientConfigBundle({ ...config, clientConfig: uri }, server).shareLink.uri, uri)
  } finally {
    await vite.close()
  }
})
