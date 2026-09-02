const { app, BrowserWindow } = require('electron')
const path = require('path')
const { spawn } = require('child_process')

let controlWindow
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
}

app.whenReady().then(createWindow)
app.on('window-all-closed', () => app.quit())
app.on('before-quit', () => backend?.kill())
