import { app, BrowserWindow, dialog, ipcMain, shell } from 'electron'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { spawn } from 'node:child_process'
import {
  appUrlForPort,
  attachBackendLogging,
  BACKEND_PORT_DEFAULT,
  buildBackendEnv,
  pickBackendPort,
  resolveBackendLaunch,
  resolveDataDir,
  stopBackend,
  waitForBackend,
} from './backend-launcher.mjs'

const __dirname = path.dirname(fileURLToPath(import.meta.url))

const SPLASH_WIDTH = 520
const SPLASH_HEIGHT = 360
const SPLASH_FADE_MS = 550
const SPLASH_MIN_MS = 2800

let backendProcess = null
let mainWindow = null
let splashWindow = null
let appUrl = appUrlForPort(BACKEND_PORT_DEFAULT)
let splashShownAt = 0
let isQuitting = false

function preloadPath() {
  return path.join(__dirname, 'preload.mjs')
}

function isGracefulBackendExit(code, signal) {
  if (isQuitting) return true
  if (code === 0 || code === null) return true
  if (signal === 'SIGTERM' || signal === 'SIGKILL') return true
  if (code === 143 || code === 137) return true
  return false
}

function requestQuit() {
  if (isQuitting) return
  isQuitting = true
  stopBackend(backendProcess)

  for (const win of BrowserWindow.getAllWindows()) {
    if (!win.isDestroyed()) {
      win.destroy()
    }
  }

  app.exit(0)
}

function splashHtmlPath() {
  return path.join(__dirname, 'splash.html')
}

function createSplashWindow() {
  splashWindow = new BrowserWindow({
    width: SPLASH_WIDTH,
    height: SPLASH_HEIGHT,
    frame: false,
    transparent: true,
    resizable: false,
    movable: true,
    center: true,
    alwaysOnTop: true,
    show: false,
    backgroundColor: '#00000000',
    webPreferences: {
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
      preload: preloadPath(),
    },
  })

  splashWindow.loadFile(splashHtmlPath())
  splashWindow.once('ready-to-show', () => {
    splashShownAt = Date.now()
    splashWindow?.show()
  })
}

function createMainWindow() {
  return new Promise((resolve, reject) => {
    mainWindow = new BrowserWindow({
      width: 1320,
      height: 860,
      minWidth: 960,
      minHeight: 640,
      title: 'Hoplyra',
      autoHideMenuBar: true,
      show: false,
      backgroundColor: '#09091a',
      webPreferences: {
        contextIsolation: true,
        nodeIntegration: false,
        sandbox: true,
        preload: preloadPath(),
      },
    })

    mainWindow.webContents.setWindowOpenHandler(({ url }) => {
      if (url.startsWith('http://127.0.0.1:') || url.startsWith('http://localhost:')) {
        return { action: 'allow' }
      }
      shell.openExternal(url)
      return { action: 'deny' }
    })

    mainWindow.once('ready-to-show', () => {
      resolve(undefined)
    })

    mainWindow.webContents.on('did-fail-load', (_event, code, _description, _url, isMainFrame) => {
      if (!isMainFrame || isQuitting) return
      if (code === -3) return
      reject(new Error(`Dashboard failed to load (${code})`))
    })

    mainWindow.on('closed', () => {
      mainWindow = null
      if (!isQuitting) {
        requestQuit()
      }
    })

    mainWindow.loadURL(appUrl)
  })
}

async function closeSplashWindow() {
  const splash = splashWindow
  if (!splash || splash.isDestroyed()) {
    return
  }

  if (!isQuitting) {
    try {
      await splash.webContents.executeJavaScript(
        'document.body.classList.add("fade-out")',
        true,
      )
    } catch {
      void 0
    }
    await new Promise((resolve) => setTimeout(resolve, SPLASH_FADE_MS))
  }

  if (!splash.isDestroyed()) {
    splash.destroy()
  }
  if (splashWindow === splash) {
    splashWindow = null
  }
}

async function waitForMinSplash() {
  if (isQuitting) return
  const elapsed = Date.now() - splashShownAt
  const remaining = SPLASH_MIN_MS - elapsed
  if (remaining > 0) {
    await new Promise((resolve) => setTimeout(resolve, remaining))
  }
}

async function revealMainWindow() {
  if (isQuitting) return
  await waitForMinSplash()
  if (isQuitting) return
  await createMainWindow()
  if (isQuitting) return
  await closeSplashWindow()
  mainWindow?.show()
  mainWindow?.focus()
}

async function startBackend() {
  const packaged = app.isPackaged
  const dataDir = resolveDataDir(app.getPath('userData'))
  const port = await pickBackendPort()
  if (port !== BACKEND_PORT_DEFAULT) {
    console.log(`[hoplyra] Port ${BACKEND_PORT_DEFAULT} is busy, using ${port}`)
  }
  appUrl = appUrlForPort(port)
  const env = buildBackendEnv(packaged, dataDir, port)
  const { command, args, cwd } = resolveBackendLaunch(packaged)

  backendProcess = spawn(command, args, {
    cwd,
    env,
    stdio: ['ignore', 'pipe', 'pipe'],
  })

  attachBackendLogging(backendProcess)

  backendProcess.on('exit', (code, signal) => {
    if (isGracefulBackendExit(code, signal)) return
    dialog.showErrorBox(
      'Hoplyra backend stopped',
      `The local API exited unexpectedly (code ${code ?? 'null'}, signal ${signal ?? 'null'}).`,
    )
    requestQuit()
  })

  await waitForBackend(port)
}

async function handleStartupFailure(err) {
  if (isQuitting) {
    requestQuit()
    return
  }
  await closeSplashWindow()
  const message = err instanceof Error ? err.message : String(err)
  dialog.showErrorBox('Hoplyra failed to start', message)
  requestQuit()
}

ipcMain.on('hoplyra-quit', () => {
  requestQuit()
})

const gotLock = app.requestSingleInstanceLock()
if (!gotLock) {
  app.quit()
} else {
  if (app.isPackaged) {
    app.commandLine.appendSwitch('disable-logging')
    app.commandLine.appendSwitch('log-level', '3')
  }

  app.on('second-instance', () => {
    if (mainWindow) {
      if (mainWindow.isMinimized()) mainWindow.restore()
      mainWindow.focus()
    }
  })

  app.whenReady().then(async () => {
    createSplashWindow()
    try {
      await startBackend()
      await revealMainWindow()
    } catch (err) {
      await handleStartupFailure(err)
    }
  })

  app.on('before-quit', () => {
    isQuitting = true
    stopBackend(backendProcess)
  })

  app.on('window-all-closed', () => {
    requestQuit()
  })
}
