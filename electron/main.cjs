const { app, BrowserWindow } = require('electron')
const path = require('path')
const { spawn } = require('child_process')

let controlWindow
let toastWindow
let contextWindow
let answerWindow
let backend
app.setPath('userData', path.join(__dirname, '..', '.electron-data'))

function createWindow() {
  const root = path.join(__dirname, '..')
  backend = spawn(path.join(root, '.venv', 'Scripts', 'pythonw.exe'), ['app.py'], { cwd: root, env: { ...process.env, VOICE_NOTES_WEB: '1' }, windowsHide: true })
  controlWindow = new BrowserWindow({
    width: 720,
    height: 560,
    minWidth: 560,
    minHeight: 420,
    backgroundColor: '#191919',
    title: 'Voice Notes',
    webPreferences: { contextIsolation: true, nodeIntegration: false },
  })
  controlWindow.loadFile(path.join(__dirname, '..', 'dist', 'index.html'))
  toastWindow = new BrowserWindow({
    width: 373, height: 200, frame: false, transparent: true, backgroundColor: '#00000000', resizable: false,
    alwaysOnTop: true, skipTaskbar: true, focusable: false,
    webPreferences: { contextIsolation: true, nodeIntegration: false },
  })
  const workArea = require('electron').screen.getPrimaryDisplay().workArea
  toastWindow.setPosition(Math.round(workArea.x + (workArea.width - 373) / 2), workArea.y + workArea.height - 199)
  toastWindow.loadFile(path.join(__dirname, '..', 'dist', 'toast.html'), { query: { surface: 'toast' } })
  contextWindow = new BrowserWindow({
    width: 615, height: 729, frame: false, transparent: true, backgroundColor: '#00000000', resizable: false,
    alwaysOnTop: true, show: false, skipTaskbar: true,
    webPreferences: { contextIsolation: true, nodeIntegration: false },
  })
  contextWindow.loadFile(path.join(__dirname, '..', 'dist', 'index.html'), { query: { surface: 'context' } })
  answerWindow = new BrowserWindow({
    width: 756, height: 554, frame: false, transparent: true, backgroundColor: '#00000000', resizable: false,
    alwaysOnTop: true, show: false, skipTaskbar: true,
    webPreferences: { contextIsolation: true, nodeIntegration: false },
  })
  answerWindow.loadFile(path.join(__dirname, '..', 'dist', 'index.html'), { query: { surface: 'answer' } })
  syncPanels()
}

async function syncPanels() {
  try {
    const state = await fetch('http://127.0.0.1:8765/state').then(response => response.json())
    setSurfaceVisible(contextWindow, Boolean(state.panels?.context))
    setSurfaceVisible(answerWindow, state.question?.status === 'answer')
  } catch {}
  setTimeout(syncPanels, 180)
}

function setSurfaceVisible(window, visible) {
  if (!window || window.isDestroyed()) return
  if (visible && !window.isVisible()) { window.center(); window.show(); window.focus() }
  if (!visible && window.isVisible()) window.hide()
}

app.whenReady().then(createWindow)
app.on('window-all-closed', () => app.quit())
app.on('before-quit', () => backend?.kill())
