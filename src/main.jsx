import React, { useEffect, useState } from 'react'
import { createRoot } from 'react-dom/client'
import './styles.css'

const icons = {
  mic: 'M12 2a3 3 0 0 0-3 3v5a3 3 0 0 0 6 0V5a3 3 0 0 0-3-3Zm-5 8a5 5 0 0 0 10 0h-2a3 3 0 0 1-6 0H7Zm4 7.9V21h2v-3.1A5.01 5.01 0 0 0 17 13h-2a3 3 0 0 1-6 0H7a5.01 5.01 0 0 0 4 4.9Z',
  check: 'm10 15.2-3.5-3.5-1.4 1.4 4.9 4.9L19 9.1l-1.4-1.4-7.6 7.5Z',
}

function Icon({ name, className = '' }) {
  return <svg className={className} viewBox="0 0 24 24" aria-hidden="true"><path d={icons[name]} /></svg>
}

function App() {
  const [recording, setRecording] = useState(false)
  const [shortcut, setShortcut] = useState('Ctrl+Alt+Espace')
  const [notice, setNotice] = useState(null)
  const [logs, setLogs] = useState([])

  useEffect(() => {
    const refresh = async () => { try { const state = await fetch('http://127.0.0.1:8765/state').then((r) => r.json()); setRecording(state.recording); if (document.activeElement?.id !== 'shortcut') setShortcut(state.shortcut); setNotice(state.notice); setLogs(state.logs) } catch {} }
    refresh(); const timer = setInterval(refresh, 180); return () => clearInterval(timer)
  }, [])

  function toggleRecording() {
    fetch('http://127.0.0.1:8765/toggle', { method: 'POST', body: '{}' })
  }

  return <main className="app-shell">
    <section className="control-card">
      <header><p className="eyebrow">VOICE NOTES</p><h1>Commandes et journal</h1><p>Le bouton reste disponible même si le raccourci clavier ne répond pas.</p></header>
      <button className={`record-button ${recording ? 'is-recording' : ''}`} onClick={toggleRecording}>
        <Icon name="mic" /> {recording ? 'Arrêter l’enregistrement' : 'Démarrer l’enregistrement'}
      </button>
      <div className="shortcut"><label htmlFor="shortcut">Raccourci global</label><div><input id="shortcut" value={shortcut} onChange={(e) => setShortcut(e.target.value)} /><button onClick={() => fetch('http://127.0.0.1:8765/hotkey', { method: 'POST', body: JSON.stringify({ value: shortcut }) })}>Appliquer</button></div><small>Exemple : Ctrl+Shift+R</small></div>
      <div className="logs" aria-live="polite">{logs.map((line, i) => <p key={i}>{line}</p>)}</div>
    </section>
    {notice && <div className={`toast ${notice.kind}`}><span className="toast-icon">{notice.app ? <img src={`/${notice.app === 'word' ? '0877fc4cdb9ff70b4647ad05d5aba6684812b1f4.png' : 'af2a6280cc6e6d04267283dd9a5d00d2fad440fc.png'}`} /> : <Icon name={notice.kind === 'success' ? 'check' : 'mic'} />}</span><span className={notice.kind === 'work' ? 'shimmer' : ''}>{notice.text}</span></div>}
  </main>
}

createRoot(document.getElementById('root')).render(<App />)
