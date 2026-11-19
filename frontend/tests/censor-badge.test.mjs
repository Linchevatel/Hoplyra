import test from 'node:test'
import assert from 'node:assert/strict'
import { existsSync } from 'node:fs'
import { createServer } from 'vite'
import { chromium } from '@playwright/test'

const browserPath = process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH ?? '/usr/bin/chromium-browser'

test('ServerCensorBadge: настоящий рендеринг в браузере (Playwright + Vite) для всех комбинаций статусов', {
  skip: !existsSync(browserPath) && 'Chromium not available',
}, async () => {
  const server = await createServer({
    root: '.',
    server: { host: '127.0.0.1', port: 0 },
    logLevel: 'error',
  })
  await server.listen()
  const address = server.httpServer.address()

  let browser
  try {
    browser = await chromium.launch({
      headless: true,
      executablePath: browserPath,
      args: ['--no-sandbox', '--disable-setuid-sandbox'],
    })
    const page = await browser.newPage()

    await page.route('**/*', async (route) => {
      const url = new URL(route.request().url())
      if (url.hostname !== '127.0.0.1') return route.abort()
      if (url.pathname === '/regression') {
        return route.fulfill({
          contentType: 'text/html',
          body: `
            <div id="fixture"></div>
            <script type="module">
              import RefreshRuntime from '/@react-refresh';
              RefreshRuntime.injectIntoGlobalHook(window);
              window.$RefreshReg$ = () => {}; window.$RefreshSig$ = () => (type) => type;
              window.__vite_plugin_react_preamble_installed__ = true;
            </script>
          `,
        })
      }
      return route.continue()
    })

    await page.goto(`http://127.0.0.1:${address.port}/regression`)
    await page.waitForFunction(() => window.__vite_plugin_react_preamble_installed__)

    await page.evaluate(async () => {
      const { default: React } = await import('/node_modules/.vite/deps/react.js')
      const { default: { createRoot } } = await import('/node_modules/.vite/deps/react-dom_client.js')
      window.fixture = { React, root: createRoot(document.getElementById('fixture')) }
    })

    const testCases = [
      {
        name: 'Замечание [1]: IP в реестре РКН + сбой SSH (ruReachability: unknown) показывает оба факта, а не "Не проверено"',
        status: {
          rknBlocked: true,
          rknExact: true,
          rknSubnet: '185.169.107.0/24',
          ruReachability: 'unknown',
          ruLatencyMs: null,
          details: [{ target: 'probe', ok: false, error: 'ssh connection refused' }],
        },
        expectedTestId: 'censor-badge-rkn',
        expectedTextFragment: 'реестре РКН',
        expectedTitleFragment: 'исходящая связь не проверена',
      },
      {
        name: 'Замечание [1]: Чистый IP + сбой SSH (ruReachability: unknown) показывает нейтральный "Не проверено", а не "Чист"',
        status: {
          rknBlocked: false,
          rknExact: false,
          neighborWarning: false,
          ruReachability: 'unknown',
          ruLatencyMs: null,
          details: [{ target: 'probe', ok: false, error: 'ssh connection timeout' }],
        },
        expectedTestId: 'censor-badge-unknown',
        expectedTextFragment: 'Не проверено',
        expectedTitleFragment: 'ssh connection timeout',
      },
      {
        name: 'Замечание [1]: Неизвестный статус реестра (rknBlocked: null) + отказ исходящей связи показывает оба факта',
        status: {
          rknBlocked: null,
          rknExact: false,
          neighborWarning: false,
          ruReachability: 'blocked',
          ruLatencyMs: null,
          details: [{ target: 'ya.ru', ok: false }],
        },
        expectedTestId: 'censor-badge-blocked',
        expectedTextFragment: 'Нет связи с тестовыми узлами (реестр не проверен)',
        expectedTitleFragment: 'Не удалось установить TCP-соединение ни с одним из трёх тестовых узлов',
      },
      {
        name: 'Замечание [2]: 100% отказ исходящей связи показывает "Нет связи с тестовыми узлами" с честным TCP тултипом',
        status: {
          rknBlocked: false,
          rknExact: false,
          neighborWarning: false,
          ruReachability: 'blocked',
          ruLatencyMs: null,
          details: [{ target: 'ya.ru', ok: false }],
        },
        expectedTestId: 'censor-badge-blocked',
        expectedTextFragment: 'Нет связи с тестовыми узлами',
        expectedTitleFragment: 'Не удалось установить TCP-соединение ни с одним из трёх тестовых узлов',
      },
      {
        name: 'Замечание [5]: Соседняя подсеть /24 показывает "Чист (соседи /24)"',
        status: {
          rknBlocked: false,
          rknExact: false,
          neighborWarning: true,
          rknSubnet: '216.73.157.0/24',
          ruReachability: 'ok',
          ruLatencyMs: 40,
          details: [{ target: 'ya.ru', ok: true, latencyMs: 40 }],
        },
        expectedTestId: 'censor-badge-neighbor',
        expectedTextFragment: 'соседи /24',
        expectedTitleFragment: 'есть заблокированные ресурсы',
      },
      {
        name: 'Полностью чистый сервер показывает "РФ: Чист"',
        status: {
          rknBlocked: false,
          rknExact: false,
          neighborWarning: false,
          rknSubnet: null,
          ruReachability: 'ok',
          ruLatencyMs: 35,
          details: [{ target: 'ya.ru', ok: true, latencyMs: 35 }],
        },
        expectedTestId: 'censor-badge-clean',
        expectedTextFragment: 'РФ: Чист',
        expectedTitleFragment: 'Исходящая связь с сервисами РФ',
      },
    ]

    for (const tc of testCases) {
      await page.evaluate(async (testCase) => {
        const { ServerCensorBadge } = await import('/src/components/dashboard/ServerCensorBadge.tsx')
        const { I18nProvider } = await import('/src/i18n/I18nProvider.tsx')
        const { setLocale } = await import('/src/i18n/core.ts')
        setLocale('ru')

        const { React: R, root } = window.fixture
        root.render(
          R.createElement(
            I18nProvider,
            null,
            R.createElement(ServerCensorBadge, { status: testCase.status }),
          ),
        )
      }, tc)

      const badgeLocator = page.locator(`[data-testid="${tc.expectedTestId}"]`)
      await badgeLocator.waitFor({ state: 'attached' })

      const text = await badgeLocator.innerText()
      const title = await badgeLocator.getAttribute('title')

      assert.ok(
        text.includes(tc.expectedTextFragment),
        `Text mismatch in: ${tc.name} (got: "${text}", expected: "${tc.expectedTextFragment}")`,
      )
      assert.ok(
        title.includes(tc.expectedTitleFragment),
        `Title mismatch in: ${tc.name} (got: "${title}", expected: "${tc.expectedTitleFragment}")`,
      )
    }

    await page.evaluate(() => window.fixture.root.render(null))
  } finally {
    if (browser) await browser.close()
    await server.close()
  }
})
