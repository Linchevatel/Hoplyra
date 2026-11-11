import test from 'node:test'
import assert from 'node:assert/strict'
import { existsSync, readFileSync } from 'node:fs'
import { createServer } from 'vite'
import { chromium } from '@playwright/test'
import { resolveSelectedServer } from '../src/lib/selection.ts'

test('selection preserves explicit/offline choice and replaces only empty/locked choices', () => {
  const online = [{ id: 'a' }, { id: 'b' }]
  assert.equal(resolveSelectedServer('b', online, new Set()), 'b')
  assert.equal(resolveSelectedServer('offline', online, new Set()), 'offline')
  assert.equal(resolveSelectedServer('', online, new Set(['a'])), 'b')
  assert.equal(resolveSelectedServer('a', online, new Set(['a'])), 'b')
  assert.equal(resolveSelectedServer('a', online, new Set(['a', 'b'])), '')
  assert.equal(resolveSelectedServer('a', [], new Set(['a'])), 'a')
})

test('Ink Amber is the only base palette and requires no storage bootstrap', () => {
  const html = readFileSync(new URL('../index.html', import.meta.url), 'utf8')
  const css = readFileSync(new URL('../src/index.css', import.meta.url), 'utf8')
  const settings = readFileSync(new URL('../src/components/dashboard/SettingsTab.tsx', import.meta.url), 'utf8')
  const sidebar = readFileSync(new URL('../src/components/layout/DashboardLayout.tsx', import.meta.url), 'utf8')
  assert.doesNotMatch(html + css + settings, /data-theme|hoplyra-theme|stealth-ops|crimson-wire|cyberpunk/)
  assert.match(css, /@theme\s*\{[^}]*--color-surface-950: #0D1117;[^}]*--color-accent-cyan: #D4A24C;/s)
  assert.doesNotMatch(settings, /chooseTheme|settings\.appearance|aria-pressed/)
  assert.doesNotMatch(sidebar, /newChain|button-primary/)
})

// Use an already installed browser; never download one during tests.
const browserPath = process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH ?? chromium.executablePath()
test('browser lifecycle regressions (local mocks only)', {
  skip: !existsSync(browserPath) && 'Set PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH to an installed Chromium',
}, async (t) => {
  const server = await createServer({ server: { host: '127.0.0.1', port: 0 }, logLevel: 'error' })
  await server.listen()
  const address = server.httpServer.address()
  let browser
  try {
    browser = await chromium.launch({ headless: true, executablePath: browserPath })
    const page = await browser.newPage()
    const errors = []
    page.on('pageerror', (error) => errors.push(error.message))
    await page.route('**/*', async (route) => {
      const url = new URL(route.request().url())
      if (url.hostname !== '127.0.0.1') return route.abort()
      if (url.pathname === '/regression') return route.fulfill({ contentType: 'text/html', body: `
        <div id="fixture"></div><script type="module">
        import RefreshRuntime from '/@react-refresh';
        RefreshRuntime.injectIntoGlobalHook(window);
        window.$RefreshReg$ = () => {}; window.$RefreshSig$ = () => (type) => type;
        window.__vite_plugin_react_preamble_installed__ = true;
        </script>` })
      if (url.pathname.startsWith('/api/')) return route.fulfill({ status: 503, body: '{}' })
      return route.continue()
    })
    await page.goto(`http://127.0.0.1:${address.port}/regression`)
    await page.waitForFunction(() => window.__vite_plugin_react_preamble_installed__)
    await page.evaluate(async () => {
      const { default: React } = await import('/node_modules/.vite/deps/react.js')
      const { default: { createRoot } } = await import('/node_modules/.vite/deps/react-dom_client.js')
      window.fixture = { React, root: createRoot(document.getElementById('fixture')) }
    })

    await t.test('login branding, mobile layout and failed authentication', async () => {
      await page.evaluate(async () => {
        await import('/src/index.css')
        const { LoginPage } = await import('/src/pages/LoginPage.tsx')
        const { I18nProvider } = await import('/src/i18n/I18nProvider.tsx')
        const { AuthContext } = await import('/src/lib/auth-context.ts')
        const { React: R, root } = window.fixture
        root.render(R.createElement(I18nProvider, null, R.createElement(AuthContext.Provider, {
          value: { loading: false, authenticated: false, defaultPassword: true,
            login: async (username, password) => {
              window.loginAttempt = { username, password }
              throw new Error('401')
            } },
        }, R.createElement(LoginPage))))
      })
      const logo = page.getByRole('img', { name: 'Hoplyra', exact: true })
      await logo.waitFor()
      assert.equal(await logo.evaluate((img) => img.complete && img.naturalWidth > 0), true)
      assert.equal(await logo.evaluate((img) => img.getBoundingClientRect().width), 96)
      assert.equal(await page.getByRole('status').count(), 1)
      await page.setViewportSize({ width: 375, height: 700 })
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true)
      await page.locator('input[name="username"]').fill(' admin ')
      await page.locator('input[name="password"]').fill('incorrect')
      await page.locator('button[type="submit"]').click()
      await page.getByRole('alert').waitFor()
      assert.deepEqual(await page.evaluate(() => window.loginAttempt), { username: 'admin', password: 'incorrect' })
      assert.equal(await page.locator('button[type="submit"]').isEnabled(), true)
      await page.setViewportSize({ width: 1280, height: 720 })
      await page.evaluate(() => window.fixture.root.render(null))
    })

    await t.test('Ink Amber chain visualization uses real hop data and respects narrow screens', async () => {
      await page.evaluate(async () => {
        await import('/src/index.css')
        const { ChainVisualization } = await import('/src/components/dashboard/ChainVisualization.tsx')
        const { I18nProvider } = await import('/src/i18n/I18nProvider.tsx')
        const { React: R, root } = window.fixture
        const servers = [
          { id: 'us', name: 'USA', location: 'US, New York', latencyMs: 193 },
          { id: 'de', name: 'Germany', location: 'DE, Frankfurt', latencyMs: 107.1 },
          { id: 'jp', name: 'Japan', location: 'JP, Tokyo' },
        ]
        root.render(R.createElement(I18nProvider, null, R.createElement('div', { className: 'glass bento-card m-6 p-6' },
          R.createElement('h2', null, 'Chain Visualization'),
          R.createElement(ChainVisualization, { servers, hops: servers.map((server) => ({ id: server.id, serverId: server.id, protocol: 'wg' })) }))))
      })
      await page.locator('.chain-node').first().waitFor()
      assert.equal(await page.locator('.chain-node').count(), 3)
      assert.match(await page.locator('.chain-track').innerText(), /US, New York/)
      assert.match(await page.locator('.chain-track').innerText(), /193 ms/)
      assert.match(await page.locator('.chain-node').last().innerText(), /—/)
      assert.equal(await page.locator('.chain-node').last().innerText().then((text) => text.includes('0 ms')), false)
      await page.setViewportSize({ width: 375, height: 700 })
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true)
      await page.emulateMedia({ reducedMotion: 'reduce' })
      assert.equal(await page.locator('.chain-orb').first().evaluate((el) => getComputedStyle(el).animationName), 'none')
      await page.emulateMedia({ reducedMotion: 'no-preference' })
      await page.setViewportSize({ width: 1280, height: 720 })
      await page.screenshot({ path: '/tmp/ink-amber-visualization.png' })
      await page.evaluate(() => {
        window.fixture.root.render(null)

      })
    })

    await t.test('Settings has one unconditional Ink Amber palette, even with obsolete stored preferences', async () => {
      await page.evaluate(async () => {
        await import('/src/index.css')
        const { SettingsTab } = await import('/src/components/dashboard/SettingsTab.tsx')
        const { I18nProvider } = await import('/src/i18n/I18nProvider.tsx')
        const { AuthContext } = await import('/src/lib/auth-context.ts')
        const { React: R, root } = window.fixture
        root.render(R.createElement(I18nProvider, null, R.createElement(AuthContext.Provider, {
          value: { username: 'test', defaultPassword: false },
        }, R.createElement(SettingsTab))))
      })
      await page.getByRole('heading', { name: 'Settings', exact: true }).waitFor()
      assert.equal(await page.getByRole('button', { name: /Ink Amber|Crimson Wire|Stealth Ops|Classic/ }).count(), 0)
      for (const stored of ['classic', 'stealth-ops', 'crimson-wire', 'cyberpunk']) {
        await page.evaluate((value) => localStorage.setItem('hoplyra-theme', value), stored)
        assert.equal(await page.evaluate(() => getComputedStyle(document.documentElement).getPropertyValue('--color-accent-cyan').trim()), '#D4A24C')
        assert.equal(await page.evaluate(() => document.documentElement.hasAttribute('data-theme')), false)
      }
      await page.evaluate(() => localStorage.removeItem('hoplyra-theme'))
      assert.equal(await page.locator('.bento-card').first().evaluate((el) => getComputedStyle(el, '::before').content), 'none')
      assert.equal(await page.locator('.bento-card').first().evaluate((el) => getComputedStyle(el).borderRadius), '13px')
      assert.equal(await page.evaluate(() => getComputedStyle(document.body).backgroundImage.includes('linear-gradient')), false)
      await page.evaluate(() => {
        window.fixture.root.render(null)
        document.querySelector('style[data-vite-dev-id$="/src/index.css"]')?.remove()
      })
    })

    await t.test('decorative dashboard PNG icons load at requested sizes; fallback protocols stay valid', async () => {
      await page.evaluate(async () => {
        const { AppIcon } = await import('/src/components/ui/AppIcon.tsx')
        const { ProtocolIcon } = await import('/src/components/ui/ProtocolIcon.tsx')
        const { React: R, root } = window.fixture
        root.render(R.createElement('div', { id: 'icon-fixture' },
          ...['activity', 'latency', 'bandwidth', 'security', 'add-server', 'deploy', 'global', 'socks', 'multihop'].map((name) =>
            R.createElement(AppIcon, { key: name, name, size: name === 'multihop' ? 64 : 24 })),
          ...['hysteria2', 'tuic'].map((protocol) => R.createElement(ProtocolIcon, { key: protocol, protocol }))))
      })
      await page.locator('#icon-fixture img').first().waitFor({ state: 'attached' })
      await page.waitForFunction(() => [...document.querySelectorAll('#icon-fixture img')].every((img) => img.complete && img.naturalWidth > 0))
      assert.equal(await page.locator('#icon-fixture img').count(), 11)
      assert.equal(await page.locator('#icon-fixture img[aria-hidden="true"]').count(), 9)
      assert.equal(await page.locator('#icon-fixture img').first().getAttribute('width'), '24')
      assert.equal(await page.locator('#icon-fixture img').nth(8).getAttribute('width'), '64')
      await page.evaluate(() => window.fixture.root.render(null))
    })

    for (const tab of ['Overview', 'Servers', 'Status', 'Vpn', 'Chains', 'Proxy', 'ClientConfigs', 'Settings']) {
      await t.test(`${tab} renders empty and populated states with consistent headers and no overflow`, async () => {
        const beforeErrors = errors.length
        await page.evaluate(async (name) => {
          // Reattach the compiled stylesheet after earlier isolated fixtures remove it.
          const css = await import('/src/index.css?inline')
          const style = document.createElement('style')
          style.id = 'smoke-style'
          style.textContent = css.default
          document.head.append(style)
          const { DashboardSmoke } = await import('/tests/dashboard-smoke.tsx')
          const { React: R, root } = window.fixture
          root.render(R.createElement(DashboardSmoke, { name }))
        }, tab)
        await page.locator('.dashboard-page h1').waitFor()
        assert.equal(await page.locator('.dashboard-page h1').count(), 1)
        assert.ok((await page.locator('.dashboard-page h1 + p').innerText()).trim())
        assert.equal(await page.locator('.dashboard-page h1').evaluate((el) => getComputedStyle(el).fontWeight), '500')
        await page.setViewportSize({ width: 375, height: 800 })
        assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true)
        await page.screenshot({ path: `/tmp/ink-amber-final-${tab.toLowerCase()}.png`, fullPage: true })
        await page.setViewportSize({ width: 1280, height: 720 })
        await page.evaluate(async (name) => {
          const { DashboardSmoke } = await import('/tests/dashboard-smoke.tsx')
          const { React: R, root } = window.fixture
          root.render(R.createElement(DashboardSmoke, { name, populated: true }))
        }, tab)
        await page.locator('.dashboard-page h1').waitFor()
        if (tab === 'Overview') await page.locator('.server-table tbody tr').first().waitFor()
        await page.screenshot({ path: `/tmp/ink-amber-final-${tab.toLowerCase()}-populated.png`, fullPage: true })
        await page.setViewportSize({ width: 375, height: 800 })
        assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true)
        if (tab === 'Chains') {
          // Exercise an actual AWG draft inside dashboard-sized mobile gutters.
          assert.equal(await page.locator('.dashboard-page [aria-haspopup="listbox"]').count(), 4)
          assert.equal(await page.locator('.dashboard-page .list-none').evaluateAll((items) =>
            items.every((item) => item.scrollWidth <= item.clientWidth)), true)
          await page.locator('.dashboard-page [aria-haspopup="listbox"]').first().click()
          await page.locator('[role="listbox"]').waitFor()
          assert.equal(await page.locator('[role="listbox"]').evaluate((el) => {
            const rect = el.getBoundingClientRect()
            return el.parentElement === document.body && rect.left >= 0 && rect.right <= innerWidth
          }), true)
          await page.keyboard.press('Escape')
        }
        await page.setViewportSize({ width: 1280, height: 720 })
        assert.deepEqual(errors.slice(beforeErrors), [])
        await page.evaluate(() => { window.fixture.root.render(null); document.querySelectorAll('#smoke-style, style[data-vite-dev-id$="/src/index.css"]').forEach((style) => style.remove()) })
      })
    }

    await t.test('AWG generation ignores unmount and stale version failures; empty fields delete keys', async () => {
      const pending = []
      await page.route('**/api/awg/generate?**', (route) => { pending.push(route) })
      await page.evaluate(async () => {
        const { AwgAdvanced } = await import('/src/components/dashboard/AwgAdvanced.tsx')
        const { I18nProvider } = await import('/src/i18n/I18nProvider.tsx')
        const { React: R, root } = window.fixture
        window.awgChanges = []
        function Probe({ version }) {
          const [value, setValue] = R.useState({ jc: 5 })
          const [mode, setMode] = R.useState('custom')
          return R.createElement(AwgAdvanced, { version, value, mode, onModeChange(next, params) { setMode(next); setValue(params); if (next === 'custom') window.awgChanges.push(params) }, onChange(next) { window.awgChanges.push(next); setValue(next) } })
        }
        window.renderAwg = (version) => root.render(R.createElement(I18nProvider, null, R.createElement(Probe, { version })))
        window.renderAwg('awg3.1')
      })
      await page.locator('#awg-jc').fill('')
      await page.waitForFunction(() => window.awgChanges.length === 1)
      assert.deepEqual(await page.evaluate(() => window.awgChanges[0]), {})
      await page.getByRole('checkbox', { name: /Automatic parameters|Автоматические параметры/ }).check()
      await page.getByRole('checkbox', { name: /Automatic parameters|Автоматические параметры/ }).click()
      await assertEventually(() => pending.length === 1)
      await page.evaluate(() => window.fixture.root.render(null))
      await page.waitForTimeout(30)
      await pending.shift().fulfill({ json: { params: { jc: 10 } } })
      await page.waitForTimeout(50)
      assert.equal(await page.evaluate(() => window.awgChanges.length), 2)
      await page.evaluate(() => window.renderAwg('awg3.1'))
      await page.getByRole('checkbox', { name: /Automatic parameters|Автоматические параметры/ }).check()
      await page.getByRole('checkbox', { name: /Automatic parameters|Автоматические параметры/ }).click()
      await assertEventually(() => pending.length === 1)
      await page.evaluate(() => window.renderAwg('awg'))
      await page.waitForTimeout(30)
      await pending.shift().fulfill({ status: 500, json: { detail: 'stale generation error' } })
      await page.waitForTimeout(50)
      assert.equal(await page.getByRole('alert').count(), 0)
      await page.evaluate(() => window.fixture.root.render(null))
      await page.unroute('**/api/awg/generate?**')
    })

    await t.test('AWG modes, manual autofill, digit filtering, live limits and API payloads', async () => {
      let generations = 0
      await page.route('**/api/awg/generate?**', route => {
        generations++
        return route.fulfill({ json: { params: { jc: 5, jmin: 40, jmax: 70, s1: 12, s2: 12, s3: 12, s4: 12, h1: '100-200', h2: '300-400', h3: '500-600', h4: '700-800' } } })
      })
      await page.evaluate(async () => {
        const { AwgAdvanced } = await import('/src/components/dashboard/AwgAdvanced.tsx')
        const { I18nProvider } = await import('/src/i18n/I18nProvider.tsx')
        const { React: R, root } = window.fixture
        function Probe() {
          const [mode, setMode] = R.useState('none')
          const [value, setValue] = R.useState()
          window.awgState = { mode, value }
          return R.createElement(AwgAdvanced, { version: 'awg3.1', mode, value, onChange: setValue, onModeChange(next, params) { setMode(next); setValue(params) } })
        }
        root.render(R.createElement(I18nProvider, null, R.createElement(Probe)))
      })
      await page.getByRole('radio', { name: /Default/ }).waitFor()
      assert.equal(await page.locator('#awg-jc').count(), 0)
      assert.equal(await page.getByRole('checkbox').count(), 0)
      await page.getByRole('radio', { name: /Stealth/ }).click()
      const auto = page.getByRole('checkbox', { name: /Automatic parameters|Автоматические параметры/ })
      assert.equal(await auto.isChecked(), true)
      assert.equal(generations, 0)
      assert.equal(await page.locator('#awg-jc').count(), 0)
      await auto.click()
      await page.locator('#awg-jc').waitFor()
      assert.equal(generations, 1)
      assert.equal(await page.locator('#awg-jc').inputValue(), '5')
      assert.equal(await page.locator('#awg-h1').inputValue(), '100')
      assert.equal(await page.locator('#awg-jc').getAttribute('type'), 'text')
      assert.equal(await page.locator('#awg-jc').getAttribute('inputmode'), 'numeric')
      await page.locator('#awg-jc').fill('x12e9-')
      assert.equal(await page.locator('#awg-jc').inputValue(), '129')
      assert.equal(await page.locator('#awg-jc').getAttribute('aria-invalid'), 'true')
      await page.locator('#awg-jc').fill('128')
      assert.equal(await page.locator('#awg-jc').getAttribute('aria-invalid'), 'false')
      await page.locator('#awg-jmax').fill('1')
      assert.equal(await page.locator('#awg-jmax').getAttribute('aria-invalid'), 'true')
      await page.locator('#awg-h1').fill('4294967296')
      assert.equal(await page.locator('#awg-h1').getAttribute('aria-invalid'), 'true')
      await page.locator('#awg-headerProtectionKey').fill('a'.repeat(64))
      await page.locator('#awg-s1').fill('11')
      assert.equal(await page.locator('#awg-s1').getAttribute('aria-invalid'), 'true')
      assert.equal(generations, 1)
      await page.getByRole('radio', { name: /Default/ }).click()
      assert.equal(await page.locator('#awg-jc').count(), 0)
      assert.deepEqual(await page.evaluate(() => window.awgState), { mode: 'none', value: undefined })
      const payloads = []
      await page.route('**/api/configs/deploy', route => {
        payloads.push(route.request().postDataJSON())
        return route.fulfill({ json: { id: 'test' } })
      })
      await page.evaluate(async () => {
        const { deployConfig } = await import('/src/lib/api.ts')
        await deployConfig('s', 'awg', { awgVersion: 'awg3.1', awgMode: 'none', awgParams: { jc: 5 } })
        await deployConfig('s', 'awg', { awgVersion: 'awg3.1', awgMode: 'auto' })
        await deployConfig('s', 'awg', { awgVersion: 'awg3.1', awgMode: 'custom', awgParams: { jc: 10 } })
      })
      assert.equal('awgParams' in payloads[0], false)
      assert.equal(payloads[0].awgMode, 'none')
      assert.equal('awgParams' in payloads[1], false)
      assert.deepEqual(payloads[2].awgParams, { jc: 10 })
      await page.unroute('**/api/configs/deploy')
      await page.unroute('**/api/awg/generate?**')
      await page.evaluate(() => window.fixture.root.render(null))
    })

    await t.test('StrictMode auth ignores initialization response after cleanup', async () => {
      const pending = []
      await page.route('**/api/auth/me', (route) => { pending.push(route) })
      await page.evaluate(async () => {
        const { AuthProvider } = await import('/src/lib/auth.tsx')
        const { useAuth } = await import('/src/lib/auth-context.ts')
        const { React: R, root } = window.fixture
        function Probe() { const auth = useAuth(); return R.createElement('pre', null, JSON.stringify(auth)) }
        root.render(R.createElement(R.StrictMode, null, R.createElement(AuthProvider, null, R.createElement(Probe))))
      })
      await assertEventually(() => pending.length === 2)
      await pending[1].fulfill({ json: { authenticated: true, authRequired: true, username: 'new-session' } })
      await page.waitForFunction(() => document.querySelector('#fixture').textContent.includes('new-session'))
      await pending[0].fulfill({ json: { authenticated: false, authRequired: true, username: 'stale-session' } })
      await page.waitForTimeout(50)
      assert.ok((await page.locator('#fixture').textContent()).includes('new-session'))
      await page.unroute('**/api/auth/me')
    })

    await t.test('config selection isolates requests and reflects saved updates', async () => {
      const pending = new Map()
      await page.route('**/api/configs/*/client-config', (route) => {
        pending.set(new URL(route.request().url()).pathname.split('/')[3], route)
      })
      await page.evaluate(async () => {
        const { ClientConfigModal } = await import('/src/components/dashboard/ClientConfigModal.tsx')
        const { I18nProvider } = await import('/src/i18n/I18nProvider.tsx')
        const { React: R, root } = window.fixture
        window.renderConfig = (id, clientConfig) => root.render(R.createElement(I18nProvider, null,
          R.createElement(ClientConfigModal, { config: { id, serverId: 's', protocol: 'wg', status: 'active', createdAt: '2026-01-01', clientConfig },
            server: { id: 's', name: 'mock', host: '192.0.2.1', port: 22, username: 'root', status: 'online' }, onClose() {} })))
        window.renderConfig('a', 'config-a')
      })
      await assertEventually(() => pending.has('a'))
      await page.evaluate(() => window.renderConfig('b', 'config-b'))
      await assertEventually(() => pending.has('b'))
      await pending.get('b').fulfill({ json: { protocol: 'wg', config: 'fresh-b' } })
      await page.waitForFunction(() => document.querySelector('#fixture').textContent.includes('fresh-b'))
      await pending.get('a').fulfill({ json: { protocol: 'wg', config: 'stale-a' } })
      await page.waitForTimeout(50)
      assert.ok(!(await page.locator('#fixture').textContent()).includes('stale-a'))
      await page.evaluate(() => window.renderConfig('b', 'saved-b'))
      await page.waitForFunction(() => document.querySelector('#fixture').textContent.includes('saved-b'))
      const configText = '[Interface]\nPrivateKey = private-test-key\nHeaderProtectionKey = hpk-test-key\nS1 = 12'
      await page.evaluate((text) => window.renderConfig('b', text), configText)
      await page.waitForFunction(() => [...document.querySelectorAll('#fixture pre')].some(el => el.textContent.includes('hpk-test-key')))
      assert.equal(await page.locator('#fixture pre').last().textContent(), configText)
      assert.equal(await page.locator('#fixture button').filter({ hasText: /Show|Hide|Показать|Скрыть/i }).count(), 0)
    })

    await t.test('metrics health resolution after unmount cannot start polling or SSE', async () => {
      const pending = []
      await page.route('**/api/health', (route) => { pending.push(route) })
      await page.evaluate(async () => {
        const { StatusTab } = await import('/src/components/dashboard/StatusTab.tsx')
        const { DashboardContext } = await import('/src/lib/dashboard-context.ts')
        const { I18nProvider } = await import('/src/i18n/I18nProvider.tsx')
        const { React: R, root } = window.fixture
        window.polls = 0; window.streams = 0
        const setInterval = window.setInterval
        window.setInterval = (...args) => { window.polls++; return setInterval(...args) }
        window.EventSource = class { constructor() { window.streams++ } close() {} }
        root.render(R.createElement(I18nProvider, null,
          R.createElement(DashboardContext.Provider, { value: { servers: [] } }, R.createElement(StatusTab))))
      })
      await assertEventually(() => pending.length > 0)
      await page.evaluate(() => window.fixture.root.render(null))
      await page.waitForFunction(() => document.querySelector('#fixture').textContent === '')
      for (const route of pending) await route.fulfill({ json: { status: 'ok' } })
      await page.waitForTimeout(50)
      assert.deepEqual(await page.evaluate(() => [window.polls, window.streams]), [0, 0])
    })
    await t.test('saved history reconciles with API configs and keeps stable ids under StrictMode', async () => {
      await page.route('**/api/health', (route) => route.fulfill({ json: { status: 'ok' } }))
      await page.route('**/api/servers', (route) => route.fulfill({ json: [{ id: 's', name: 'mock', host: '192.0.2.1', port: 22, username: 'root', status: 'online', location: 'US' }] }))
      await page.route('**/api/configs', (route) => route.fulfill({ json: [{
        id: 'deployed', serverId: 's', protocol: 'wg', status: 'active', createdAt: '2026-01-01',
      }] }))
      await page.evaluate(async () => {
        localStorage.setItem('hoplyra_deploy_history', JSON.stringify([{
          id: 'stable-id', userId: 'local', userName: 'tester', type: 'vpn', serverId: 's', protocol: 'wg',
          status: 'deploying', message: 'pending', createdAt: new Date().toISOString(),
        }]))
        const { DashboardProvider } = await import('/src/lib/dashboard.tsx')
        const { useDashboard } = await import('/src/lib/dashboard-context.ts')
        const { React: R, root } = window.fixture
        function Probe() {
          const dashboard = useDashboard()
          return R.createElement('pre', null, JSON.stringify({ configs: dashboard.configs, history: dashboard.deployHistory }))
        }
        root.render(R.createElement(R.StrictMode, null, R.createElement(DashboardProvider, null, R.createElement(Probe))))
      })
      await page.waitForFunction(() => {
        const state = JSON.parse(document.querySelector('#fixture').textContent || '{}')
        return state.history?.[0]?.status === 'success'
      })
      const saved = await page.evaluate(() => JSON.parse(localStorage.getItem('hoplyra_deploy_history')))
      assert.equal(saved.length, 1)
      assert.equal(saved[0].id, 'stable-id')
      assert.equal(saved[0].status, 'success')
      await page.evaluate(() => window.fixture.root.render(null))
    })
    await t.test('409 never recovers old VPN/chain; unique pending ids and synchronous server locks', async () => {
      const servers = ['a', 'b', 'c'].map((id) => ({ id, name: id, host: '192.0.2.1', port: 22, username: 'root', status: 'online' }))
      const old = { id: 'old-active', serverId: 'a', protocol: 'wg', status: 'active', createdAt: '2020-01-01' }
      const oldChain = { id: 'old-chain', serverId: 'b', protocol: 'wg', status: 'active', createdAt: '2020-01-01', hops: [{ id: 'h1', serverId: 'b', protocol: 'wg' }, { id: 'h2', serverId: 'c', protocol: 'wg' }] }
      const posts = []
      let listReads = 0
      await page.route('**/api/servers', (route) => route.fulfill({ json: servers }))
      await page.route('**/api/configs', (route) => { listReads++; return route.fulfill({ json: [old, oldChain] }) })
      await page.route('**/api/configs/deploy', (route) => { posts.push(route) })
      await page.route('**/api/configs/deploy-chain', (route) => { posts.push(route) })
      await page.evaluate(async () => {
        localStorage.clear(); sessionStorage.clear()
        const { DashboardProvider } = await import('/src/lib/dashboard.tsx')
        const { useDashboard } = await import('/src/lib/dashboard-context.ts')
        const { React: R, root } = window.fixture
        function Probe() {
          const d = useDashboard(); window.dashboard = d
          return R.createElement('pre', null, JSON.stringify({ configs: d.configs, notifications: d.notifications, servers: d.servers }))
        }
        root.render(R.createElement(DashboardProvider, null, R.createElement(Probe)))
      })
      await page.waitForFunction(() => window.dashboard?.configs.some((c) => c.id === 'old-active'))
      // Run both calls before React renders: a state-only lock would admit the duplicate.
      await page.evaluate(() => {
        window.deployResults = []
        window.dashboard.deployProtocol('a', 'wg').then((value) => window.deployResults.push(value?.id ?? null))
        window.dashboard.deployProtocol('a', 'awg').catch((err) => { window.lockError = err.message })
        window.dashboard.deployProtocol('b', 'wg').then((value) => window.deployResults.push(value?.id ?? null))
      })
      await assertEventually(() => posts.length === 2)
      await page.waitForFunction(() => window.dashboard.configs.filter((c) => c.id.startsWith('pending-')).length === 2)
      const before = await page.evaluate(() => ({ ids: window.dashboard.configs.filter((c) => c.id.startsWith('pending-')).map((c) => c.id), lock: window.lockError, oldPresent: window.dashboard.configs.some((c) => c.id === 'old-active') }))
      assert.equal(new Set(before.ids).size, 2)
      assert.ok(before.lock)
      assert.equal(before.oldPresent, true)
      const readsBefore = listReads
      const firstPosts = posts.splice(0)
      await firstPosts[0].fulfill({ status: 409, json: { detail: 'reserved' } })
      await page.waitForFunction(() => window.deployResults.length === 1)
      assert.equal(await page.evaluate(() => window.dashboard.configs.filter((c) => c.id.startsWith('pending-')).length), 1, 'one response cannot remove another deployment')
      await firstPosts[1].fulfill({ status: 409, json: { detail: 'reserved' } })
      await page.waitForFunction(() => window.deployResults.length === 2)
      assert.deepEqual(await page.evaluate(() => window.deployResults), [null, null])
      assert.ok(listReads > readsBefore, 'conflict refetches authoritative list')
      assert.equal(await page.evaluate(() => window.dashboard.notifications.some((n) => n.type === 'deploy_complete')), false)
      // A failed response must release the lock. Chain locks cover the whole set.
      await page.evaluate(() => {
        window.chainDone = false
        window.dashboard.deployChain([{ id: 'h1', serverId: 'b', protocol: 'wg' }, { id: 'h2', serverId: 'c', protocol: 'wg' }]).then(() => { window.chainDone = true })
        window.dashboard.deployProtocol('c', 'wg').catch((err) => { window.chainLockError = err.message })
      })
      await assertEventually(() => posts.length === 1)
      await page.waitForFunction(() => window.dashboard.configs.some((c) => c.id.startsWith('pending-chain-')))
      assert.ok(await page.evaluate(() => window.chainLockError))
      assert.equal(await page.evaluate(() => window.dashboard.configs.some((c) => c.id === 'old-chain')), true)
      // Exercise background reconciliation before reserve is accepted.
      await page.waitForTimeout(3100)
      assert.equal(await page.evaluate(() => window.dashboard.configs.some((c) => c.id === 'old-chain')), true)
      await posts[0].fulfill({ status: 409, json: { detail: 'reserved chain' } })
      await page.waitForFunction(() => window.chainDone)
      assert.equal(await page.evaluate(() => window.dashboard.configs.some((c) => c.id.startsWith('pending'))), false)
      assert.equal(await page.evaluate(() => window.dashboard.configs.some((c) => c.id === 'old-chain')), true)
      assert.equal(await page.evaluate(() => window.dashboard.chainDeployUi), null)
      assert.equal(await page.evaluate(() => window.dashboard.notifications.some((n) => n.type === 'deploy_complete')), false)
      await page.evaluate(() => window.fixture.root.render(null))
    })
    assert.deepEqual(errors, [])
  } finally {
    await browser?.close()
    await server.close()
  }
})

async function assertEventually(predicate) {
  for (let attempt = 0; attempt < 200; attempt++) {
    if (predicate()) return
    await new Promise((resolve) => setTimeout(resolve, 10))
  }
  assert.ok(predicate(), 'expected local request did not arrive')
}
