import test from 'node:test'
import assert from 'node:assert/strict'
import { createServer } from 'vite'

test('Hysteria 2 exports real server YAML and an isolated URI for QR/import', async () => {
  const vite = await createServer({ server: { middlewareMode: true }, logLevel: 'error' })
  try {
    const { buildClientConfigBundle } = await vite.ssrLoadModule('/src/lib/client-config.ts')
    const server = { name: 'Test Server', host: '192.0.2.1' }
    const config = { id: 'id', protocol: 'hysteria2', status: 'active' }
    const missing = buildClientConfigBundle(config, server)
    assert.equal(missing.fileConfig, '')
    assert.equal(missing.shareLink, undefined)
    const uri = 'hysteria2://password@192.0.2.1:444/?insecure=1&sni=bing.com#Hoplyra-HY2'
    const yaml = `# ${uri}\nserver: "192.0.2.1:444"\nauth: "password"\n`
    const bundle = buildClientConfigBundle({ ...config, clientConfig: yaml }, server)
    assert.equal(bundle.fileConfig, yaml)
    assert.equal(bundle.filename, 'hoplyra-hysteria2-test-server.yaml')
    assert.deepEqual(bundle.shareLink, { scheme: 'hysteria2', uri })
    assert.equal(bundle.qrPayload, uri)
    assert.equal(bundle.supportsQr, true)
    assert.equal(buildClientConfigBundle({ ...config, clientConfig: uri }, server).shareLink.uri, uri)
  } finally {
    await vite.close()
  }
})
