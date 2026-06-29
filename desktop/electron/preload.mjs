import { contextBridge, ipcRenderer } from 'electron'

contextBridge.exposeInMainWorld('hoplyraDesktop', {
  isDesktop: true,
  quit: () => ipcRenderer.send('hoplyra-quit'),
})
