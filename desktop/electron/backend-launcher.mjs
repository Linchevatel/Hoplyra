import crypto from 'node:crypto'
import fs from 'node:fs'
import http from 'node:http'
import net from 'node:net'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const __dirname = path.dirname(fileURLToPath(import.meta.url))

export const BACKEND_HOST = '127.0.0.1'
export const BACKEND_PORT_DEFAULT = 8787
export const BACKEND_PORT_MAX = 8797

export function resolveDataDir(userDataPath) {
  return path.join(userDataPath, 'hoplyra-data')
}

export function appUrlForPort(port) {
  return `http://${BACKEND_HOST}:${port}/`
}

export function resolveBackendRoot(packaged) {
  if (packaged) {
    return path.join(process.resourcesPath, 'backend')
  }
  return path.resolve(__dirname, '../..')
}

function backendBinaryName() {
  return process.platform === 'win32' ? 'hoplyra-backend.exe' : 'hoplyra-backend'
}

function devPythonPath(backendRoot) {
  const candidates =
    process.platform === 'win32'
      ? [
          path.join(backendRoot, '.venv', 'Scripts', 'python.exe'),
          path.join(backendRoot, '.venv', 'Scripts', 'python'),
        ]
      : [path.join(backendRoot, '.venv', 'bin', 'python')]
  for (const candidate of candidates) {
    if (fs.existsSync(candidate)) return candidate
  }
  return process.platform === 'win32' ? 'python' : 'python3'
}

export function resolveBackendLaunch(packaged) {
  const backendRoot = resolveBackendRoot(packaged)
  if (packaged) {
    const binary = path.join(backendRoot, backendBinaryName())
    return { command: binary, args: [], cwd: backendRoot }
  }

  const python = devPythonPath(backendRoot)
  return {
    command: python,
    args: [path.join(backendRoot, 'run.py')],
    cwd: backendRoot,
  }
}

function generateFernetKey() {
  return crypto.randomBytes(32).toString('base64url')
}

export function ensureDesktopEnvFile(dataDir) {
  fs.mkdirSync(dataDir, { recursive: true })
  const envPath = path.join(dataDir, '.env')
  const existing = fs.existsSync(envPath) ? loadEnvFile(dataDir) : {}
  const next = { ...existing }
  let changed = !fs.existsSync(envPath)

  if (!next.HOPLYRA_SESSION_SECRET) {
    next.HOPLYRA_SESSION_SECRET = crypto.randomBytes(32).toString('hex')
    changed = true
  }
  if (!next.HOPLYRA_SECRET_KEY) {
    next.HOPLYRA_SECRET_KEY = generateFernetKey()
    changed = true
  }
  if (!next.HOPLYRA_ADMIN_USER) {
    next.HOPLYRA_ADMIN_USER = 'admin'
    changed = true
  }
  if (!next.HOPLYRA_ADMIN_PASSWORD) {
    next.HOPLYRA_ADMIN_PASSWORD = 'admin'
    changed = true
  }

  if (!changed) {
    return
  }

  const lines = [
    `HOPLYRA_SESSION_SECRET=${next.HOPLYRA_SESSION_SECRET}`,
    `HOPLYRA_SECRET_KEY=${next.HOPLYRA_SECRET_KEY}`,
    `HOPLYRA_ADMIN_USER=${next.HOPLYRA_ADMIN_USER}`,
    `HOPLYRA_ADMIN_PASSWORD=${next.HOPLYRA_ADMIN_PASSWORD}`,
    '',
  ]
  fs.writeFileSync(envPath, lines.join('\n'), { mode: 0o600 })
}

export function loadEnvFile(dataDir) {
  const envPath = path.join(dataDir, '.env')
  const out = {}
  if (!fs.existsSync(envPath)) {
    return out
  }
  for (const line of fs.readFileSync(envPath, 'utf8').split('\n')) {
    const trimmed = line.trim()
    if (!trimmed || trimmed.startsWith('#') || !trimmed.includes('=')) continue
    const idx = trimmed.indexOf('=')
    const key = trimmed.slice(0, idx).trim()
    const value = trimmed.slice(idx + 1).trim()
    if (key) out[key] = value
  }
  return out
}

export function isPortFree(host, port) {
  return new Promise((resolve) => {
    const server = net.createServer()
    server.once('error', () => resolve(false))
    server.once('listening', () => {
      server.close(() => resolve(true))
    })
    server.listen(port, host)
  })
}

export async function pickBackendPort(
  preferred = BACKEND_PORT_DEFAULT,
  maxPort = BACKEND_PORT_MAX,
) {
  for (let port = preferred; port <= maxPort; port += 1) {
    if (await isPortFree(BACKEND_HOST, port)) {
      return port
    }
  }
  throw new Error(
    `Ports ${preferred}-${maxPort} are busy. Stop another Hoplyra instance (systemctl stop hoplyra) and retry.`,
  )
}

export function buildBackendEnv(packaged, dataDir, port) {
  ensureDesktopEnvFile(dataDir)
  const fileEnv = loadEnvFile(dataDir)
  return {
    ...process.env,
    ...fileEnv,
    HOPLYRA_DESKTOP: '1',
    HOPLYRA_HOST: BACKEND_HOST,
    HOPLYRA_PORT: String(port),
    HOPLYRA_DATA: dataDir,
    HOPLYRA_LOG_LEVEL: process.env.HOPLYRA_LOG_LEVEL || 'WARNING',
  }
}

export function attachBackendLogging(child) {
  const forward = (stream, chunk) => {
    const text = String(chunk)
    for (const line of text.split('\n')) {
      if (!line.trim()) continue
      if (/^\s*INFO:\s/.test(line)) continue
      stream.write(`[hoplyra] ${line}\n`)
    }
  }

  child.stdout?.on('data', (chunk) => forward(process.stdout, chunk))
  child.stderr?.on('data', (chunk) => forward(process.stderr, chunk))
}

export function waitForBackend(port, timeoutMs = 90_000) {
  const started = Date.now()
  const url = `http://${BACKEND_HOST}:${port}/api/health`

  return new Promise((resolve, reject) => {
    const tick = () => {
      const req = http.get(url, (res) => {
        res.resume()
        if (res.statusCode && res.statusCode >= 200 && res.statusCode < 300) {
          resolve(undefined)
          return
        }
        schedule()
      })
      req.on('error', schedule)
      req.setTimeout(2000, () => {
        req.destroy()
        schedule()
      })
    }

    const schedule = () => {
      if (Date.now() - started > timeoutMs) {
        reject(new Error(`Hoplyra backend did not become ready on port ${port}`))
        return
      }
      setTimeout(tick, 400)
    }

    tick()
  })
}

export function stopBackend(child) {
  if (!child || child.killed || child.exitCode !== null) return
  child.kill('SIGTERM')
  setTimeout(() => {
    if (child && child.exitCode === null && !child.killed) {
      child.kill('SIGKILL')
    }
  }, 5000)
}
