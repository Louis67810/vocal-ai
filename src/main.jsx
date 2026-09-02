import React, { useEffect, useState } from 'react'
import { createRoot } from 'react-dom/client'
import { CheckIcon, EllipsisHorizontalIcon, ExclamationTriangleIcon, InformationCircleIcon, MicrophoneIcon, SpeakerWaveIcon } from '@heroicons/react/24/solid'
import './styles.css'

const officeAsset = { word: './0877fc4cdb9ff70b4647ad05d5aba6684812b1f4.png', onenote: './af2a6280cc6e6d04267283dd9a5d00d2fad440fc.png' }

function StatusIcon({ notice }) {
  if (notice.app) return <img src={officeAsset[notice.app]} />
  if (notice.kind === 'success') return <CheckIcon />
  if (notice.kind === 'recording') return <MicrophoneIcon />
  if (notice.kind === 'error' && notice.details?.code === 48) return <SpeakerWaveIcon />
  if (notice.kind === 'error') return <ExclamationTriangleIcon />
  return <EllipsisHorizontalIcon />
}

function ErrorOverlay({ details }) {
  if (!details) return null
  return <section className="error-overlay" role="status" aria-label={`Détails de l’erreur ${details.code}`} onPointerDown={event => event.stopPropagation()}>
    <header className="error-overlay-title"><strong>{details.title}</strong><span>Erreur #{details.code}</span></header>
    <div className="error-overlay-divider" />
    <p><strong>{details.message}</strong><span> {details.help}</span></p>
    {details.technical && <small>Référence technique : {details.technical}</small>}
  </section>
}

function Toast({ notice, leaving, isInfoOpen, setInfoOpen }) {
  const isError = notice.kind === 'error'
  return <>
    {isError && isInfoOpen && <ErrorOverlay details={notice.details} />}
    <div className={`toast ${notice.kind} ${leaving ? 'leaving' : ''}`} onPointerDown={event => event.stopPropagation()}>
      <span className="toast-icon"><StatusIcon notice={notice} /></span>
      <span className={notice.kind === 'work' ? 'shimmer' : ''}>{notice.text}</span>
      {isError && <button className="toast-info" type="button" aria-label={`Voir l’aide pour l’erreur ${notice.details?.code ?? ''}`} aria-expanded={isInfoOpen} onClick={() => setInfoOpen(open => !open)}><InformationCircleIcon /></button>}
    </div>
  </>
}

function App() {
  const isToast = new URLSearchParams(window.location.search).get('surface') === 'toast'
  const [recording, setRecording] = useState(false), [shortcut, setShortcut] = useState('Ctrl+Alt+Espace'), [notice, setNotice] = useState(null), [displayNotice, setDisplayNotice] = useState(null), [leaving, setLeaving] = useState(false), [logs, setLogs] = useState([]), [isInfoOpen, setInfoOpen] = useState(false)
  document.body.classList.toggle('toast-body', isToast)
  useEffect(() => { const refresh = async () => { try { const state = await fetch('http://127.0.0.1:8765/state').then(r => r.json()); setRecording(state.recording); if (document.activeElement?.id !== 'shortcut') setShortcut(state.shortcut); setNotice(state.notice); setLogs(state.logs) } catch {} }; refresh(); const timer = setInterval(refresh, 120); return () => clearInterval(timer) }, [])
  useEffect(() => { if (notice) { setDisplayNotice(notice); setLeaving(false); return } if (!displayNotice) return; setLeaving(true); const timer = setTimeout(() => { setDisplayNotice(null); setLeaving(false) }, 240); return () => clearTimeout(timer) }, [notice])
  useEffect(() => setInfoOpen(false), [notice?.details?.code, notice?.text])
  const toggleRecording = () => fetch('http://127.0.0.1:8765/toggle', { method: 'POST', body: '{}' })
  if (isToast) return <main className={`toast-surface ${isInfoOpen ? 'is-help-open' : ''}`} onPointerDown={() => setInfoOpen(false)}>{displayNotice && <Toast notice={displayNotice} leaving={leaving} isInfoOpen={isInfoOpen} setInfoOpen={setInfoOpen} />}</main>
  return <main className="app-shell"><section className="control-card"><header><p className="eyebrow">VOICE NOTES</p><h1>Commandes et journal</h1><p>Le bouton reste disponible même si le raccourci clavier ne répond pas.</p></header><button className={`record-button ${recording ? 'is-recording' : ''}`} onClick={toggleRecording}><MicrophoneIcon /> {recording ? 'Arrêter l’enregistrement' : 'Démarrer l’enregistrement'}</button><div className="shortcut"><label htmlFor="shortcut">Raccourci global</label><div><input id="shortcut" value={shortcut} onChange={e => setShortcut(e.target.value)} /><button onClick={() => fetch('http://127.0.0.1:8765/hotkey', { method: 'POST', body: JSON.stringify({ value: shortcut }) })}>Appliquer</button></div><small>Exemple : Ctrl+Shift+R</small></div><div className="logs" aria-live="polite">{logs.map((line, i) => <p key={i}>{line}</p>)}</div></section></main>
}

createRoot(document.getElementById('root')).render(<App />)
