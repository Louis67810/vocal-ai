"""Crisp HTML/CSS notification surface, rendered by the local Edge WebView."""
from __future__ import annotations

import json
import threading
import webview


HTML = r"""<!doctype html><html><head><meta charset="utf-8"><style>
*{box-sizing:border-box} html,body{width:100%;height:100%;margin:0;background:transparent;overflow:hidden;font-family:Inter,"Segoe UI",sans-serif}
#toast{width:296px;height:46px;border:1px solid rgba(0,0,0,.15);border-radius:46px;background:#fff;display:flex;align-items:center;gap:5px;padding:6px 16px 6px 6px;box-shadow:0 10px 28px rgba(0,0,0,.10);transform:translateY(60px);opacity:0;transition:transform .22s cubic-bezier(.22,1,.36,1),opacity .16s ease}
#toast.visible{transform:translateY(0);opacity:1} .bubble{width:32px;height:32px;border-radius:50%;display:grid;place-items:center;flex:0 0 32px;font-size:18px;font-weight:800}.label{font-size:16px;font-weight:500;letter-spacing:-.03em;line-height:.9;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;flex:1}
.work .bubble{background:#f4f4f4;color:#666}.recording .bubble{background:#e6f4ff;color:#69a2d1}.success .bubble{background:#eaf8ef;color:#318452}.error .bubble{background:rgba(255,0,0,.08);color:#d43333}.error .label{color:#963333}.work .label{background:linear-gradient(90deg,rgba(0,0,0,.69),rgba(84,84,84,.42),rgba(0,0,0,.69));background-size:200% 100%;background-clip:text;-webkit-background-clip:text;color:transparent;-webkit-text-fill-color:transparent;animation:shimmer 1.25s linear infinite}@keyframes shimmer{to{background-position:-200% 0}}
#help{display:none;width:20px;height:20px;border:0;border-radius:50%;background:rgba(117,103,103,.47);color:white;font-weight:800;cursor:pointer}.error #help{display:block}
#overlay{display:none;width:373px;height:275px;background:#191919;border-radius:24px;padding:24px;color:#fff;box-shadow:0 36px 14px rgba(0,0,0,.01),0 20px 12px rgba(0,0,0,.05),0 9px 9px rgba(0,0,0,.09),0 2px 5px rgba(0,0,0,.1)}#overlay.open{display:block}.head{display:flex;align-items:center;gap:12px;font-size:18px;font-weight:500;letter-spacing:-.03em}.close{border:0;background:transparent;color:#fff;font-size:25px;line-height:14px;padding:0;cursor:pointer}.line{height:1px;background:rgba(255,255,255,.08);margin:20px 0 16px}.message{font-size:16px;line-height:1.9;font-weight:500;letter-spacing:-.03em}.helptext{font-size:16px;line-height:1.6;color:rgba(255,255,255,.6);cursor:pointer;letter-spacing:-.03em}.copy{font-size:12px;color:#8d8d8d;margin-top:7px}
</style></head><body><div id="toast" class="work"><div class="bubble" id="icon">•</div><div class="label" id="label"></div><button id="help" title="Voir le détail">?</button></div><section id="overlay"><div class="head"><button class="close" id="close">×</button><span id="title"></span></div><div class="line"></div><div class="message" id="message"></div><div class="line"></div><div class="helptext" id="helptext"></div><div class="copy">Cliquer ici pour copier le diagnostic</div></section><script>
let current=null, timer=null; const icons={work:'•',recording:'●',success:'✓',error:'!'};
const toastEl=document.getElementById('toast'), labelEl=document.getElementById('label'), iconEl=document.getElementById('icon'), helpEl=document.getElementById('help'), overlayEl=document.getElementById('overlay'), titleEl=document.getElementById('title'), messageEl=document.getElementById('message'), helpTextEl=document.getElementById('helptext'), closeEl=document.getElementById('close');
function showToast(data){current=data;clearTimeout(timer);toastEl.className=data.kind;toastEl.classList.add('visible');labelEl.textContent=data.text;iconEl.textContent=icons[data.kind]||'•';window.pywebview.api.toast_size();if(data.duration){timer=setTimeout(hideToast,data.duration)}}
function hideToast(){toastEl.classList.remove('visible');setTimeout(()=>window.pywebview.api.hide_toast(),240)}
function openDetails(){if(!current||!current.details)return;window.pywebview.api.details_size();toastEl.style.display='none';overlayEl.classList.add('open');titleEl.textContent=current.details.title+' : #'+current.details.code;messageEl.textContent=current.details.message;helpTextEl.textContent=current.details.help}
function closeDetails(){overlayEl.classList.remove('open');toastEl.style.display='flex';window.pywebview.api.toast_size()}
helpEl.onclick=openDetails;closeEl.onclick=closeDetails;helpTextEl.onclick=()=>window.pywebview.api.copy_diagnostic();
</script></body></html>"""


class Api:
    def __init__(self, owner: "WebOverlay") -> None: self.owner = owner
    def hide_toast(self): self.owner.window.hide()
    def toast_size(self): self.owner.window.resize(296, 46); self.owner.window.move(*self.owner.toast_position()); self.owner.window.show()
    def details_size(self): self.owner.window.resize(373, 275); self.owner.window.move(*self.owner.details_position()); self.owner.window.show()
    def copy_diagnostic(self): self.owner.app.copy_diagnostic()


class WebOverlay:
    def __init__(self, app) -> None:
        self.app, self.window, self.ready = app, None, threading.Event()

    def toast_position(self):
        return ((self.app.screen_width - 296)//2, self.app.screen_height - 122)

    def details_position(self):
        return ((self.app.screen_width - 373)//2, self.app.screen_height - 350)

    def run(self) -> None:
        self.app.screen_width, self.app.screen_height = self.app.screen_size()
        x, y = self.toast_position()
        self.window = webview.create_window("Voice Notes", html=HTML, js_api=Api(self), width=296, height=46, x=x, y=y, hidden=True, frameless=True, easy_drag=False, shadow=True, focus=False, on_top=True, transparent=True, background_color="#ffffff")
        self.window.events.loaded += self._loaded
        webview.start()

    def _loaded(self):
        self.ready.set(); self.app.on_ui_ready()

    def show(self, text, kind="work", duration=3000, details=None):
        if not self.ready.is_set(): return
        payload = json.dumps({"text": text, "kind": kind, "duration": duration, "details": details}, ensure_ascii=False)
        self.window.run_js(f"showToast({payload})")

    def hide(self):
        if self.window and self.ready.is_set(): self.window.hide()

    def close(self):
        if self.window: self.window.destroy()
