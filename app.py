"""Local voice-to-OneNote helper. No cloud API is used after Whisper is cached."""
from __future__ import annotations

import queue
import json
import os
import threading
import time
import traceback
import urllib.error
import urllib.request
import tkinter as tk
import ctypes
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pyperclip
import sounddevice as sd
from faster_whisper import WhisperModel
from pynput import keyboard
from pyvda import AppView, VirtualDesktop
from PIL import Image, ImageDraw, ImageFont, ImageTk

HOTKEY = "<ctrl>+<alt>+<space>"
EXIT_HOTKEY = "<ctrl>+<alt>+<shift>+<space>"
QUESTION_HOTKEY = "<ctrl>+<alt>+q"
CONTEXT_HOTKEY = "<ctrl>+<alt>+c"
WHISPER_SAMPLE_RATE = 16_000
# Use the Windows default input device.  Hard-coding a PortAudio index makes the
# app silently target the wrong device as soon as an audio interface is added,
# removed, or its drivers are updated.
INPUT_DEVICE: int | None = None
MODEL_NAME = "small"  # Better French transcription while remaining viable on an 8 GB PC with INT8 CPU inference.
LLM_MODEL_NAME = "qwen2.5:1.5b"
STREAM_CHUNK_SECONDS = 20
STREAM_OVERLAP_SECONDS = 1.5
MIN_FINAL_SEGMENT_SECONDS = 1.5
LANGUAGE = "fr"
WHISPER_INITIAL_PROMPT = (
    "Ceci est une prise de notes vocale en français. "
    "Transcris fidèlement les phrases prononcées, avec ponctuation et accents. "
    "Ne traduis pas. Respecte les noms propres, les termes techniques et les nombres."
)
CLEANUP_PROMPT = """Corrige cette transcription vocale française en conservant exactement le style,
l'ordre, le ton et les formulations de la personne qui parle. Ne résume pas, ne reformule
pas et n'ajoute aucune information. Corrige uniquement les erreurs manifestes de
reconnaissance vocale lorsque le contexte permet d'identifier avec certitude le mot voulu.
Supprime uniquement les répétitions exactes, les mots clairement parasites ou les fragments
provenant de paroles superposées. Conserve les hésitations, les phrases incomplètes et les
passages ambigus s'ils font partie du discours. Si tu n'es pas certain d'une correction,
garde le texte original."""
FOCUS_SETTLE_SECONDS = 0.35
WEB_UI = os.environ.get("VOICE_NOTES_WEB") == "1"
QUESTION_PROMPT = """Tu es un assistant de cours. Reponds uniquement a partir du contexte fourni.
Si le contexte ne permet pas de repondre, indique-le clairement. Reponds en francais avec une
reponse courte, puis des puces utiles. N'invente aucune source."""

# ASCII source text avoids passing legacy mojibake characters to the local models.
WHISPER_INITIAL_PROMPT = (
    "Ceci est une prise de notes vocale en francais. "
    "Transcris fidelement les phrases prononcees, avec ponctuation. "
    "Ne traduis pas. Respecte les noms propres, les termes techniques et les nombres."
)
CLEANUP_PROMPT = """Corrige cette transcription vocale en francais sans changer le sens.
Conserve le style, l'ordre, le ton, les hesitations utiles et les passages ambigus.
Ne resume pas, ne reformule pas et n'ajoute aucune information.
Corrige uniquement les erreurs manifestes de reconnaissance vocale et retire les repetitions exactes.
Retourne uniquement le texte nettoye, sans commentaire."""


def repair_display_text(value):
    if isinstance(value, str):
        try:
            return value.encode("latin1").decode("utf-8")
        except UnicodeError:
            return value
    if isinstance(value, list): return [repair_display_text(item) for item in value]
    if isinstance(value, dict): return {key: repair_display_text(item) for key, item in value.items()}
    return value


def apply_rounded_window_region(window: tk.Toplevel, width: int, height: int, radius: int) -> None:
    """Use a native Windows clip instead of magenta chroma-key transparency."""
    window.update_idletasks()
    region = ctypes.windll.gdi32.CreateRoundRectRgn(0, 0, width + 1, height + 1, radius, radius)
    ctypes.windll.user32.SetWindowRgn(window.winfo_id(), region, True)


@dataclass
class PasteTarget:
    hwnd: int
    title: str
    desktop_number: int | None


class Toast:
    """A 296x46 status tag, deliberately kept separate from the diagnostic overlay."""
    WIDTH, HEIGHT = 296, 46
    ASSET_DIR = Path(__file__).with_name("assets")
    APP_ICON_FILES = {
        "word": "0877fc4cdb9ff70b4647ad05d5aba6684812b1f4.png",
        "onenote": "af2a6280cc6e6d04267283dd9a5d00d2fad440fc.png",
    }

    def __init__(self, root: tk.Tk, open_details) -> None:
        self.root = root
        self.open_details = open_details
        self.window = tk.Toplevel(root, bg="#ffffff")
        self.window.overrideredirect(True)
        self.window.attributes("-topmost", True)
        self.canvas = tk.Canvas(self.window, width=self.WIDTH, height=self.HEIGHT, highlightthickness=0, bg="#ffffff")
        self.canvas.pack()
        self.window.after_idle(apply_rounded_window_region, self.window, self.WIDTH, self.HEIGHT, self.HEIGHT)
        self.after_id: str | None = None
        self.shimmer_id: str | None = None
        self.shimmer_phase = 0
        self.motion_generation = 0
        self.visible = False
        self.error_details: dict | None = None
        self.photo: ImageTk.PhotoImage | None = None
        self.current_text = ""
        self.current_kind = "work"
        self.target_app: str | None = None
        self.app_icons: dict[str, Image.Image] = {}
        for name, filename in self.APP_ICON_FILES.items():
            path = self.ASSET_DIR / filename
            if path.exists():
                with Image.open(path) as source:
                    self.app_icons[name] = source.convert("RGBA")
        self.canvas.bind("<Button-1>", self._click)
        self.window.withdraw()

    def _position(self, hidden: bool = False) -> tuple[int, int]:
        x = (self.root.winfo_screenwidth() - self.WIDTH) // 2
        bottom = 76
        y = self.root.winfo_screenheight() - bottom - self.HEIGHT
        return x, self.root.winfo_screenheight() + 4 if hidden else y

    @staticmethod
    def _font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
        filename = "C:/Windows/Fonts/segoeuib.ttf" if bold else "C:/Windows/Fonts/segoeui.ttf"
        return ImageFont.truetype(filename, size)

    def _draw_status_icon(self, draw: ImageDraw.ImageDraw, kind: str, ink: str, scale: int) -> None:
        """16px status glyphs, drawn at 4x to keep the Figma tag edges crisp."""
        xy = lambda value: int(value * scale)
        if kind == "recording":
            draw.rounded_rectangle((xy(19), xy(12), xy(27), xy(25)), radius=xy(4), fill=ink)
            draw.arc((xy(15), xy(16), xy(31), xy(31)), 0, 180, fill=ink, width=xy(2))
            draw.line((xy(23), xy(30), xy(23), xy(34)), fill=ink, width=xy(2))
            draw.line((xy(19), xy(34), xy(27), xy(34)), fill=ink, width=xy(2))
        elif kind == "success":
            draw.polygon([(xy(16), xy(23)), (xy(21), xy(28)), (xy(31), xy(17)), (xy(31), xy(22)), (xy(21), xy(33)), (xy(16), xy(28))], fill=ink)
        elif kind == "error":
            code = (self.error_details or {}).get("code")
            if code == 32:
                draw.rounded_rectangle((xy(19), xy(12), xy(27), xy(25)), radius=xy(4), fill=ink)
                draw.arc((xy(15), xy(16), xy(31), xy(31)), 0, 180, fill=ink, width=xy(2))
                draw.line((xy(23), xy(30), xy(23), xy(34)), fill=ink, width=xy(2))
            elif code == 48:
                draw.polygon([(xy(15), xy(20)), (xy(20), xy(20)), (xy(27), xy(14)), (xy(27), xy(30)), (xy(20), xy(25)), (xy(15), xy(25))], fill=ink)
                draw.arc((xy(23), xy(17), xy(33), xy(29)), -60, 60, fill=ink, width=xy(2))
            else:
                draw.polygon([(xy(23), xy(13)), (xy(32), xy(30)), (xy(14), xy(30))], fill=ink)
                draw.text((xy(23), xy(24)), "!", anchor="mm", fill="#FFFFFF", font=self._font(xy(10), True))
        else:
            draw.ellipse((xy(20), xy(20), xy(26), xy(26)), fill=ink)

    def _draw_shimmer_label(self, image: Image.Image, text: str, scale: int) -> None:
        """A moving highlight clipped to the label, matching the Figma shimmer."""
        width, height = image.size
        mask = Image.new("L", (width, height), 0)
        mask_draw = ImageDraw.Draw(mask)
        mask_draw.text((47 * scale, 23 * scale), text, anchor="lm", fill=255, font=self._font(11 * scale, True))
        gradient = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        gradient_draw = ImageDraw.Draw(gradient)
        travel = width + 120 * scale
        center = (self.shimmer_phase * 18 * scale) % travel - 60 * scale
        for x in range(width):
            distance = min(1.0, abs(x - center) / (70 * scale))
            brightness = int(35 + (140 - 35) * (1.0 - distance) ** 2)
            gradient_draw.line((x, 0, x, height), fill=(brightness, brightness, brightness, 255))
        gradient.putalpha(mask)
        image.alpha_composite(gradient)

    def _render(self, text: str, kind: str, text_color: str | None = None) -> None:
        """Draw at 4x then downsample: native Tk canvas arcs are visibly pixelated."""
        scale = 4
        size = (self.WIDTH * scale, self.HEIGHT * scale)
        image = Image.new("RGBA", size, (0, 0, 0, 0))
        draw = ImageDraw.Draw(image)
        xy = lambda value: int(value * scale)
        draw.rounded_rectangle((0, 0, xy(self.WIDTH) - 1, xy(self.HEIGHT) - 1), radius=xy(23), fill="#FFFFFF", outline=(0, 0, 0, 38), width=xy(1))
        if kind == "error":
            bubble, ink, symbol, color = "#FFF0F0", "#D43333", "!", "#963333"
        elif kind == "recording":
            bubble, ink, symbol, color = "#E6F4FF", "#69A2D1", "●", "#3E6E94"
        elif kind == "success":
            bubble, ink, symbol, color = "#EAF8EF", "#318452", "✓", "#318452"
        else:
            bubble, ink, symbol, color = "#F4F4F4", "#6E6E6E", "•", "#454545"
        draw.ellipse((xy(7), xy(7), xy(39), xy(39)), fill=bubble)
        if kind == "success" and self.target_app:
            bubble = "#F4F4F4"
            draw.ellipse((xy(7), xy(7), xy(39), xy(39)), fill=bubble)
        app_icon = self.app_icons.get(self.target_app or "") if kind == "success" else None
        if app_icon:
            logo = app_icon.resize((xy(16), xy(16)), Image.Resampling.LANCZOS)
            image.alpha_composite(logo, (xy(15), xy(15)))
        else:
            self._draw_status_icon(draw, kind, ink, scale)
        if kind == "work":
            self._draw_shimmer_label(image, text, scale)
        else:
            draw.text((xy(47), xy(23)), text, anchor="lm", fill=text_color or color, font=self._font(xy(11), True))
        if kind == "error":
            draw.ellipse((xy(261), xy(13), xy(281), xy(33)), fill="#756767")
            draw.text((xy(271), xy(23)), "?", anchor="mm", fill="#FFFFFF", font=self._font(xy(11), True))
        image = image.resize((self.WIDTH, self.HEIGHT), Image.Resampling.LANCZOS)
        self.photo = ImageTk.PhotoImage(image)
        self.canvas.delete("all")
        self.canvas.create_image(0, 0, anchor="nw", image=self.photo)

    def _click(self, event) -> None:
        if self.current_kind == "error" and event.x >= 250:
            self.open_details(self.error_details)

    def show(self, text: str, kind: str = "work", duration: int | None = 3000, details: dict | None = None) -> None:
        self.motion_generation += 1
        if self.after_id:
            self.root.after_cancel(self.after_id)
        if self.shimmer_id:
            self.root.after_cancel(self.shimmer_id)
            self.shimmer_id = None
        self.error_details = details
        self.current_text, self.current_kind = text, kind
        self._render(text, kind)
        self.window.geometry(f"{self.WIDTH}x{self.HEIGHT}+{self._position(True)[0]}+{self._position(True)[1]}")
        self.window.deiconify()
        self.visible = True
        self._slide_in(0, self.motion_generation)
        if kind == "work":
            self._shimmer("")
        if duration is not None:
            self.after_id = self.root.after(duration, self.hide)

    def set_target_application(self, window_title: str) -> None:
        title = window_title.lower()
        if "onenote" in title:
            self.target_app = "onenote"
        elif "word" in title or "winword" in title:
            self.target_app = "word"
        else:
            self.target_app = None

    def _slide_in(self, step: int, generation: int) -> None:
        if generation != self.motion_generation:
            return
        x, target_y = self._position()
        _, start_y = self._position(True)
        y = int(start_y + (target_y - start_y) * min(step, 12) / 12)
        self.window.geometry(f"{self.WIDTH}x{self.HEIGHT}+{x}+{y}")
        if step < 12:
            self.root.after(12, self._slide_in, step + 1, generation)

    def _shimmer(self, base: str) -> None:
        self._render(self.current_text, self.current_kind)
        self.shimmer_phase += 1
        self.shimmer_id = self.root.after(50, self._shimmer, base)

    def hide(self) -> None:
        if self.shimmer_id:
            self.root.after_cancel(self.shimmer_id)
            self.shimmer_id = None
        if self.visible:
            self.motion_generation += 1
            self._slide_out(0, self.motion_generation)

    def _slide_out(self, step: int, generation: int) -> None:
        if generation != self.motion_generation:
            return
        x, start_y = self._position()
        _, target_y = self._position(True)
        y = int(start_y + (target_y - start_y) * min(step, 12) / 12)
        self.window.geometry(f"{self.WIDTH}x{self.HEIGHT}+{x}+{y}")
        if step < 12:
            self.root.after(12, self._slide_out, step + 1, generation)
        else:
            self.window.withdraw()
            self.visible = False


class ErrorOverlay:
    """A draggable dark details card; it only exists while the error help is open."""
    def __init__(self, root: tk.Tk, logs: list[str]) -> None:
        self.root, self.logs = root, logs
        self.shade: tk.Toplevel | None = None
        self.card: tk.Toplevel | None = None
        self.drag = (0, 0)
        self.animation_generation = 0

    def show(self, details: dict | None) -> None:
        if not details:
            return
        self.close()
        self.shade = tk.Toplevel(self.root, bg="#000000")
        self.shade.overrideredirect(True)
        self.shade.attributes("-alpha", 0.0, "-topmost", True)
        self.shade.geometry(f"{self.root.winfo_screenwidth()}x{self.root.winfo_screenheight()}+0+0")
        self.shade.bind("<Button-1>", lambda _event: self.close())
        self.card = tk.Toplevel(self.root, bg="#191919")
        self.card.overrideredirect(True)
        self.card.attributes("-topmost", True)
        self.card.geometry("373x275+108+461")
        self.card.after_idle(apply_rounded_window_region, self.card, 373, 275, 48)
        self.card.bind("<Button-1>", self._drag_start)
        self.card.bind("<B1-Motion>", self._drag_move)
        frame = tk.Frame(self.card, bg="#191919", padx=24, pady=20)
        frame.pack(fill="both", expand=True)
        tk.Button(frame, text="×", command=self.close, bg="#191919", fg="#FFFFFF", bd=0, activebackground="#191919", activeforeground="#FFFFFF", font=("Inter", 18, "bold"), cursor="hand2").place(x=0, y=-6)
        tk.Label(frame, text=details["title"], bg="#191919", fg="#FFFFFF", font=("Inter", 13, "bold")).pack(anchor="w", padx=(28, 0))
        tk.Frame(frame, height=1, bg="#303030").pack(fill="x", pady=(16, 14))
        tk.Label(frame, text=details["message"], bg="#191919", fg="#FFFFFF", justify="left", wraplength=310, font=("Inter", 10, "bold")).pack(anchor="w")
        tk.Frame(frame, height=1, bg="#303030").pack(fill="x", pady=14)
        hint = "Clique ici pour copier le diagnostic technique"
        copy = tk.Label(frame, text=f"{details['help']}\n\n{hint}", bg="#191919", fg="#A8A8A8", justify="left", wraplength=310, font=("Inter", 10), cursor="hand2")
        copy.pack(anchor="w")
        copy.bind("<Button-1>", lambda _event: self.copy(details))
        self.animation_generation += 1
        self._animate_in(0, self.animation_generation)

    def _animate_in(self, step: int, generation: int) -> None:
        if generation != self.animation_generation or not self.shade or not self.card:
            return
        progress = min(step, 10) / 10
        self.shade.attributes("-alpha", 0.42 * progress)
        y = 443 + int(18 * (1 - progress))
        self.card.geometry(f"373x275+108+{y}")
        if step < 10:
            self.root.after(16, self._animate_in, step + 1, generation)

    def copy(self, details: dict) -> None:
        pyperclip.copy("\n".join(self.logs[-20:]) + f"\nCode : #{details['code']}")

    def _drag_start(self, event) -> None:
        self.drag = (event.x_root - self.card.winfo_x(), event.y_root - self.card.winfo_y())

    def _drag_move(self, event) -> None:
        if self.card:
            self.card.geometry(f"+{event.x_root-self.drag[0]}+{event.y_root-self.drag[1]}")

    def close(self) -> None:
        self.animation_generation += 1
        for window in (self.card, self.shade):
            if window and window.winfo_exists():
                window.destroy()
        self.card = self.shade = None


class ControlWindow:
    """Always-visible session log so startup and transcription are inspectable."""

    def __init__(self, root: tk.Tk, app: "VoiceNotesApp") -> None:
        self.window = tk.Toplevel(root, bg="#161616")
        self.app = app
        self.window.title("Voice Notes")
        self.window.geometry("720x510+80+80")
        self.window.minsize(500, 240)
        self.window.protocol("WM_DELETE_WINDOW", app.close)

        header = tk.Label(
            self.window,
            text="Journal de diagnostic — Voice Notes",
            bg="#161616",
            fg="#FFFFFF",
            font=("Segoe UI", 12, "bold"),
            padx=16,
            pady=12,
        )
        header.configure(text="Voice Notes - commandes et journal")
        header.pack(anchor="w")
        tk.Label(
            self.window,
            text="Cette fenêtre reste ouverte pendant l'utilisation. Les erreurs et les transcriptions y apparaissent.",
            bg="#161616",
            fg="#B8B8B8",
            font=("Segoe UI", 9),
            padx=16,
        ).pack(anchor="w", pady=(0, 10))

        controls = tk.Frame(self.window, bg="#161616", padx=16)
        controls.pack(fill="x", pady=(0, 10))
        self.record_button = tk.Button(
            controls, text="Démarrer l'enregistrement", command=app.toggle_recording,
            bg="#2F7D4B", fg="#FFFFFF", activebackground="#25643C",
            activeforeground="#FFFFFF", relief="flat", padx=16, pady=9,
            font=("Segoe UI", 10, "bold"), cursor="hand2",
        )
        self.record_button.pack(side="left")

        hotkey = tk.Frame(self.window, bg="#202020", padx=16, pady=12)
        hotkey.pack(fill="x", padx=16, pady=(0, 8))
        tk.Label(hotkey, text="Raccourci global", bg="#202020", fg="#FFFFFF", font=("Segoe UI", 10, "bold")).grid(row=0, column=0, sticky="w")
        tk.Label(hotkey, text="Exemple : Ctrl+Shift+R. Le bouton fonctionne sans raccourci.", bg="#202020", fg="#B8B8B8", font=("Segoe UI", 9)).grid(row=1, column=0, sticky="w", pady=(2, 8))
        self.hotkey_var = tk.StringVar(value=app.hotkey_display)
        tk.Entry(hotkey, textvariable=self.hotkey_var, bg="#0D0D0D", fg="#FFFFFF", insertbackground="#FFFFFF", relief="flat", font=("Segoe UI", 10), width=30).grid(row=2, column=0, sticky="ew", ipady=6)
        tk.Button(hotkey, text="Appliquer", command=self.apply_hotkey, bg="#3C6EAA", fg="#FFFFFF", activebackground="#315A8B", activeforeground="#FFFFFF", relief="flat", padx=14, pady=7, cursor="hand2").grid(row=2, column=1, padx=(10, 0))
        hotkey.columnconfigure(0, weight=1)
        self.hotkey_status = tk.Label(hotkey, text="", bg="#202020", fg="#9BD6AA", font=("Segoe UI", 9))
        self.hotkey_status.grid(row=3, column=0, columnspan=2, sticky="w", pady=(7, 0))

        content = tk.Frame(self.window, bg="#161616", padx=16, pady=8)
        content.pack(fill="both", expand=True)
        scrollbar = tk.Scrollbar(content)
        scrollbar.pack(side="right", fill="y")
        self.text = tk.Text(
            content,
            bg="#0D0D0D",
            fg="#E8E8E8",
            insertbackground="#FFFFFF",
            bd=0,
            padx=12,
            pady=10,
            font=("Cascadia Mono", 9),
            wrap="word",
            state="disabled",
            yscrollcommand=scrollbar.set,
        )
        self.text.pack(side="left", fill="both", expand=True)
        scrollbar.config(command=self.text.yview)

    def apply_hotkey(self) -> None:
        self.app.apply_hotkey(self.hotkey_var.get())

    def set_hotkey_status(self, text: str, ok: bool) -> None:
        self.hotkey_status.configure(text=text, fg="#9BD6AA" if ok else "#F29A9A")

    def set_recording(self, recording: bool) -> None:
        self.record_button.configure(
            text="Arrêter l'enregistrement" if recording else "Démarrer l'enregistrement",
            bg="#B44848" if recording else "#2F7D4B",
            activebackground="#8E3838" if recording else "#25643C",
        )

    def append(self, message: str) -> None:
        self.text.configure(state="normal")
        self.text.insert("end", message + "\n")
        self.text.see("end")
        self.text.configure(state="disabled")


def normalize_hotkey(value: str) -> tuple[str, str]:
    aliases = {"control": "ctrl", "option": "alt", "escape": "esc", "return": "enter", "espace": "space", "entrée": "enter"}
    special = {"ctrl", "alt", "shift", "space", "enter", "tab", "esc", "cmd", "win"}
    parts = [aliases.get(part.strip().lower(), part.strip().lower()) for part in value.split("+")]
    if len(parts) < 2 or any(not part for part in parts):
        raise ValueError("Utilisez au moins deux touches, par exemple Ctrl+Shift+R.")
    rendered = []
    for part in parts:
        if part in special or (part.startswith("f") and part[1:].isdigit()):
            rendered.append(f"<{part}>")
        elif len(part) == 1 and part.isalnum():
            rendered.append(part)
        else:
            raise ValueError(f"Touche non reconnue : {part}")
    return "+".join(rendered), "+".join(part.title() if part != "ctrl" else "Ctrl" for part in parts)


class WebControls:
    def __init__(self, app: "VoiceNotesApp") -> None:
        self.app = app

    def append(self, _message: str) -> None: pass
    def set_hotkey_status(self, text: str, ok: bool) -> None:
        self.app.web_hotkey_status = {"text": text, "ok": ok}
    def set_recording(self, recording: bool) -> None:
        self.app.web_recording = recording


class WebToast:
    def __init__(self, app: "VoiceNotesApp") -> None:
        self.app = app
        self.target_app: str | None = None

    def set_target_application(self, title: str) -> None:
        title = title.lower()
        self.target_app = "onenote" if "onenote" in title else "word" if "word" in title or "winword" in title else None

    def show(self, text: str, kind: str = "work", duration: int | None = 3000, details: dict | None = None) -> None:
        self.app.web_notice = {"text": text, "kind": kind, "app": self.target_app if kind == "success" else None, "details": details, "expiresAt": time.time() + duration / 1000 if duration else None}

    def close(self) -> None: pass


class VoiceNotesApp:
    def __init__(self) -> None:
        self.recording = False
        self.audio_chunks: queue.Queue[np.ndarray] = queue.Queue()
        self.stream: sd.InputStream | None = None
        self.model: WhisperModel | None = None
        self.model_lock = threading.Lock()
        self.transcribing = False
        self.streaming_thread: threading.Thread | None = None
        self.session_segments: list[str] = []
        self.session_lock = threading.Lock()
        self.llm_available: bool | None = None
        self.capture_sample_rate = 48_000
        self.paste_target: PasteTarget | None = None
        self.last_external_target: PasteTarget | None = None
        self.paste_keyboard = keyboard.Controller()
        self.last_hotkey_at = 0.0
        self.hotkey = HOTKEY
        self.hotkey_display = "Ctrl+Alt+Espace"
        self.web_notice: dict | None = None
        self.web_recording = False
        self.web_hotkey_status = {"text": "", "ok": True}
        self.question_hotkey = QUESTION_HOTKEY
        self.question_hotkey_display = "Ctrl+Alt+Q"
        self.context_hotkey = CONTEXT_HOTKEY
        self.context_hotkey_display = "Ctrl+Alt+C"
        self.question_stream: sd.InputStream | None = None
        self.question_recording = False
        self.question_audio_chunks: queue.Queue[np.ndarray] = queue.Queue()
        self.question_context: list[dict] = []
        self.web_question = {"status": "idle", "question": "", "answer": ""}
        self.web_panels = {"context": False}
        self.web_server: ThreadingHTTPServer | None = None
        self.logs: list[str] = []
        self.screen_width = self.screen_height = 0
        if WEB_UI:
            self.root = None
            self.diagnostic = WebControls(self)
            self.error_overlay = None
            self.toast = WebToast(self)
        else:
            self.root = tk.Tk()
            self.root.withdraw()
            self.root.protocol("WM_DELETE_WINDOW", self.close)
            self.diagnostic = ControlWindow(self.root, self)
            self.error_overlay = ErrorOverlay(self.root, self.logs)
            self.toast = Toast(self.root, self.error_overlay.show)
        self.listener = None

    def ui(self, callback, *args) -> None:
        if WEB_UI:
            callback(*args)
        else:
            self.root.after(0, callback, *args)

    def screen_size(self) -> tuple[int, int]:
        return ctypes.windll.user32.GetSystemMetrics(0), ctypes.windll.user32.GetSystemMetrics(1)

    def on_ui_ready(self) -> None:
        self.log("Mode progressif : Whisper Small + Qwen 2.5 1.5B local sont prets.")
        self.log(f"Application démarrée. Raccourci : {self.hotkey_display}")
        threading.Thread(target=self.preload_whisper_model, daemon=True).start()
        threading.Thread(target=self.track_external_target, daemon=True).start()
        self.start_listener()

    @staticmethod
    def is_voice_notes_window(target: PasteTarget | None) -> bool:
        return bool(target and "voice notes" in target.title.casefold())

    def track_external_target(self) -> None:
        """Keep the last document window so clicking Electron's Start button does not steal the paste target."""
        while True:
            try:
                candidate = remember_active_window()
                if candidate and not self.is_voice_notes_window(candidate):
                    self.last_external_target = candidate
            except Exception:
                pass
            time.sleep(0.20)
        self.toast.show("Prêt — Ctrl + Alt + Espace", "work", duration=3000)

    def start_listener(self) -> None:
        if self.listener:
            self.listener.stop()
        self.listener = keyboard.GlobalHotKeys({self.hotkey: self.on_hotkey, self.question_hotkey: self.on_question_hotkey, self.context_hotkey: self.on_context_hotkey, EXIT_HOTKEY: self.request_exit})
        self.listener.start()

    def apply_hotkey(self, value: str) -> None:
        try:
            hotkey, display = normalize_hotkey(value)
            previous_hotkey, previous_display = self.hotkey, self.hotkey_display
            self.hotkey, self.hotkey_display = hotkey, display
            try:
                self.start_listener()
            except Exception:
                self.hotkey, self.hotkey_display = previous_hotkey, previous_display
                self.start_listener()
                raise
            if not WEB_UI:
                self.diagnostic.hotkey_var.set(display)
            self.diagnostic.set_hotkey_status(f"Raccourci appliqué : {display}", True)
            self.log(f"Raccourci modifié : {display}")
        except ValueError as exc:
            self.diagnostic.set_hotkey_status(str(exc), False)
        except Exception as exc:
            self.diagnostic.set_hotkey_status(f"Impossible d'activer ce raccourci : {exc}", False)

    def copy_diagnostic(self) -> None:
        pyperclip.copy("\n".join(self.logs[-20:]))

    def set_status(self, value: str) -> None:
        self.log(value)
        lower = value.lower()
        if "erreur" in lower or "silencieux" in lower or "aucun texte" in lower:
            self.show_error(121, "Erreur inconnue", value, "Consultez les paramètres ou copiez le diagnostic pour obtenir de l'aide.")
        elif "enregistrement" in lower:
            self.toast.show("Transcription...", "work", duration=3000)
        elif "collée" in lower:
            self.toast.show("Transcription ajoutée", "success", duration=2000)
        else:
            self.toast.show(short_toast_text(value), "work", duration=None if "cours" in lower or "chargement" in lower else 3000)

    def show_error(self, code: int, title: str, message: str, help_text: str, technical: str | None = None) -> None:
        details = {"code": code, "title": title, "message": message, "help": help_text, "technical": technical}
        self.toast.show(f"{title} : #{code}", "error", duration=12000, details=details)

    def log(self, message: str) -> None:
        stamp = time.strftime("%H:%M:%S")
        entry = f"[{stamp}] {message}"
        self.logs.append(entry)
        self.logs[:] = self.logs[-100:]
        self.diagnostic.append(entry)

    def web_state(self) -> dict:
        notice = self.web_notice
        if notice and notice.get("expiresAt") and notice["expiresAt"] < time.time():
            self.web_notice = notice = None
        return repair_display_text({"recording": self.recording, "busy": self.transcribing, "shortcut": self.hotkey_display, "questionShortcut": self.question_hotkey_display, "contextShortcut": self.context_hotkey_display, "question": self.web_question, "context": self.question_context, "panels": self.web_panels, "notice": notice, "logs": self.logs[-100:], "hotkeyStatus": self.web_hotkey_status})

    def run_web_server(self) -> None:
        owner = self
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args): pass
            def send_json(self, data, status=200):
                body = json.dumps(data, ensure_ascii=False).encode("utf-8")
                self.send_response(status); self.send_header("Content-Type", "application/json"); self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)
            def do_GET(self):
                if self.path == "/state": return self.send_json(owner.web_state())
                if self.path == "/context-candidates": return self.send_json({"items": owner.context_candidates()})
                self.send_json({"error": "not found"}, 404)
            def do_POST(self):
                length = int(self.headers.get("Content-Length", 0)); payload = json.loads(self.rfile.read(length) or b"{}")
                if self.path == "/toggle": owner.toggle_recording()
                elif self.path == "/start": owner.start_recording()
                elif self.path == "/stop": owner.stop_recording()
                elif self.path == "/hotkey": owner.apply_hotkey(payload.get("value", ""))
                elif self.path == "/question-toggle": owner.toggle_question()
                elif self.path == "/question-hotkey": owner.apply_question_hotkey(payload.get("value", ""))
                elif self.path == "/context-hotkey": owner.apply_context_hotkey(payload.get("value", ""))
                elif self.path == "/context": owner.set_question_context(payload.get("items", []))
                elif self.path == "/context-panel-open": owner.web_panels["context"] = True
                elif self.path == "/context-panel-close": owner.web_panels["context"] = False
                elif self.path == "/question-close": owner.web_question = {"status": "idle", "question": "", "answer": ""}
                else: return self.send_json({"error": "not found"}, 404)
                self.send_json(owner.web_state())
        self.on_ui_ready()
        self.web_server = ThreadingHTTPServer(("127.0.0.1", 8765), Handler)
        self.web_server.serve_forever()

    def on_hotkey(self) -> None:
        now = time.monotonic()
        if now - self.last_hotkey_at < 0.35:
            return
        self.last_hotkey_at = now
        self.ui(self.toggle_recording)

    def on_question_hotkey(self) -> None:
        self.ui(self.toggle_question)

    def on_context_hotkey(self) -> None:
        self.ui(self.toggle_context_panel)

    def toggle_context_panel(self) -> None:
        self.web_panels["context"] = not self.web_panels.get("context", False)
        self.log("Panneau de contexte " + ("ouvert." if self.web_panels["context"] else "ferme."))

    def apply_question_hotkey(self, value: str) -> None:
        try:
            hotkey, display = normalize_hotkey(value)
            previous, previous_display = self.question_hotkey, self.question_hotkey_display
            self.question_hotkey, self.question_hotkey_display = hotkey, display
            try:
                self.start_listener()
            except Exception:
                self.question_hotkey, self.question_hotkey_display = previous, previous_display
                self.start_listener()
                raise
            self.log(f"Raccourci question modifie : {display}")
        except Exception as exc:
            self.log(f"Raccourci question invalide : {exc}")

    def apply_context_hotkey(self, value: str) -> None:
        try:
            hotkey, display = normalize_hotkey(value)
            previous, previous_display = self.context_hotkey, self.context_hotkey_display
            self.context_hotkey, self.context_hotkey_display = hotkey, display
            try:
                self.start_listener()
            except Exception:
                self.context_hotkey, self.context_hotkey_display = previous, previous_display
                self.start_listener()
                raise
            self.log(f"Raccourci contexte modifie : {display}")
        except Exception as exc:
            self.log(f"Raccourci contexte invalide : {exc}")

    def context_candidates(self) -> list[dict]:
        items = []
        for target in list_open_document_windows():
            items.append({"id": f"window:{target.hwnd}", "title": target.title, "type": document_type(target.title), "text": ""})
        try:
            clipboard = pyperclip.paste().strip()
            if clipboard:
                items.insert(0, {"id": "selection:clipboard", "title": "Texte selectionne", "type": "selection", "text": clipboard[:12000]})
        except Exception:
            pass
        return items[:12]

    def set_question_context(self, items: list[dict]) -> None:
        self.question_context = [
            {"id": str(item.get("id", "")), "title": str(item.get("title", "Contexte"))[:160], "type": str(item.get("type", "document")), "text": str(item.get("text", ""))[:12000]}
            for item in items if isinstance(item, dict)
        ][:12]
        self.log(f"Contexte question sauvegarde : {len(self.question_context)} element(s).")
        self.web_panels["context"] = False

    def toggle_question(self) -> None:
        if self.question_recording:
            self.stop_question()
        else:
            self.start_question()

    def start_question(self) -> None:
        if self.recording or self.transcribing or self.question_recording:
            self.log("Question ignoree : une operation vocale est deja en cours.")
            return
        try:
            while not self.question_audio_chunks.empty(): self.question_audio_chunks.get_nowait()
            input_device = INPUT_DEVICE if INPUT_DEVICE is not None else sd.default.device[0]
            device = sd.query_devices(input_device)
            self.capture_sample_rate = int(device["default_samplerate"])
            self.question_stream = sd.InputStream(samplerate=self.capture_sample_rate, channels=1, dtype="float32", device=input_device, callback=lambda indata, *_args: self.question_audio_chunks.put(indata.copy()))
            self.question_stream.start()
            self.question_recording = True
            self.web_question = {"status": "listening", "question": "", "answer": ""}
            self.toast.show("Ecoute de la question", "recording", duration=None)
            self.log("Ecoute de la question demarree.")
        except Exception as exc:
            self.show_error(32, "Erreur du microphone", "La question ne peut pas etre enregistree.", "Verifiez le micro et les autorisations Windows.", str(exc))

    def stop_question(self) -> None:
        if not self.question_stream: return
        self.question_stream.stop(); self.question_stream.close(); self.question_stream = None
        self.question_recording = False
        self.web_question = {"status": "thinking", "question": "", "answer": ""}
        threading.Thread(target=self.transcribe_question_and_answer, daemon=True).start()

    def transcribe_question_and_answer(self) -> None:
        chunks = []
        while not self.question_audio_chunks.empty(): chunks.append(self.question_audio_chunks.get_nowait())
        if not chunks:
            self.ui(self.show_error, 48, "Pas de son entendu", "Aucune question audio n'a ete recue.", "Verifiez le volume du microphone.")
            self.web_question = {"status": "idle", "question": "", "answer": ""}; return
        audio = np.concatenate(chunks, axis=0).reshape(-1)
        try:
            model = self.ensure_whisper_model()
            segments, _info = model.transcribe(resample_for_whisper(audio, self.capture_sample_rate), language=LANGUAGE, vad_filter=True, beam_size=5, initial_prompt=WHISPER_INITIAL_PROMPT)
            question = clean_text(" ".join(segment.text.strip() for segment in segments))
            if not question: raise ValueError("Question vide")
            answer = self.answer_question_with_context(question)
            self.web_question = {"status": "answer", "question": question, "answer": answer}
            self.ui(self.toast.show, "Reponse prete", "success", 2000)
            self.ui(self.log, f"Question : {question}")
        except Exception as exc:
            self.web_question = {"status": "error", "question": "", "answer": ""}
            self.ui(self.show_error, 121, "Erreur de reponse", "La question n'a pas pu etre traitee.", "Consultez le journal de diagnostic.", str(exc))

    def answer_question_with_context(self, question: str) -> str:
        blocks = []
        for item in self.question_context:
            excerpt = item.get("text") or f"Document ouvert : {item.get('title', 'Sans titre')}"
            blocks.append(f"### {item.get('title', 'Contexte')}\n{excerpt}")
        context = "\n\n".join(blocks) or "Aucun contexte selectionne."
        payload = json.dumps({"model": LLM_MODEL_NAME, "stream": False, "keep_alive": "5m", "options": {"temperature": 0.2, "num_ctx": 4096, "num_predict": 700}, "messages": [{"role": "system", "content": QUESTION_PROMPT}, {"role": "user", "content": f"CONTEXTE:\n{context}\n\nQUESTION:\n{question}"}]}).encode("utf-8")
        request = urllib.request.Request("http://127.0.0.1:11434/api/chat", data=payload, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(request, timeout=120) as response:
            answer = json.loads(response.read().decode("utf-8")).get("message", {}).get("content", "").strip()
        return answer or "Je n'ai pas pu produire de reponse a partir du contexte selectionne."

    def request_exit(self) -> None:
        self.ui(self.close)

    def toggle_recording(self) -> None:
        if self.recording:
            self.stop_recording()
        else:
            self.start_recording()

    def start_recording(self) -> None:
        if self.transcribing:
            self.log("Demande de demarrage ignoree : finalisation encore en cours.")
            return
        try:
            active_target = remember_active_window()
            self.paste_target = self.last_external_target if self.is_voice_notes_window(active_target) else active_target
            if self.paste_target:
                self.toast.set_target_application(self.paste_target.title)
                desktop = self.paste_target.desktop_number
                self.log(f"Cible mémorisée : {self.paste_target.title} (bureau {desktop or '?'})")
            else:
                self.log("ATTENTION : aucune fenêtre cible n'a pu être mémorisée")
            while not self.audio_chunks.empty():
                self.audio_chunks.get_nowait()

            def callback(indata, frames, time_info, status) -> None:
                if status:
                    self.ui(self.log, f"Audio : {status}")
                self.audio_chunks.put(indata.copy())

            input_device = INPUT_DEVICE if INPUT_DEVICE is not None else sd.default.device[0]
            device_info = sd.query_devices(input_device)
            # Use the sample rate supported by the hardware, then resample for Whisper.
            self.capture_sample_rate = int(device_info["default_samplerate"])
            self.stream = sd.InputStream(samplerate=self.capture_sample_rate, channels=1, dtype="float32", device=input_device, callback=callback)
            self.stream.start()
            self.recording = True
            self.transcribing = True
            self.session_segments = []
            self.streaming_thread = threading.Thread(target=self.streaming_transcription_loop, daemon=True)
            self.streaming_thread.start()
            self.diagnostic.set_recording(True)
            device_name = device_info["name"]
            self.set_status(f"● ENREGISTREMENT ({device_name}, {self.capture_sample_rate} Hz) — refaites Ctrl+Alt+Espace pour terminer")
        except Exception as exc:
            self.log(f"ERREUR micro : {exc}")
            self.show_error(32, "Erreur du microphone", "Le microphone ne peut pas démarrer.", "Vérifiez le micro choisi et les autorisations Windows.")

    def stop_recording(self) -> None:
        """Stop capture; the streaming worker drains the last audio and pastes once ready."""
        if not self.stream:
            return
        self.stream.stop()
        self.stream.close()
        self.stream = None
        self.recording = False
        self.diagnostic.set_recording(False)
        self.log("Finalisation des derniers segments en arriere-plan.")
        return
        if not self.stream:
            return
        self.stream.stop()
        self.stream.close()
        self.stream = None
        self.recording = False
        self.diagnostic.set_recording(False)
        chunks = []
        while not self.audio_chunks.empty():
            chunks.append(self.audio_chunks.get_nowait())
        if not chunks:
            self.show_error(48, "Pas de son entendu", "Aucun son n'est arrivé à l'application.", "Vérifiez que Microphone Array n'est pas coupé dans Paramètres > Son > Entrée.")
            return
        audio = np.concatenate(chunks, axis=0).reshape(-1)
        duration = len(audio) / self.capture_sample_rate
        rms = float(np.sqrt(np.mean(np.square(audio))))
        dbfs = 20 * np.log10(max(rms, 1e-10))
        self.log(f"Niveau microphone reçu : {dbfs:.1f} dBFS (plus grand que -45 dBFS en parlant est normal)")
        if dbfs < -55:
            self.show_error(48, "Pas de son entendu", "Le signal du microphone est presque silencieux.", "Vérifiez le volume et les autorisations dans Paramètres > Confidentialité > Microphone.")
            return
        audio = resample_for_whisper(audio, self.capture_sample_rate)
        self.transcribing = True
        self.set_status(f"Transcription locale en cours ({duration:.1f} s)…")
        threading.Thread(target=self.transcribe_and_paste, args=(audio,), daemon=True).start()

    def streaming_transcription_loop(self) -> None:
        """Transcribe 20-second slices while recording; retain a short overlap for word boundaries."""
        audio = np.empty(0, dtype=np.float32)
        next_end = int(STREAM_CHUNK_SECONDS * self.capture_sample_rate)
        overlap = int(STREAM_OVERLAP_SECONDS * self.capture_sample_rate)
        segment_index = 0
        try:
            while self.recording or not self.audio_chunks.empty():
                try:
                    chunk = self.audio_chunks.get(timeout=0.20)
                except queue.Empty:
                    continue
                audio = np.concatenate((audio, chunk.reshape(-1)))
                while len(audio) >= next_end:
                    start = max(0, next_end - int(STREAM_CHUNK_SECONDS * self.capture_sample_rate) - overlap)
                    segment_index += 1
                    self.process_stream_segment(audio[start:next_end], segment_index)
                    next_end += int(STREAM_CHUNK_SECONDS * self.capture_sample_rate)

            last_processed_end = next_end - int(STREAM_CHUNK_SECONDS * self.capture_sample_rate)
            final_start = max(0, last_processed_end - overlap)
            final_audio = audio[final_start:]
            if (len(audio) - last_processed_end) / self.capture_sample_rate >= MIN_FINAL_SEGMENT_SECONDS:
                segment_index += 1
                self.process_stream_segment(final_audio, segment_index)
            self.finish_streaming_session()
        except Exception as exc:
            technical = "".join(traceback.format_exception_only(type(exc), exc)).strip()
            self.ui(self.log, f"ERREUR transcription progressive : {technical}")
            self.ui(self.show_error, 121, "Erreur de transcription", "La transcription progressive n'a pas pu etre terminee.", "Communiquez le code et la reference technique si le probleme persiste.", technical)
            self.transcribing = False

    def preload_whisper_model(self) -> None:
        try:
            self.ensure_whisper_model()
            self.ui(self.log, "Whisper Small est pret pour la transcription.")
        except Exception as exc:
            self.ui(self.log, f"Prechargement de Whisper Small echoue : {exc}")

    def ensure_whisper_model(self) -> WhisperModel:
        with self.model_lock:
            if self.model is None:
                self.ui(self.log, "Chargement de Whisper Small en arriere-plan…")
                self.model = WhisperModel(MODEL_NAME, device="cpu", compute_type="int8")
            return self.model

    def process_stream_segment(self, audio: np.ndarray, index: int) -> None:
        model = self.ensure_whisper_model()
        duration = len(audio) / self.capture_sample_rate
        resampled = resample_for_whisper(audio, self.capture_sample_rate)
        self.ui(self.log, f"Transcription du segment {index} ({duration:.0f} s)…")
        segments, _info = model.transcribe(
            resampled, language=LANGUAGE, vad_filter=True, beam_size=5, initial_prompt=WHISPER_INITIAL_PROMPT,
        )
        raw = " ".join(segment.text.strip() for segment in segments).strip()
        if not raw:
            return
        with self.session_lock:
            previous = self.session_segments[-1] if self.session_segments else ""
        delta = remove_overlap_text(previous, raw)
        if not delta:
            return
        cleaned = self.clean_segment_with_llm(delta, index)
        with self.session_lock:
            self.session_segments.append(cleaned)
        self.ui(self.log, f"Segment {index} : {cleaned}")

    def clean_segment_with_llm(self, text: str, index: int) -> str:
        """Use Qwen locally when available; raw Whisper text remains the safe fallback."""
        if self.llm_available is False:
            return clean_text(text)
        self.ui(self.log, f"Mise en forme locale du segment {index}…")
        payload = json.dumps({
            "model": LLM_MODEL_NAME,
            "stream": False,
            "keep_alive": "5m",
            "options": {"temperature": 0, "num_ctx": 2048, "num_predict": 320},
            "messages": [{"role": "system", "content": CLEANUP_PROMPT}, {"role": "user", "content": text}],
        }).encode("utf-8")
        request = urllib.request.Request("http://127.0.0.1:11434/api/chat", data=payload, headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=90) as response:
                data = json.loads(response.read().decode("utf-8"))
            self.llm_available = True
            result = data.get("message", {}).get("content", "").strip()
            if is_cleanup_prompt_echo(result):
                self.ui(self.log, f"Qwen a renvoye sa consigne au segment {index}; texte Whisper conserve.")
                return clean_text(text)
            self.ui(self.log, f"Nettoyage local du segment {index} termine")
            return clean_text(result) if result else clean_text(text)
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            if self.llm_available is not False:
                self.ui(self.log, f"LLM local indisponible, texte Whisper conserve : {exc}")
            self.llm_available = False
            return clean_text(text)

    def finish_streaming_session(self) -> None:
        with self.session_lock:
            text = clean_text(" ".join(self.session_segments))
        if not text:
            self.ui(self.show_error, 48, "Pas de texte detecte", "Aucune parole exploitable n'a ete detectee.", "Verifiez le micro, son niveau et les autorisations Windows.")
            self.transcribing = False
            return
        pyperclip.copy(text + " ")
        if not activate_target(self.paste_target):
            target_name = self.paste_target.title if self.paste_target else "aucune cible"
            self.log(f"ERREUR collage : Windows n'a pas active la cible {target_name!r}.")
            self.ui(self.show_error, 121, "Erreur de collage", "Le texte est copie mais la fenetre cible n'a pas ete reactivee.", "Collez le texte manuellement avec Ctrl+V.")
            self.transcribing = False
            return
        time.sleep(FOCUS_SETTLE_SECONDS)
        self.paste_keyboard.press(keyboard.Key.ctrl)
        self.paste_keyboard.press("v")
        self.paste_keyboard.release("v")
        self.paste_keyboard.release(keyboard.Key.ctrl)
        self.ui(self.set_status, "Transcription ajoutee")
        self.transcribing = False

    def transcribe_and_paste(self, audio: np.ndarray) -> None:
        try:
            if self.model is None:
                self.ui(self.set_status, "Chargement de Whisper Base…")
                self.model = WhisperModel(MODEL_NAME, device="cpu", compute_type="int8")
            segments, info = self.model.transcribe(
                audio,
                language=LANGUAGE,
                vad_filter=True,
                beam_size=5,
                initial_prompt=WHISPER_INITIAL_PROMPT,
            )
            text = " ".join(segment.text.strip() for segment in segments).strip()
            if not text:
                self.ui(self.show_error, 48, "Pas de texte détecté", "Whisper n'a reconnu aucune parole exploitable.", "Parlez plus près du microphone et vérifiez que le niveau sonore bouge.")
                return
            text = clean_text(text)
            self.ui(self.log, f"Texte : {text}")
            pyperclip.copy(text + " ")
            if not activate_target(self.paste_target):
                self.ui(self.show_error, 121, "Erreur de collage", "Le texte a été copié, mais Windows n'a pas réactivé la fenêtre cible.", "Le texte est dans le presse-papiers : collez-le manuellement avec Ctrl+V.")
                return
            target_title = self.paste_target.title if self.paste_target else "la fenêtre cible"
            self.ui(self.log, f"Fenêtre réactivée : {target_title}")
            time.sleep(FOCUS_SETTLE_SECONDS)
            self.paste_keyboard.press(keyboard.Key.ctrl)
            self.paste_keyboard.press("v")
            self.paste_keyboard.release("v")
            self.paste_keyboard.release(keyboard.Key.ctrl)
            self.ui(self.set_status, f"✓ Transcription collée dans : {target_title}")
        except Exception as exc:
            details = "".join(traceback.format_exception_only(type(exc), exc)).strip()
            self.ui(self.log, f"ERREUR transcription : {details}")
            self.ui(self.show_error, 121, "Erreur de transcription", "La transcription n'a pas pu être terminée.", "Cliquez sur ? pour copier le diagnostic et me l'envoyer.")
        finally:
            self.transcribing = False

    def close(self) -> None:
        if self.stream:
            self.stream.close()
        if self.listener:
            self.listener.stop()
        if WEB_UI:
            if self.web_server:
                threading.Thread(target=self.web_server.shutdown, daemon=True).start()
            return
        self.error_overlay.close()
        if self.diagnostic.window.winfo_exists(): self.diagnostic.window.destroy()
        if self.toast.window.winfo_exists(): self.toast.window.destroy()
        if self.root.winfo_exists(): self.root.destroy()

    def run(self) -> None:
        if WEB_UI:
            self.run_web_server()
            return
        self.root.after(0, self.on_ui_ready)
        self.root.mainloop()


def clean_text(text: str) -> str:
    """Reserved for the future local Gemma cleanup step; keeps raw transcription intact for now."""
    return " ".join(text.split())


def remove_overlap_text(previous: str, current: str) -> str:
    """Drop the repeated prefix introduced by the audio overlap, without rewriting speech."""
    previous_words = previous.split()
    current_words = current.split()
    max_overlap = min(30, len(previous_words), len(current_words))
    for size in range(max_overlap, 2, -1):
        if [word.casefold().strip(".,!?;:") for word in previous_words[-size:]] == [word.casefold().strip(".,!?;:") for word in current_words[:size]]:
            return " ".join(current_words[size:])
    return current


def is_cleanup_prompt_echo(text: str) -> bool:
    """Never append Qwen's system prompt as if it were dictated speech."""
    normalized = text.casefold()
    markers = ("conserve le style", "ne resume pas", "retourne uniquement le texte", "erreurs manifestes")
    return sum(marker in normalized for marker in markers) >= 2


def short_toast_text(value: str) -> str:
    """Keep the 296px status tag readable while technical detail stays in the overlay."""
    lower = value.lower()
    if "chargement" in lower:
        return "Chargement du modèle..."
    if "transcription" in lower:
        return "Transcription..."
    if "cible" in lower:
        return "Préparation de la note..."
    return value[:32] + ("..." if len(value) > 32 else "")


def resample_for_whisper(audio: np.ndarray, source_rate: int) -> np.ndarray:
    """Small dependency-free mono resampler; Whisper expects 16 kHz float audio."""
    if source_rate == WHISPER_SAMPLE_RATE:
        return audio
    target_length = round(len(audio) * WHISPER_SAMPLE_RATE / source_rate)
    source_positions = np.arange(len(audio), dtype=np.float64)
    target_positions = np.linspace(0, len(audio) - 1, target_length)
    return np.interp(target_positions, source_positions, audio).astype(np.float32)


def window_title(hwnd: int) -> str:
    """Read a top-level Windows window title without needing an extra dependency."""
    length = ctypes.windll.user32.GetWindowTextLengthW(hwnd)
    buffer = ctypes.create_unicode_buffer(length + 1)
    ctypes.windll.user32.GetWindowTextW(hwnd, buffer, len(buffer))
    return buffer.value or "Fenêtre sans titre"


def document_type(title: str) -> str:
    lower = title.casefold()
    if "onenote" in lower: return "onenote"
    if "word" in lower or "winword" in lower: return "word"
    if "pdf" in lower or "acrobat" in lower: return "pdf"
    return "document"


def list_open_document_windows() -> list[PasteTarget]:
    """List document-like top-level windows; their text is added only when the user supplies it."""
    user32 = ctypes.windll.user32
    results: list[PasteTarget] = []
    callback_type = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
    def visit(hwnd, _lparam):
        if not user32.IsWindowVisible(hwnd): return True
        title = window_title(hwnd)
        if title and title != "Fenêtre sans titre" and not "voice notes" in title.casefold():
            kind = document_type(title)
            if kind != "document" or "chrome" in title.casefold() or "edge" in title.casefold():
                results.append(PasteTarget(hwnd=hwnd, title=title, desktop_number=None))
        return True
    user32.EnumWindows(callback_type(visit), 0)
    return results


def remember_active_window() -> PasteTarget | None:
    hwnd = ctypes.windll.user32.GetForegroundWindow()
    if not hwnd:
        return None
    try:
        desktop_number = AppView(hwnd=hwnd).desktop.number
    except Exception:
        # Standard paste still works if Windows does not expose a desktop for this window.
        desktop_number = None
    return PasteTarget(hwnd=hwnd, title=window_title(hwnd), desktop_number=desktop_number)


def activate_target(target: PasteTarget | None) -> bool:
    """Restore the target (if minimized), bring it forward and confirm Windows accepted it."""
    if target is None or not ctypes.windll.user32.IsWindow(target.hwnd):
        return False
    user32 = ctypes.windll.user32
    SW_RESTORE = 9
    if target.desktop_number is not None:
        current_desktop = VirtualDesktop.current()
        if current_desktop.number != target.desktop_number:
            VirtualDesktop(number=target.desktop_number).go()
            # Let Windows display the target desktop before attempting to activate its window.
            time.sleep(FOCUS_SETTLE_SECONDS)
    if user32.IsIconic(target.hwnd):
        user32.ShowWindow(target.hwnd, SW_RESTORE)
    foreground = user32.GetForegroundWindow()
    foreground_thread = user32.GetWindowThreadProcessId(foreground, None) if foreground else 0
    target_thread = user32.GetWindowThreadProcessId(target.hwnd, None)
    attached = bool(foreground_thread and target_thread and foreground_thread != target_thread and user32.AttachThreadInput(foreground_thread, target_thread, True))
    try:
        user32.BringWindowToTop(target.hwnd)
        user32.SetForegroundWindow(target.hwnd)
        user32.SetFocus(target.hwnd)
    finally:
        if attached:
            user32.AttachThreadInput(foreground_thread, target_thread, False)
    for _attempt in range(8):
        if user32.GetForegroundWindow() == target.hwnd:
            return True
        time.sleep(0.05)
        user32.BringWindowToTop(target.hwnd)
        user32.SetForegroundWindow(target.hwnd)
    return False


if __name__ == "__main__":
    VoiceNotesApp().run()
