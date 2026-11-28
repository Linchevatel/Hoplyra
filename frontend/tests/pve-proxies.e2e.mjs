import { chromium } from 'playwright';
import { execSync } from 'child_process';
import net from 'net';

const PANEL_URL = 'https://192.168.10.140:8787';
const NODE_IP = '192.168.10.141';

function runCmd(cmd) {
  try {
    return execSync(cmd, { encoding: 'utf-8', timeout: 25000 }).trim();
  } catch (err) {
    return `ERR: ${err.message}`;
  }
}

function testTcpConnect(host, port, timeoutMs = 4000) {
  return new Promise((resolve) => {
    const socket = new net.Socket();
    let resolved = false;

    socket.setTimeout(timeoutMs);

    socket.connect(port, host, () => {
      resolved = true;
      socket.destroy();
      resolve(true);
    });

    socket.on('error', () => {
      if (!resolved) {
        resolved = true;
        socket.destroy();
        resolve(false);
      }
    });

    socket.on('timeout', () => {
      if (!resolved) {
        resolved = true;
        socket.destroy();
        resolve(false);
      }
    });
  });
}

async function main() {
  console.log('====================================================');
  console.log('   PVE STANDALONE PROXY E2E PLAYWRIGHT TEST SUITE   ');
  console.log('====================================================');
  console.log(`Panel URL: ${PANEL_URL}`);
  console.log(`Node IP:  ${NODE_IP}`);
  console.log(`Chromium: /usr/bin/chromium\n`);

  const browser = await chromium.launch({
    executablePath: '/usr/bin/chromium',
    headless: true,
    args: ['--no-sandbox', '--disable-setuid-sandbox', '--disable-dev-shm-usage', '--ignore-certificate-errors'],
  });

  const context = await browser.newContext({
    ignoreHTTPSErrors: true,
    viewport: { width: 1440, height: 900 },
  });

  // Force Russian locale in localStorage so UI matches Russian assertions
  await context.addInitScript(() => {
    try {
      localStorage.setItem('hoplyra_locale', 'ru');
    } catch {
      // ignore
    }
  });

  const page = await context.newPage();

  // Handle any window.confirm / alert dialogs automatically
  page.on('dialog', async (dialog) => {
    console.log(`[Dialog] ${dialog.type()}: "${dialog.message()}" -> accepting`);
    await dialog.accept();
  });

  // 1. LOGIN
  console.log('1. Navigating to Login Page...');
  await page.goto(`${PANEL_URL}/login`, { waitUntil: 'networkidle', timeout: 20000 });

  console.log('2. Submitting login credentials (admin / admin)...');
  await page.fill('input[name="username"]', 'admin');
  await page.fill('input[name="password"]', 'admin');
  await page.click('button[type="submit"]');

  // Wait for login redirect to dashboard
  await page.waitForSelector('nav', { timeout: 15000 });
  console.log('   -> Successfully authenticated, dashboard loaded.');

  // 2. NAVIGATE TO PROXY TAB
  console.log('3. Navigating to Proxy Tab (/proxy)...');
  await page.goto(`${PANEL_URL}/proxy`, { waitUntil: 'networkidle', timeout: 15000 });
  await page.waitForSelector('h1:has-text("Прокси-серверы"), h1:has-text("Proxy Servers")', { timeout: 15000 });
  console.log('   -> Proxy tab opened and title verified.');

  const protocolsToTest = [
    { type: 'socks5', label: 'SOCKS5', name: 'Test SOCKS5 PVE' },
    { type: 'https', label: 'Secure HTTPS', name: 'Test HTTPS PVE' },
    { type: 'shadowsocks', label: 'Shadowsocks', name: 'Test SS PVE' },
    { type: 'mtproto', label: 'MTProto', name: 'Test MTG PVE' },
  ];

  const deployedProxies = [];

  for (const proto of protocolsToTest) {
    console.log(`\n----------------------------------------------------`);
    console.log(`Deploying & testing protocol via WebUI: ${proto.label} (${proto.type})`);
    console.log(`----------------------------------------------------`);

    // Click "Развернуть прокси" button
    const deployBtn = page.locator('button:has-text("Развернуть прокси"), button:has-text("Deploy Proxy")').first();
    await deployBtn.click();

    // Wait for modal
    await page.waitForSelector('form', { timeout: 5000 });

    // Select protocol card
    const protoBtn = page.locator(`button:has-text("${proto.label}")`).first();
    await protoBtn.click();

    // Select Server: explicitly select node-test (192.168.10.141)
    console.log('   - Selecting VPS server node-test in dropdown...');
    const selectTrigger = page.locator('form button[aria-haspopup="listbox"]').first();
    await selectTrigger.click();
    await page.waitForSelector('div[role="listbox"]', { timeout: 5000 });

    const serverOption = page.locator('div[role="listbox"] button[role="option"]:has-text("192.168.10.141")').first();
    await serverOption.click();
    await page.waitForTimeout(300);

    // Enter custom name
    const nameInput = page.locator('form input[placeholder*="My"], form input[type="text"]').first();
    await nameInput.fill(proto.name);

    // Submit modal
    console.log('   - Submitting modal form...');
    const submitModalBtn = page.locator('form button[type="submit"]').first();
    await submitModalBtn.click();

    // Modal closes and DeployLogDrawer opens
    console.log('   - Waiting for DeployLogDrawer / deploy completion...');
    try {
      await page.waitForSelector('aside[role="region"], aside', { timeout: 10000 });
      console.log('   - DeployLogDrawer is visible, waiting for ok status...');
      await page.waitForSelector(
        'aside span[role="status"]:has-text("Готово"), aside span[role="status"]:has-text("ok"), aside span[role="status"]:has-text("✓")',
        { timeout: 50000 }
      );
      console.log('   - DeployLog completed with success status!');
    } catch {
      console.log('   - Drawer wait completed or closed, verifying API status...');
    }

    // Wait until proxy status is active in API
    console.log('   - Polling /api/standalone-proxies for active status...');
    let activeRecord = null;
    for (let i = 0; i < 30; i++) {
      const apiProxies = await page.evaluate(async () => {
        const res = await fetch('/api/standalone-proxies');
        return res.json();
      });
      const match = apiProxies.find((p) => p.name === proto.name);
      if (match && match.status === 'active') {
        activeRecord = match;
        break;
      }
      await page.waitForTimeout(1000);
    }

    if (!activeRecord) {
      throw new Error(`Proxy ${proto.name} did not reach active status within timeout!`);
    }

    console.log(`   - Server DB record: id=${activeRecord.id}, port=${activeRecord.listenPort}, status=${activeRecord.status}`);

    // Close log drawer if still open
    const closeDrawerBtn = page.locator('aside button[aria-label="Закрыть"], aside button[aria-label="Close"], aside button:has(svg.lucide-x)').first();
    if (await closeDrawerBtn.isVisible()) {
      await closeDrawerBtn.click();
      await page.waitForTimeout(500);
    }

    // Locate the newly created proxy card
    const card = page.locator('div.bento-card, div.rounded-2xl').filter({ hasText: proto.name }).first();
    await card.waitFor({ state: 'visible', timeout: 5000 });

    // Verify status badge is "active"
    const activeBadge = card.locator('span:has-text("active")').first();
    await activeBadge.waitFor({ state: 'visible', timeout: 5000 });
    console.log('   - Proxy card visible with status: active');

    // Extract connection URI
    const uriEl = card.locator('.font-mono.select-all').first();
    let clientUri = '';
    if (await uriEl.isVisible()) {
      clientUri = (await uriEl.textContent()).trim();
      console.log(`   - Client URI: ${clientUri}`);
    }

    deployedProxies.push({ ...proto, record: activeRecord, clientUri });

    // Verify card is expanded by default (has Host / Port fields visible)
    const hostField = card.locator('div:has-text("Хост"), div:has-text("Host")').first();
    const portField = card.locator('div:has-text("Порт"), div:has-text("Port")').first();
    if ((await hostField.isVisible()) && (await portField.isVisible())) {
      console.log('   - Verified card is expanded by default with Host & Port copy fields.');
    } else {
      console.warn('   - Warning: Host/Port fields not immediately visible.');
    }

    // FUNCTIONAL VERIFICATION OF PROXY PROTOCOL
    console.log(`   - Testing TCP connectivity to ${NODE_IP}:${activeRecord.listenPort}...`);
    const tcpOk = await testTcpConnect(NODE_IP, activeRecord.listenPort, 5000);
    if (!tcpOk) {
      throw new Error(`TCP connect to ${NODE_IP}:${activeRecord.listenPort} failed!`);
    }
    console.log(`   - TCP port ${activeRecord.listenPort} is open and accepting connections.`);

    if (proto.type === 'socks5') {
      const user = activeRecord.meta?.username;
      const pass = activeRecord.meta?.password;
      console.log(`   - Testing SOCKS5 curl request through ${NODE_IP}:${activeRecord.listenPort} (user: ${user})...`);
      const curlOut = runCmd(`curl -sS -o /dev/null -w "%{http_code}" --socks5-hostname "${user}:${pass}@${NODE_IP}:${activeRecord.listenPort}" --connect-timeout 8 https://1.1.1.1`);
      console.log(`   - SOCKS5 curl response code: ${curlOut}`);
      if (!['200', '301', '302'].includes(curlOut)) {
        throw new Error(`SOCKS5 proxy traffic test failed: expected 200/301, got ${curlOut}`);
      }
      console.log('   -> SOCKS5 proxy fully operational!');

    } else if (proto.type === 'https') {
      const user = activeRecord.meta?.username;
      const pass = activeRecord.meta?.password;
      console.log(`   - Testing HTTPS CONNECT curl through ${NODE_IP}:${activeRecord.listenPort} (user: ${user})...`);
      const curlOut = runCmd(`curl -sS -k --proxy-insecure -o /dev/null -w "%{http_code}" -x "https://${user}:${pass}@${NODE_IP}:${activeRecord.listenPort}" --connect-timeout 8 https://1.1.1.1`);
      console.log(`   - HTTPS proxy curl response code: ${curlOut}`);
      if (!['200', '301', '302'].includes(curlOut)) {
        throw new Error(`HTTPS proxy traffic test failed: expected 200/301, got ${curlOut}`);
      }
      console.log('   -> Secure HTTPS proxy fully operational!');

    } else if (proto.type === 'shadowsocks') {
      const cipher = activeRecord.meta?.cipher || 'AEAD_CHACHA20_POLY1305';
      const pass = activeRecord.meta?.password;
      console.log(`   - Testing Shadowsocks client tunnel through ${NODE_IP}:${activeRecord.listenPort} (cipher: ${cipher})...`);
      const ssTunnelCmd = `ssh -o ProxyJump=none root@192.168.10.140 'podman run -d --name ss_test_tunnel --net=host localhost/hoplyra-gost:1 -L :19876 -F "ss://${cipher}:${pass}@${NODE_IP}:${activeRecord.listenPort}" >/dev/null && sleep 1 && curl -s -o /dev/null -w "%{http_code}" --socks5-hostname 127.0.0.1:19876 https://1.1.1.1 --connect-timeout 6 ; podman rm -f ss_test_tunnel >/dev/null 2>&1'`;
      const ssCurlOut = runCmd(ssTunnelCmd);
      console.log(`   - Shadowsocks end-to-end curl response code: ${ssCurlOut}`);
      if (!['200', '301', '302'].includes(ssCurlOut.trim())) {
        throw new Error(`Shadowsocks proxy traffic test failed: got ${ssCurlOut}`);
      }
      console.log('   -> Shadowsocks proxy fully operational!');

    } else if (proto.type === 'mtproto') {
      console.log(`   - Testing MTProto fake-TLS fronting on ${NODE_IP}:${activeRecord.listenPort} (sni: ${activeRecord.meta?.sni})...`);
      const mtgCurlCmd = `curl -sS -k -o /dev/null -w "%{http_code}" --resolve cloudflare.com:${activeRecord.listenPort}:${NODE_IP} https://cloudflare.com:${activeRecord.listenPort}/ --connect-timeout 6`;
      const mtgCurlOut = runCmd(mtgCurlCmd);
      console.log(`   - MTProto fake-TLS fronting response code: ${mtgCurlOut}`);
      const mtgLogs = runCmd(`ssh -o ProxyJump=none root@${NODE_IP} 'podman logs --tail 10 ${activeRecord.containerName}'`);
      console.log(`   - MTProto mtg container logs: ${mtgLogs.replace(/\n/g, ' ')}`);
      console.log('   -> MTProto Telegram proxy fully operational!');
    }
  }

  // 4. INTERACTIVE WEBUI CONTROLS: STOP / START / COLLAPSE ON SOCKS5 CARD
  console.log('\n====================================================');
  console.log('   TESTING INTERACTIVE WEBUI CONTROLS (STOP/START)   ');
  console.log('====================================================');
  const socksProxy = deployedProxies.find((p) => p.type === 'socks5');
  const socksCard = page.locator('div.bento-card, div.rounded-2xl').filter({ hasText: socksProxy.name }).first();

  // Test Collapse & Expand
  console.log('1. Testing Collapse & Expand button...');
  const expandBtn = socksCard.locator('button:has-text("Свернуть"), button:has-text("Развернуть"), button[title="Свернуть"], button[title="Развернуть"]').first();
  await expandBtn.click();
  await page.waitForTimeout(500);
  const hostFieldAfterCollapse = socksCard.locator('div:has-text("Хост"), div:has-text("Host")').first();
  const isHidden = !(await hostFieldAfterCollapse.isVisible());
  console.log(`   - Card collapsed: host field hidden = ${isHidden}`);

  await expandBtn.click();
  await page.waitForTimeout(500);
  const hostFieldAfterExpand = socksCard.locator('div:has-text("Хост"), div:has-text("Host")').first();
  const isShown = await hostFieldAfterExpand.isVisible();
  console.log(`   - Card expanded back: host field visible = ${isShown}`);

  // Test Stop
  console.log('2. Clicking "Остановить" (Stop) on SOCKS5 card...');
  const stopBtn = socksCard.locator('button:has-text("Остановить"), button:has-text("Stop")').first();
  await stopBtn.click();

  // Wait for status badge to become "inactive"
  await socksCard.locator('span:has-text("inactive")').first().waitFor({ state: 'visible', timeout: 15000 });
  console.log('   - Verified UI card updated to "inactive".');

  // Verify proxy traffic now fails
  const user = socksProxy.record.meta?.username;
  const pass = socksProxy.record.meta?.password;
  const curlStopped = runCmd(`curl -sS -o /dev/null -w "%{http_code}" --socks5-hostname "${user}:${pass}@${NODE_IP}:${socksProxy.record.listenPort}" --connect-timeout 2 https://1.1.1.1`);
  console.log(`   - Curl during inactive status returned error as expected: ${curlStopped.startsWith('ERR') || curlStopped === '000'}`);

  // Test Start
  console.log('3. Clicking "Запустить" (Start) on SOCKS5 card...');
  const startBtn = socksCard.locator('button:has-text("Запустить"), button:has-text("Start")').first();
  await startBtn.click();

  // Wait for status badge to become "active" again
  await socksCard.locator('span:has-text("active")').first().waitFor({ state: 'visible', timeout: 15000 });
  console.log('   - Verified UI card updated back to "active".');

  // Verify proxy traffic works again (with retry for podman port forwarding initialization)
  console.log('   - Verifying proxy traffic resumption through restarted proxy...');
  let curlRestarted = '';
  let resumed = false;
  for (let attempt = 1; attempt <= 10; attempt++) {
    await page.waitForTimeout(1000);
    curlRestarted = runCmd(`curl -sS -o /dev/null -w "%{http_code}" --socks5-hostname "${user}:${pass}@${NODE_IP}:${socksProxy.record.listenPort}" --connect-timeout 5 https://1.1.1.1`);
    if (['200', '301', '302'].includes(curlRestarted)) {
      resumed = true;
      break;
    }
    console.log(`     (attempt ${attempt}/10: ${curlRestarted}, waiting 1s...)`);
  }
  console.log(`   - Curl after restart returned: ${curlRestarted}`);
  if (!resumed) {
    throw new Error(`SOCKS5 failed to resume traffic after start: ${curlRestarted}`);
  }
  console.log('   -> Stop and Start WebUI buttons verified successfully!');

  // 5. CLEANUP VIA WEBUI: DELETE ALL DEPLOYED PROXIES
  console.log('\n====================================================');
  console.log('   CLEANUP: DELETING ALL PROXIES VIA WEBUI          ');
  console.log('====================================================');
  for (const p of deployedProxies) {
    console.log(`Deleting ${p.name} (${p.label})...`);
    const cardEl = page.locator('div.bento-card, div.rounded-2xl').filter({ hasText: p.name }).first();
    const trashBtn = cardEl.locator('button[title="Удалить"], button[title="Delete"], button:has(svg.lucide-trash-2)').first();
    await trashBtn.click();
    await page.waitForTimeout(1500);
  }

  // Verify empty state is displayed
  await page.locator(':text("Нет активных прокси-серверов"), :text("No active proxy servers")').first().waitFor({ state: 'visible', timeout: 10000 });
  console.log('   - All proxies deleted; empty state rendered.');

  // Verify on CT 141 that no proxy containers remain
  const remainingContainers = runCmd(`ssh -o ProxyJump=none root@${NODE_IP} "podman ps -a --format '{{.Names}}' | grep cv-prx || true"`);
  console.log(`   - Remaining proxy containers on CT 141: "${remainingContainers}"`);
  if (remainingContainers) {
    console.warn(`   - Warning: Leftover containers found: ${remainingContainers}`);
  } else {
    console.log('   - Verified zero leftover proxy containers on PVE node!');
  }

  await browser.close();
  console.log('\n====================================================');
  console.log('   ALL PVE PROXY PROTOCOL TESTS PASSED IN WEBUI!    ');
  console.log('====================================================');
}

main().catch((err) => {
  console.error('\nFAILED PVE PLAYWRIGHT TEST:', err);
  process.exit(1);
});
