import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const __dirname = path.dirname(fileURLToPath(import.meta.url))
const desktopDir = path.resolve(__dirname, '..')
const backendDir = path.resolve(desktopDir, '..')
const frontendDist = path.resolve(backendDir, '..', 'frontend', 'dist')
const uiDist = path.join(backendDir, 'ui', 'dist')

if (!fs.existsSync(frontendDist)) {
  console.error(`sync-ui-dist: missing frontend build at ${frontendDist}`)
  process.exit(1)
}

fs.rmSync(uiDist, { recursive: true, force: true })
fs.mkdirSync(path.dirname(uiDist), { recursive: true })
fs.cpSync(frontendDist, uiDist, { recursive: true })
console.log(`sync-ui-dist: ${frontendDist} -> ${uiDist}`)
