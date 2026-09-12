"""
SmartScheduler GUI — Redesign v3
=================================
Interfaccia moderna con CustomTkinter ispirata all'app React:
  - Welcome screen con file picker (navigazione cartelle)
  - Pipeline verticale a nodi con status animati e log in tempo reale
  - Diagnostica infeasibility con proposte cliccabili
  - Griglia turni con colori, legenda, hover effects, scrollbar
  - Fairness panel con metric cards
  - Floating timer mm:ss durante l'esecuzione
"""

import sys
import os

if sys.platform.startswith("win"):
    try:
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("unical.smartscheduler.hospital.scheduler.v1")
    except Exception:
        pass

import threading
import queue
import time
from pathlib import Path
from typing import List, Optional, Dict, Any
import tkinter as tk
from tkinter import filedialog, messagebox
from PIL import Image, ImageDraw, ImageTk, ImageFont

import customtkinter as ctk

PROJECT_ROOT = Path(__file__).resolve().parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from config_loader import load_config
from calendar_manager import SchedulingHorizon
from worker_agent import FormalizedWorkerProfile, WorkerAgent
from drafting_agent import ScheduleDraftingAgent
from verification_agent import HardConstraintVerificationAgent, SymbolicFairnessVerificationAgent
from refinement_loop import ScheduleRefinementAgent


# ─────────────────────────────────────────────
# Palette colori dark-mode premium (Nero puro + grigio scuro neutro)
# ─────────────────────────────────────────────
# ─────────────────────────────────────────────
# Palette colori dark-mode premium (Tonalità coerenti su scala Zinc/Argento, zero bianco, zero azzurro)
# ─────────────────────────────────────────────
C = {
    "bg":            "#080808",       # Nero profondo (nessuna sfumatura blu)
    "surface":       "#141414",       # Grigio scurissimo per celle/card (contrasto elegante)
    "surface2":      "#1E1E1E",       # Grigio scuro per box interni
    "surface3":      "#282828",       # Grigio pulsanti/hover
    "border":        "#242424",       # Bordo sottile neutro
    "border_light":  "#363636",       # Bordo evidenziato
    "text":          "#D4D4D8",       # Argento/Zinco chiaro per titoli/testi primari (coerente, non bianco)
    "text_body":     "#A1A1AA",       # Zinco medio per testi di corpo
    "text_muted":    "#71717A",       # Zinco secondario per etichette, tradeoff e puntini elenchi
    "text_dim":      "#52525B",       # Zinco profondo per dettagli e note
    "accent":        "#10B981",       # Smeraldo elegante
    "accent_hover":  "#059669",
    "accent_glow":   "#047857",
    "green":         "#10B981",
    "green_soft":    "#064E3B",
    "green_bright":  "#34D399",
    "yellow":        "#F59E0B",
    "yellow_soft":   "#451A03",
    "red":           "#EF4444",
    "red_soft":      "#450A0A",
    "purple":        "#8B5CF6",
    "purple_soft":   "#2E1065",
    "cyan":          "#A1A1AA",       # Unificato alla scala neutra (nessun azzurro)
    "morning_fg":    "#D4D4D8",       # Tonalità coerente ad alta intensità
    "morning_bg":    "#141414",
    "afternoon_fg":  "#A1A1AA",       # Tonalità coerente a media intensità
    "afternoon_bg":  "#141414",
    "night_fg":      "#71717A",       # Tonalità coerente a bassa intensità
    "night_bg":      "#141414",
    "rest_fg":       "#3F3F46",
    "rest_bg":       "#141414",
    "holiday_border":"#71717A",
    "line_idle":     "#222222",       # Grigio scuro neutro linea inattiva
    "line_done":     "#10B981",
    "line_error":    "#EF4444",
}

# Tipografia arrotondata moderna
FONT_TITLE = "Century Gothic"
FONT_BODY = "Segoe UI"
FONT_MONO = "Consolas"

SHIFT_ABBR   = {0: "M", 1: "P", 2: "N", None: "–"}
SHIFT_FULL   = {0: "Mattina", 1: "Pomeriggio", 2: "Notte", None: "Riposo"}
SHIFT_FG     = {0: C["morning_fg"], 1: C["afternoon_fg"], 2: C["night_fg"], None: C["rest_fg"]}
SHIFT_BG     = {0: C["morning_bg"], 1: C["afternoon_bg"], 2: C["night_bg"], None: C["rest_bg"]}

# Agent metadata (fasi di pipeline numerate senza emoji + descrizioni)
AGENT_META = {
    "config":     {"icon": "01", "label": "Configurazione",        "desc": "Caricamento parametri da file di config"},
    "workers":    {"icon": "02", "label": "Workers Agent",          "desc": "Parsing preferenze dal testo libero via LLM"},
    "drafting":   {"icon": "03", "label": "Drafting Agent",         "desc": "Costruzione e risoluzione modello CP-SAT"},
    "quality":    {"icon": "04", "label": "Quality Gate",           "desc": "Validazione vincoli hard e analisi equità"},
    "verify":     {"icon": "05", "label": "Verification Agent",     "desc": "Verifica conformità Hard Constraints"},
    "fairness":   {"icon": "06", "label": "Fairness Agent",        "desc": "Analisi equità con Gini Index"},
    "refinement": {"icon": "07", "label": "Refinement Loop",       "desc": "Loop iterativo di ottimizzazione fairness"},
    "replanning": {"icon": "08", "label": "Dynamic Replanning",    "desc": "Diagnostica e negoziazione vincoli per risolvibilità"},
}


# ─────────────────────────────────────────────
# Helper: Generatori di forme anti-alias con supersampling (Zero bordi frastagliati)
# ─────────────────────────────────────────────
_SMOOTH_IMAGE_CACHE: Dict[tuple, Any] = {}

def get_smooth_circle_image(size: int, fill_color: str, outline_color: str = None, outline_width: int = 2, bg_color: str = "#080808"):
    """Genera un'immagine circolare con anti-aliasing tramite supersampling 4x."""
    key = ("circle", size, fill_color, outline_color, outline_width, bg_color)
    if key in _SMOOTH_IMAGE_CACHE:
        return _SMOOTH_IMAGE_CACHE[key]
    scale = 4
    s = size * scale
    pad = outline_width * scale
    im = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    draw = ImageDraw.Draw(im)
    draw.ellipse([pad, pad, s - pad - 1, s - pad - 1], fill=fill_color, outline=outline_color, width=outline_width * scale)
    im = im.resize((size, size), Image.Resampling.LANCZOS)
    bg = Image.new("RGBA", (size, size), bg_color)
    combined = Image.alpha_composite(bg, im)
    tk_img = ImageTk.PhotoImage(combined)
    _SMOOTH_IMAGE_CACHE[key] = tk_img
    return tk_img

def _hex_to_rgb(h: str) -> tuple:
    h = h.lstrip('#')
    return tuple(int(h[i:i+2], 16) for i in (0, 2, 4))

def get_smooth_circle_icon_image(size: int, icon_text: str, fill_color: str, outline_color: str = None, outline_width: int = 2, bg_color: str = "#080808"):
    """Genera un'icona circolare con raffinati effetti di trasparenza glassmorphic e centratura perfetta."""
    key = ("circle_icon_glass", size, icon_text, fill_color, outline_color, outline_width, bg_color)
    if key in _SMOOTH_IMAGE_CACHE:
        return _SMOOTH_IMAGE_CACHE[key]
    scale = 4
    s = size * scale
    im = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    draw = ImageDraw.Draw(im)

    col = outline_color or fill_color
    r, g, b = _hex_to_rgb(col)

    # 1. Outer soft glowing halo (transparency effect)
    glow_pad = 2 * scale
    draw.ellipse([glow_pad, glow_pad, s - glow_pad - 1, s - glow_pad - 1],
                 fill=(r, g, b, 24), outline=(r, g, b, 70), width=1 * scale)

    # 2. Main translucent glass body (semi-transparent tinted glass)
    body_pad = 4 * scale
    draw.ellipse([body_pad, body_pad, s - body_pad - 1, s - body_pad - 1],
                 fill=(r, g, b, 50), outline=(r, g, b, 170), width=outline_width * scale)

    # 3. Upper glass specular highlight reflection
    shine_pad = 7 * scale
    draw.arc([shine_pad, shine_pad, s - shine_pad - 1, s - shine_pad - 1],
             start=200, end=340, fill=(255, 255, 255, 80), width=int(1.5 * scale))

    # 4. Text / Glyph inside in coherent soft silver/zinc (no harsh pure white)
    if icon_text:
        try:
            font_size = int(size * 0.36 * scale)
            try:
                font = ImageFont.truetype("consola.ttf", font_size)
            except Exception:
                try:
                    font = ImageFont.truetype("segoeui.ttf", font_size)
                except Exception:
                    font = ImageFont.load_default()

            bbox = draw.textbbox((0, 0), icon_text, font=font)
            bw = bbox[2] - bbox[0]
            bh = bbox[3] - bbox[1]
            tx = (s - bw) / 2.0 - bbox[0]
            ty = (s - bh) / 2.0 - bbox[1]
            draw.text((tx, ty), icon_text, font=font, fill=(212, 212, 216, 230))
        except Exception:
            pass

    im = im.resize((size, size), Image.Resampling.LANCZOS)
    bg = Image.new("RGBA", (size, size), bg_color)
    combined = Image.alpha_composite(bg, im)
    tk_img = ImageTk.PhotoImage(combined)
    _SMOOTH_IMAGE_CACHE[key] = tk_img
    return tk_img

_AVATAR_CACHE: Dict[tuple, Any] = {}
def get_avatar_image(worker_idx: int, bg_canvas_color: str = "#0D0D0D"):
    """Genera un avatar elegante arrotondato in stile chip silhouette come da mockup di riferimento."""
    key = (worker_idx, bg_canvas_color)
    if key in _AVATAR_CACHE:
        return _AVATAR_CACHE[key]
    
    size = 28
    scale = 3
    s = size * scale
    im = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    draw = ImageDraw.Draw(im)
    
    avatar_colors = [
        ("#2A3342", "#4B6B94"),
        ("#382A42", "#8F5E9E"),
        ("#2A423B", "#4E9E82"),
        ("#423B2A", "#B59E52"),
        ("#422A2A", "#B55252"),
        ("#2B2E33", "#6C727F"),
        ("#1F383D", "#438A99"),
        ("#3D2B3D", "#995B99"),
        ("#3D352B", "#997F43"),
        ("#2B3D2B", "#5B995B"),
        ("#352B3D", "#7F5B99"),
        ("#3D2B33", "#995B72"),
        ("#2B383D", "#5B8599")
    ]
    bg_c, fg_c = avatar_colors[worker_idx % len(avatar_colors)]
    
    draw.rounded_rectangle([0, 0, s - 1, s - 1], radius=6 * scale, fill=bg_c)
    cx, cy = s // 2, s // 2
    head_r = 7 * scale
    draw.ellipse([cx - head_r, cy - 8 * scale - head_r, cx + head_r, cy - 8 * scale + head_r], fill=fg_c)
    draw.ellipse([cx - 13 * scale, cy + 2 * scale, cx + 13 * scale, cy + 26 * scale], fill=fg_c)
    
    im = im.resize((size, size), Image.Resampling.LANCZOS)
    bg_out = Image.new("RGBA", (size, size), bg_canvas_color)
    combined = Image.alpha_composite(bg_out, im)
    tk_img = ImageTk.PhotoImage(combined)
    _AVATAR_CACHE[key] = tk_img
    return tk_img

def get_smooth_pill_image(width: int, height: int, radius: int, fill_color: str, outline_color: str = None, outline_width: int = 1, bg_color: str = "#141414"):
    """Genera una pillola arrotondata con effetto trasparenza vitrea anti-alias."""
    key = ("pill_glass", width, height, radius, fill_color, outline_color, outline_width, bg_color)
    if key in _SMOOTH_IMAGE_CACHE:
        return _SMOOTH_IMAGE_CACHE[key]
    scale = 4
    w = width * scale
    h = height * scale
    r = radius * scale
    pad = outline_width * scale
    im = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(im)

    col = outline_color or fill_color
    rc, gc, bc = _hex_to_rgb(col)

    # Translucent glass body + tinted border
    draw.rounded_rectangle([pad, pad, w - pad - 1, h - pad - 1], radius=r,
                           fill=(rc, gc, bc, 42), outline=(rc, gc, bc, 140), width=outline_width * scale)
    im = im.resize((width, height), Image.Resampling.LANCZOS)
    bg = Image.new("RGBA", (width, height), bg_color)
    combined = Image.alpha_composite(bg, im)
    tk_img = ImageTk.PhotoImage(combined)
    _SMOOTH_IMAGE_CACHE[key] = tk_img
    return tk_img

def get_shift_tile(text: str, bg_color: str, fg_color: str, is_holiday: bool = False, is_hover: bool = False, bg_canvas_color: str = "#141414"):
    """Genera una tile di turno arrotondata e anti-alias con supersampling (Zero pixel scalettati)."""
    cell_w, cell_h = 44, 32
    key = ("shift_tile", text, bg_color, fg_color, is_holiday, is_hover, bg_canvas_color)
    if key in _SMOOTH_IMAGE_CACHE:
        return _SMOOTH_IMAGE_CACHE[key]

    scale = 3
    w = cell_w * scale
    h = cell_h * scale
    r = 7 * scale
    im = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(im)

    outline = C["holiday_border"] if is_holiday else (C["border_light"] if is_hover else None)
    out_w = (2 * scale) if is_holiday else (1 * scale if is_hover else 0)

    draw.rounded_rectangle([out_w, out_w, w - out_w - 1, h - out_w - 1], radius=r, fill=bg_color, outline=outline, width=out_w)
    im = im.resize((cell_w, cell_h), Image.Resampling.LANCZOS)

    bg_canvas = Image.new("RGBA", (cell_w, cell_h), bg_canvas_color)
    combined = Image.alpha_composite(bg_canvas, im)

    draw_res = ImageDraw.Draw(combined)
    try:
        from PIL import ImageFont
        fnt = ImageFont.truetype("seguisb.ttf", 15)
    except Exception:
        fnt = None

    bbox = draw_res.textbbox((0, 0), text, font=fnt) if fnt else (0, 0, 10, 10)
    tw = bbox[2] - bbox[0]
    th = bbox[3] - bbox[1]
    tx = (cell_w - tw) // 2
    ty = (cell_h - th) // 2 - 1
    draw_res.text((tx, ty), text, fill=fg_color, font=fnt)

    tk_img = ImageTk.PhotoImage(combined)
    _SMOOTH_IMAGE_CACHE[key] = tk_img
    return tk_img


def _apply_win32_icon_handles(root: ctk.CTk, ico_path_str: str) -> None:
    """Invia esplicitamente i messaggi WM_SETICON (ICON_BIG e ICON_SMALL) tramite Win32 API per la taskbar di Windows."""
    try:
        import ctypes
        from ctypes import wintypes

        WM_SETICON = 0x0080
        ICON_SMALL = 0
        ICON_BIG = 1
        IMAGE_ICON = 1
        LR_LOADFROMFILE = 0x0010
        LR_DEFAULTSIZE = 0x0040

        LoadImageW = ctypes.windll.user32.LoadImageW
        LoadImageW.argtypes = [wintypes.HINSTANCE, wintypes.LPCWSTR, wintypes.UINT, ctypes.c_int, ctypes.c_int, wintypes.UINT]
        LoadImageW.restype = wintypes.HANDLE

        SendMessageW = ctypes.windll.user32.SendMessageW
        SendMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
        SendMessageW.restype = wintypes.LPARAM

        # Carica l'icona a 32x32 per la barra delle applicazioni (ICON_BIG) e 16x16 per la barra del titolo (ICON_SMALL)
        hicon_big = LoadImageW(None, ico_path_str, IMAGE_ICON, 32, 32, LR_LOADFROMFILE)
        if not hicon_big:
            hicon_big = LoadImageW(None, ico_path_str, IMAGE_ICON, 0, 0, LR_LOADFROMFILE | LR_DEFAULTSIZE)
        hicon_small = LoadImageW(None, ico_path_str, IMAGE_ICON, 16, 16, LR_LOADFROMFILE)

        hwnd = root.winfo_id()
        parent_hwnd = ctypes.windll.user32.GetParent(hwnd)
        hwnds = [h for h in (hwnd, parent_hwnd) if h]

        for h in hwnds:
            if hicon_big:
                SendMessageW(h, WM_SETICON, ICON_BIG, hicon_big)
            if hicon_small:
                SendMessageW(h, WM_SETICON, ICON_SMALL, hicon_small)
    except Exception:
        pass


def apply_app_icon(root: ctk.CTk) -> None:
    """Configura l'icona dell'applicazione per la finestra e la barra delle applicazioni (Windows / Linux / macOS)."""
    if sys.platform.startswith("win"):
        try:
            import ctypes
            app_id = "unical.smartscheduler.hospital.scheduler.v1"
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(app_id)
        except Exception:
            pass

    icon_ico = None
    icon_png = None
    search_dirs = [
        PROJECT_ROOT / "assets",
        PROJECT_ROOT.parent / "assets",
        Path.cwd() / "assets",
        Path.cwd() / "Stages" / "assets",
    ]
    for d in search_dirs:
        ico = d / "app_icon.ico"
        png = d / "app_icon.png"
        if ico.is_file() and icon_ico is None:
            icon_ico = ico
        if png.is_file() and icon_png is None:
            icon_png = png

    # ── WINDOWS ─────────────────────────────────────────────────────────────
    if sys.platform.startswith("win"):
        # 1. Imposta iconphoto (aggiorna la Taskbar di Windows istantaneamente)
        if icon_png and icon_png.is_file():
            try:
                photo = ImageTk.PhotoImage(Image.open(str(icon_png.resolve())))
                root.iconphoto(True, photo)
                root._app_icon_photo_ref = photo
            except Exception:
                pass

        # 2. Imposta iconbitmap e Win32 HICON
        if icon_ico and icon_ico.is_file():
            ico_path_str = str(icon_ico.resolve())
            try:
                root.iconbitmap(ico_path_str)
            except Exception:
                try:
                    root.iconbitmap(default=ico_path_str)
                except Exception:
                    pass

            try:
                root.update_idletasks()
            except Exception:
                pass
            _apply_win32_icon_handles(root, ico_path_str)
            try:
                root.after(150, lambda: _apply_win32_icon_handles(root, ico_path_str))
            except Exception:
                pass

    # ── LINUX / MACOS ───────────────────────────────────────────────────────
    else:
        if icon_png and icon_png.is_file():
            try:
                photo = ImageTk.PhotoImage(Image.open(str(icon_png)))
                root.iconphoto(True, photo)
                root._app_icon_photo_ref = photo
            except Exception:
                pass
        elif icon_ico and icon_ico.is_file():
            try:
                root.iconbitmap(str(icon_ico))
            except Exception:
                pass


# ─────────────────────────────────────────────
# Main GUI class
# ─────────────────────────────────────────────
class SmartSchedulerGUI:
    TIME_LIMIT = 20

    def __init__(self, root: ctk.CTk):
        self.root = root
        apply_app_icon(self.root)
        self.root.title("SmartScheduler — Turni Ospedalieri")
        try:
            sw = self.root.winfo_screenwidth()
            sh = self.root.winfo_screenheight()
            init_w = min(1560, max(1180, sw - 80))
            init_h = min(920, max(700, sh - 80))
            self.root.geometry(f"{init_w}x{init_h}")
        except Exception:
            self.root.geometry("1400x880")
        self.root.minsize(1024, 660)

        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("blue")

        # State
        self.log_queue: queue.Queue = queue.Queue()
        self.result_data: Dict[str, Any] = {}
        self.txt_file = tk.StringVar(value="")
        self.use_case = tk.StringVar(value="A")
        self._running = False
        self._start_time = 0.0
        self._timer_id = None
        self._pipeline_nodes: List[Dict] = []

        # Infeasibility interactive state
        self._proposal_event = threading.Event()
        self._selected_proposal_workers: Optional[List[str]] = None

        # Build UI
        self._build_welcome_screen()
        self._poll_log_queue()

    # ═══════════════════════════════════════════
    #  WELCOME SCREEN
    # ═══════════════════════════════════════════
    def _build_welcome_screen(self):
        """Initial screen with file picker and Use Case selector."""
        self.welcome_frame = ctk.CTkFrame(self.root, fg_color=C["bg"])
        self.welcome_frame.pack(fill="both", expand=True)

        # Center container
        center = ctk.CTkFrame(self.welcome_frame, fg_color=C["bg"])
        center.place(relx=0.5, rely=0.5, anchor="center")

        # Logo / Title
        logo_frame = ctk.CTkFrame(center, fg_color=C["bg"])
        logo_frame.pack(pady=(0, 8))

        ctk.CTkLabel(
            logo_frame, text="🏥",
            font=ctk.CTkFont(size=64),
            text_color=C["accent"]
        ).pack()

        ctk.CTkLabel(
            center, text="SmartScheduler",
            font=ctk.CTkFont(family=FONT_TITLE, size=46, weight="bold"),
            text_color=C["text"]
        ).pack(pady=(4, 0))

        ctk.CTkLabel(
            center,
            text="Sistema multi-agente per la generazione di turnazioni ospedaliere",
            font=ctk.CTkFont(family=FONT_BODY, size=17),
            text_color=C["text_muted"]
        ).pack(pady=(4, 36))

        # ── File picker card ──────────────────
        file_card = ctk.CTkFrame(center, fg_color=C["surface"], corner_radius=16, border_width=1, border_color=C["border"])
        file_card.pack(padx=20, pady=(0, 20), fill="x")

        inner = ctk.CTkFrame(file_card, fg_color="transparent")
        inner.pack(padx=28, pady=24, fill="x")

        ctk.CTkLabel(
            inner, text="File Preferenze Lavoratori",
            font=ctk.CTkFont(family=FONT_TITLE, size=20, weight="bold"),
            text_color=C["text"]
        ).pack(anchor="w")

        ctk.CTkLabel(
            inner,
            text="Seleziona il file .txt contenente le preferenze in linguaggio naturale",
            font=ctk.CTkFont(family=FONT_BODY, size=15),
            text_color=C["text_muted"]
        ).pack(anchor="w", pady=(2, 14))

        file_row = ctk.CTkFrame(inner, fg_color="transparent")
        file_row.pack(fill="x")

        self.file_entry = ctk.CTkEntry(
            file_row,
            textvariable=self.txt_file,
            placeholder_text="Nessun file selezionato...",
            height=52,
            corner_radius=10,
            fg_color=C["surface2"],
            border_color=C["border"],
            text_color=C["text"],
            font=ctk.CTkFont(family=FONT_BODY, size=15)
        )
        self.file_entry.pack(side="left", fill="x", expand=True, padx=(0, 10))

        ctk.CTkButton(
            file_row, text="Sfoglia",
            width=150, height=52,
            corner_radius=10,
            fg_color=C["surface3"],
            hover_color=C["border_light"],
            text_color=C["text"],
            font=ctk.CTkFont(family=FONT_TITLE, size=15, weight="bold"),
            command=self._browse_file
        ).pack(side="right")

        # ── Config card (Use Case only) ───────
        config_card = ctk.CTkFrame(center, fg_color=C["surface"], corner_radius=16, border_width=1, border_color=C["border"])
        config_card.pack(padx=20, pady=(0, 24), fill="x")

        cfg_inner = ctk.CTkFrame(config_card, fg_color="transparent")
        cfg_inner.pack(padx=28, pady=24, fill="x")

        ctk.CTkLabel(
            cfg_inner, text="Configurazione",
            font=ctk.CTkFont(family=FONT_TITLE, size=20, weight="bold"),
            text_color=C["text"]
        ).pack(anchor="w", pady=(0, 16))

        cfg_row = ctk.CTkFrame(cfg_inner, fg_color="transparent")
        cfg_row.pack(fill="x")

        # Use Case
        uc_frame = ctk.CTkFrame(cfg_row, fg_color="transparent")
        uc_frame.pack(side="left", padx=(0, 32))
        ctk.CTkLabel(uc_frame, text="Use Case", font=ctk.CTkFont(family=FONT_BODY, size=15),
                     text_color=C["text_muted"]).pack(anchor="w")
        self.uc_seg = ctk.CTkSegmentedButton(
            uc_frame, values=["A", "B"],
            variable=self.use_case,
            font=ctk.CTkFont(family=FONT_TITLE, size=16, weight="bold"),
            selected_color=C["accent"],
            selected_hover_color=C["accent_hover"],
            unselected_color=C["surface2"],
            unselected_hover_color=C["surface3"],
            corner_radius=8,
            height=48
        )
        self.uc_seg.pack(pady=(6, 0))

        # Info labels
        info_frame = ctk.CTkFrame(cfg_row, fg_color="transparent")
        info_frame.pack(side="left")
        ctk.CTkLabel(info_frame, text="Il loop di raffinamento itera fino alla convergenza ottimale",
                     font=ctk.CTkFont(family=FONT_BODY, size=14), text_color=C["text_dim"]).pack(anchor="w")
        ctk.CTkLabel(info_frame, text="senza alcun limite massimo fisso di iterazioni.",
                     font=ctk.CTkFont(family=FONT_BODY, size=14), text_color=C["text_dim"]).pack(anchor="w")

        # ── Start button ──────────────────────
        self.start_btn = ctk.CTkButton(
            center, text="AVVIA PIPELINE",
            width=380, height=64,
            corner_radius=14,
            fg_color=C["accent"],
            hover_color=C["accent_hover"],
            text_color=C["text"],
            font=ctk.CTkFont(family=FONT_TITLE, size=22, weight="bold"),
            command=self._start_pipeline
        )
        self.start_btn.pack(pady=(8, 16))

        # Footer
        ctk.CTkLabel(
            center,
            text="Progetto per il corso di Intelligenza Artificiale  ·  UNICAL — A.A. 2025/2026",
            font=ctk.CTkFont(family=FONT_BODY, size=14),
            text_color=C["text_dim"]
        ).pack(pady=(8, 0))

    # ═══════════════════════════════════════════
    #  PIPELINE VIEW
    # ═══════════════════════════════════════════
    def _build_pipeline_view(self):
        """Replace welcome screen with pipeline execution view."""
        self.welcome_frame.destroy()

        self.main_frame = ctk.CTkFrame(self.root, fg_color=C["bg"])
        self.main_frame.pack(fill="both", expand=True)

        # ── Top bar ───────────────────────────
        topbar = ctk.CTkFrame(self.main_frame, fg_color=C["surface"], height=70, corner_radius=0)
        topbar.pack(fill="x")
        topbar.pack_propagate(False)

        ctk.CTkLabel(
            topbar, text="SmartScheduler",
            font=ctk.CTkFont(family=FONT_TITLE, size=22, weight="bold"),
            text_color=C["text"]
        ).pack(side="left", padx=24)

        uc_val = self.use_case.get()
        ctk.CTkLabel(
            topbar,
            text=f"Use Case {uc_val}  ·  Pipeline in esecuzione",
            font=ctk.CTkFont(family=FONT_BODY, size=15),
            text_color=C["text_muted"]
        ).pack(side="left", padx=8)

        # Timer
        self.timer_label = ctk.CTkLabel(
            topbar, text="00:00",
            font=ctk.CTkFont(family=FONT_MONO, size=20, weight="bold"),
            text_color=C["yellow"]
        )
        self.timer_label.pack(side="right", padx=24)

        # Status
        self.status_label = ctk.CTkLabel(
            topbar, text="In esecuzione",
            font=ctk.CTkFont(family=FONT_TITLE, size=16, weight="bold"),
            text_color=C["yellow"]
        )
        self.status_label.pack(side="right", padx=20)

        # ── Scrollable pipeline area ──────────
        self.scroll_frame = ctk.CTkScrollableFrame(
            self.main_frame, fg_color=C["bg"],
            scrollbar_button_color=C["surface3"],
            scrollbar_button_hover_color=C["border_light"],
        )
        self.scroll_frame.pack(fill="both", expand=True, padx=0, pady=0)

        # Inner centered container (responsive, expands to fit full width)
        self.pipeline_container = ctk.CTkFrame(self.scroll_frame, fg_color=C["bg"])
        self.pipeline_container.pack(padx=28, pady=24, fill="x", expand=True)

        self._pipeline_nodes = []

    # ─────────────────────────────────────────
    # Agent Node Widget
    # ─────────────────────────────────────────
    def _add_agent_node(self, agent_id: str, extra_label: str = "") -> Dict:
        """Add a pipeline node for the given agent. Returns a dict with widget references."""
        meta = AGENT_META.get(agent_id, {"icon": "--", "label": agent_id, "desc": ""})
        label = meta["label"]
        if extra_label:
            label += f" {extra_label}"

        node_frame = ctk.CTkFrame(self.pipeline_container, fg_color="transparent")
        node_frame.pack(fill="x", pady=(0, 0))

        # Row: icon circle + content
        row = tk.Frame(node_frame, bg=C["bg"])
        row.pack(fill="x")

        # ── Left: icon column (continuous vertical pipeline line) ──────
        icon_col = tk.Frame(row, bg=C["bg"], width=60)
        icon_col.pack(side="left", fill="y", anchor="n")

        # Icon circle (Anti-aliased PIL supersampling ensures silky-smooth border and perfect sub-pixel centering)
        circle_canvas = tk.Canvas(icon_col, width=60, height=52, bg=C["bg"], highlightthickness=0)
        circle_canvas.pack(side="top")

        init_circ_img = get_smooth_circle_icon_image(48, meta["icon"], C["accent"], C["accent_glow"], 2, C["bg"])
        circle_canvas.create_image(30, 26, image=init_circ_img, anchor="center", tags="circle_img")
        circle_canvas._circ_img = init_circ_img

        class CircleCanvasWrapper:
            def __init__(self, c, icon_txt, bg=C["bg"]):
                self._c = c
                self._icon_txt = icon_txt
                self._bg = bg
            def configure(self, fg_color=None, border_color=None, **kw):
                if fg_color:
                    img = get_smooth_circle_icon_image(48, self._icon_txt, fg_color, border_color or fg_color, 2, self._bg)
                    self._c.itemconfig("circle_img", image=img)
                    self._c._circ_img = img

        icon_circle = CircleCanvasWrapper(circle_canvas, meta["icon"], C["bg"])

        # ── Vertical line (connector) — dynamically stretches to full node height ──
        line_canvas = tk.Canvas(icon_col, width=60, height=1, bg=C["bg"], highlightthickness=0)
        line_canvas.pack(side="top", fill="both", expand=True)
        line_canvas.create_line(30, 0, 30, 5000, fill=C["line_idle"], width=2, tags="connector")

        # ── Right: content card with translucent styling ──────
        content = ctk.CTkFrame(row, fg_color=C["surface"], corner_radius=16,
                               border_width=1, border_color=C["border"])
        content.pack(side="left", fill="x", expand=True, padx=(16, 0), pady=(0, 12))

        # Header row
        header = ctk.CTkFrame(content, fg_color="transparent")
        header.pack(fill="x", padx=22, pady=(16, 6))

        title_label = ctk.CTkLabel(
            header, text=label,
            font=ctk.CTkFont(family=FONT_TITLE, size=20, weight="bold"),
            text_color=C["text"]
        )
        title_label.pack(side="left")

        # Status badge on Canvas (Ultra-smooth PIL pill image prevents any jagged/frastagliata edges)
        badge_canvas = tk.Canvas(header, width=175, height=36, bg=C["surface"], highlightthickness=0)
        badge_canvas.pack(side="right", padx=(8, 0))

        # Description
        ctk.CTkLabel(
            content, text=meta["desc"],
            font=ctk.CTkFont(family=FONT_BODY, size=15),
            text_color=C["text_muted"],
            anchor="w"
        ).pack(fill="x", padx=22, pady=(0, 8))

        # Log area — seamless card integration (no md code cell)
        log_frame = ctk.CTkFrame(content, fg_color="transparent")
        log_frame.pack(fill="x", padx=22, pady=(2, 16))

        log_text = tk.Text(
            log_frame,
            bg=C["surface"], fg=C["text_body"],
            font=(FONT_BODY, 14),
            relief="flat", padx=4, pady=4,
            spacing1=3, spacing2=4, spacing3=3,
            insertbackground=C["text"],
            height=3, wrap="word",
            state="disabled",
            borderwidth=0,
            highlightthickness=0
        )
        log_text.pack(fill="x")

        # Color and formatting tags (strictly unified Zinc intensity ramp, zero white, zero blue)
        log_text.tag_configure("worker_name",    foreground=C["text"],         font=(FONT_BODY, 13))
        log_text.tag_configure("worker_details", foreground=C["text_muted"],   font=(FONT_BODY, 12))
        log_text.tag_configure("info",           foreground=C["text_muted"],   font=(FONT_BODY, 14))
        log_text.tag_configure("key",            foreground=C["text"],         font=(FONT_BODY, 14, "bold"))
        log_text.tag_configure("val",            foreground=C["text_body"],    font=(FONT_BODY, 14))
        log_text.tag_configure("ok",             foreground=C["green_bright"], font=(FONT_BODY, 14, "bold"))
        log_text.tag_configure("warn",           foreground=C["yellow"],       font=(FONT_BODY, 14, "bold"))
        log_text.tag_configure("error",          foreground=C["red"],          font=(FONT_BODY, 14, "bold"))
        log_text.tag_configure("stage",          foreground=C["text"],         font=(FONT_TITLE, 15, "bold"))
        log_text.tag_configure("value",          foreground=C["text"],         font=(FONT_BODY, 14, "bold"))
        log_text.tag_configure("phrase",         foreground=C["text_dim"],     font=(FONT_BODY, 13, "italic"))

        # Rich markdown formatting tags (Unified zinc intensities for titles, text, bullets, numbers)
        log_text.tag_configure("md_main_title",  foreground=C["text"],         font=(FONT_TITLE, 18, "bold"), spacing1=8, spacing3=4)
        log_text.tag_configure("md_section",     foreground=C["text"],         font=(FONT_TITLE, 16, "bold"), spacing1=8, spacing3=3)
        log_text.tag_configure("md_bold",        foreground=C["text"],         font=(FONT_BODY, 14, "bold"))
        log_text.tag_configure("md_body",        foreground=C["text_body"],    font=(FONT_BODY, 14))
        log_text.tag_configure("md_bullet",      foreground=C["text_muted"],   font=(FONT_BODY, 14, "bold"))
        log_text.tag_configure("md_number",      foreground=C["text_muted"],   font=(FONT_BODY, 14, "bold"))

        node_data = {
            "agent_id": agent_id,
            "icon_text": meta["icon"],
            "frame": node_frame,
            "content": content,
            "circle_canvas": circle_canvas,
            "icon_circle": icon_circle,
            "line_canvas": line_canvas,
            "badge_canvas": badge_canvas,
            "_spinner_job": None,
            "log_text": log_text,
            "log_frame": log_frame,
            "status": "running",
        }
        self._pipeline_nodes.append(node_data)

        # Initialize badge as running
        self._set_node_badge(node_data, "running")

        # Auto-scroll to bottom
        self.root.after(50, lambda: self.scroll_frame._parent_canvas.yview_moveto(1.0))

        return node_data

    def _set_node_badge(self, node: Dict, status: str, message: str = ""):
        """Update a node's badge with ultra-smooth anti-aliased pill image (zero jagged edges)."""
        if "_spinner_job" in node and node["_spinner_job"]:
            try:
                self.root.after_cancel(node["_spinner_job"])
            except Exception:
                pass
            node["_spinner_job"] = None

        bc = node["badge_canvas"]
        bc.delete("all")

        if status == "running":
            pill_img = get_smooth_pill_image(175, 36, 15, C["yellow_soft"], C["yellow"], 1, C["surface"])
            bc.create_image(0, 0, image=pill_img, anchor="nw", tags="pill")
            bc._pill_img = pill_img
            bc.create_text(104, 18, text="In esecuzione", font=(FONT_TITLE, 13, "bold"), fill=C["yellow"], anchor="center", tags="text")

            # Animate smooth spinner arc
            angle = [0]
            def _spin():
                if node.get("status") != "running":
                    return
                bc.delete("spinner_arc")
                cx, cy, r = 26, 18, 8
                bc.create_arc(cx - r, cy - r, cx + r, cy + r,
                              start=angle[0], extent=270,
                              outline=C["yellow"], width=2, style="arc", tags="spinner_arc")
                angle[0] = (angle[0] + 18) % 360
                node["_spinner_job"] = self.root.after(40, _spin)
            _spin()

        elif status == "done":
            pill_img = get_smooth_pill_image(175, 36, 15, C["green_soft"], C["green"], 1, C["surface"])
            bc.create_image(0, 0, image=pill_img, anchor="nw", tags="pill")
            bc._pill_img = pill_img
            bc.create_text(87, 18, text="Completato", font=(FONT_TITLE, 13, "bold"), fill=C["green_bright"], anchor="center")

        elif status == "error":
            pill_img = get_smooth_pill_image(175, 36, 15, C["red_soft"], C["red"], 1, C["surface"])
            bc.create_image(0, 0, image=pill_img, anchor="nw", tags="pill")
            bc._pill_img = pill_img
            bc.create_text(87, 18, text=message or "Errore", font=(FONT_TITLE, 13, "bold"), fill=C["red"], anchor="center")

        elif status == "waiting":
            pill_img = get_smooth_pill_image(175, 36, 15, C["purple_soft"], C["purple"], 1, C["surface"])
            bc.create_image(0, 0, image=pill_img, anchor="nw", tags="pill")
            bc._pill_img = pill_img
            bc.create_text(87, 18, text="In attesa", font=(FONT_TITLE, 13, "bold"), fill=C["purple"], anchor="center")

    def _update_node_status(self, node: Dict, status: str, message: str = ""):
        """Update a node's visual status: 'done', 'error', 'waiting'."""
        node["status"] = status
        self._set_node_badge(node, status, message)

        icon_text = node.get("icon_text", "")
        if status == "done":
            circ_img = get_smooth_circle_icon_image(48, icon_text, C["green"], C["green_soft"], 2, C["bg"])
            node["circle_canvas"].itemconfig("circle_img", image=circ_img)
            node["circle_canvas"]._circ_img = circ_img
            node["line_canvas"].itemconfig("connector", fill=C["line_done"])
        elif status == "error":
            circ_img = get_smooth_circle_icon_image(48, icon_text, C["red"], C["red_soft"], 2, C["bg"])
            node["circle_canvas"].itemconfig("circle_img", image=circ_img)
            node["circle_canvas"]._circ_img = circ_img
            node["line_canvas"].itemconfig("connector", fill=C["line_error"])

    def _insert_inline_markdown(self, text_widget: tk.Text, text: str):
        """Format inline bold **text** within a line."""
        parts = text.split("**")
        for i, part in enumerate(parts):
            if not part:
                continue
            if i % 2 == 1:
                text_widget.insert("end", part, "md_bold")
            else:
                text_widget.insert("end", part, "md_body")

    def _node_log_markdown(self, node: Dict, markdown_text: str):
        """Parse and append formatted markdown text into the node's log widget."""
        import re
        lt = node["log_text"]
        lt.configure(state="normal")

        lines = markdown_text.split("\n")
        for line in lines:
            s = line.strip()
            if not s:
                lt.insert("end", "\n")
                continue

            # Separator lines like ===== or -----
            if re.match(r"^[=\-─_]{4,}$", s):
                lt.insert("end", "─" * 58 + "\n", "stage")
                continue

            # Major Title: **Report di Diagnosi Simbolica** or # Report di Diagnosi or ANALISI DI INFATTIBILITÀ
            cleaned = s.strip("#* ").strip()
            if any(k in cleaned.lower() for k in ["report di diagnosi", "analisi di infattibilit", "root cause"]):
                lt.insert("end", f"\n{cleaned.upper()}\n", "md_main_title")
                lt.insert("end", "─" * 58 + "\n", "stage")
                continue

            # Section Header: e.g. ## Sintesi or **Sintesi del problema principale** or 1. Sintesi... or COLI DI BOTTIGLIA...
            is_sec = False
            sec_title = ""
            if s.startswith(("## ", "### ")):
                is_sec = True
                sec_title = s.lstrip("#").strip().strip("*").strip()
            elif s.startswith("**") and s.endswith("**") and s.count("**") == 2:
                is_sec = True
                sec_title = s.strip("*").strip()
            elif any(cleaned.startswith(k) for k in ["COLI DI BOTTIGLIA", "PROBLEMI DI MONTE ORE", "Infermieri Interessati"]):
                is_sec = True
                sec_title = cleaned

            if is_sec:
                lt.insert("end", f"\n{sec_title}\n", "md_section")
                continue

            # Bullet item: * item or - item or • item
            if s.startswith(("* ", "- ", "• ")):
                content = s[2:].strip()
                lt.insert("end", "   • ", "md_bullet")
                self._insert_inline_markdown(lt, content)
                lt.insert("end", "\n")
                continue

            # Numbered list: 1. **Title**: desc
            num_m = re.match(r"^(\d+[\.\)])\s*(.*)", s)
            if num_m:
                prefix = num_m.group(1)
                rest = num_m.group(2)
                lt.insert("end", f"   {prefix} ", "md_number")
                self._insert_inline_markdown(lt, rest)
                lt.insert("end", "\n")
                continue

            # Regular paragraph with inline markdown
            lt.insert("end", "   ")
            self._insert_inline_markdown(lt, s)
            lt.insert("end", "\n")

        # Auto-expand height (up to 40 lines for comfortable viewing)
        lines_count = int(lt.index("end-1c").split(".")[0])
        lt.configure(height=min(lines_count + 1, 40))
        lt.see("end")
        lt.configure(state="disabled")

        # Auto-scroll pipeline
        self.root.after(30, lambda: self.scroll_frame._parent_canvas.yview_moveto(1.0))

    def _log_worker_card(self, node: Dict, p: Any):
        """Mostra il profilo worker analizzato con tutte le informazioni rilevanti in testo grigio (worker id non in grassetto)."""
        lt = node["log_text"]
        lt.configure(state="normal")

        # Worker header: normale (NON in grassetto), colore grigio
        lt.insert("end", f"   {p.worker_id}:\n", "worker_name")

        raw = p.raw_preference
        pref_shifts = [s.value for s in getattr(raw, "preferred_shifts", [])]
        disliked_shifts = [s.value for s in getattr(raw.shift_tolerance, "disliked_shift_types", [])]
        unavail = getattr(raw.availability, "unavailable_days", [])
        rest_pref = getattr(raw.availability, "preferred_rest_days", [])

        night_tol = getattr(raw.shift_tolerance, "max_tolerated_nights", None)
        hol_tol = getattr(raw.shift_tolerance, "max_tolerated_holidays", None)
        weekend_tol = getattr(raw.shift_tolerance, "max_tolerated_weekends", None)

        night_str = "nessuna notte (0)" if night_tol == 0 else (f"max {night_tol}" if night_tol is not None else "nessun limite")
        hol_str = "nessun festivo (0)" if hol_tol == 0 else (f"max {hol_tol}" if hol_tol is not None else "nessun limite")
        weekend_str = "nessun weekend (0)" if weekend_tol == 0 else (f"max {weekend_tol}" if weekend_tol is not None else "nessun limite")

        p_str = ", ".join(pref_shifts) if pref_shifts else "nessuno"
        d_str = ", ".join(disliked_shifts) if disliked_shifts else "nessuno"
        u_str = ", ".join(unavail) if unavail else "nessuno"
        r_str = ", ".join(rest_pref) if rest_pref else "nessuno"

        lt.insert("end", f"      • Turni: preferiti=[{p_str}]  sgraditi=[{d_str}]\n", "worker_details")
        lt.insert("end", f"      • Calendario: indisponibile=[{u_str}]  riposo preferito=[{r_str}]\n", "worker_details")
        lt.insert("end", f"      • Tolleranze: notti={night_str}  festivi={hol_str}  weekend={weekend_str}\n\n", "worker_details")

        # Auto-expand height
        lines = int(lt.index("end-1c").split(".")[0])
        lt.configure(height=min(lines + 1, 40))
        lt.see("end")
        lt.configure(state="disabled")

    def _node_log(self, node: Dict, msg: str, tag: str = "info"):
        """Append a formatted log line to a node's log text widget."""
        lt = node["log_text"]
        lt.configure(state="normal")

        clean_msg = msg.lstrip("✔✓✘✕⚠⚡🔍📝🛡⚖🏥●- ").strip()

        if "**" in msg:
            self._insert_inline_markdown(lt, msg)
            lt.insert("end", "\n")
        elif ": " in msg and (tag in ("info", "value")):
            key, val = msg.split(": ", 1)
            lt.insert("end", f"{key}: ", "key")
            val_tag = "value" if tag == "value" else "val"
            lt.insert("end", f"{val}\n", val_tag)
        elif tag in ("ok", "error", "warn"):
            lt.insert("end", clean_msg + "\n", tag)
        else:
            lt.insert("end", msg + "\n", tag)

        # Auto-expand height (up to 40 lines)
        lines = int(lt.index("end-1c").split(".")[0])
        lt.configure(height=min(lines + 1, 40))
        lt.see("end")
        lt.configure(state="disabled")

        # Auto-scroll pipeline
        self.root.after(30, lambda: self.scroll_frame._parent_canvas.yview_moveto(1.0))

    # ─────────────────────────────────────────
    # Interactive Proposal Buttons (Infeasibility)
    # ─────────────────────────────────────────
    def _add_proposal_buttons(self, node: Dict, proposals: List[Dict], diagnosis: Dict):
        """Add clickable proposal buttons arranged in a responsive grid with dynamic text wrapping."""
        content = node["content"]

        proposals_frame = ctk.CTkFrame(content, fg_color=C["surface2"], corner_radius=14,
                                      border_width=1, border_color=C["border"])
        proposals_frame.pack(fill="x", padx=22, pady=(6, 16))

        # Proposals Grid Container
        grid_frame = ctk.CTkFrame(proposals_frame, fg_color="transparent")
        grid_frame.pack(fill="x", padx=12, pady=(12, 10))

        use_two_cols = len(proposals) >= 2
        if use_two_cols:
            grid_frame.grid_columnconfigure(0, weight=1, uniform="prop_col")
            grid_frame.grid_columnconfigure(1, weight=1, uniform="prop_col")
        else:
            grid_frame.grid_columnconfigure(0, weight=1)

        label_pairs = []
        for i, prop in enumerate(proposals):
            r = (i // 2) if use_two_cols else i
            c = (i % 2) if use_two_cols else 0

            prop_card = ctk.CTkFrame(grid_frame, fg_color=C["surface"], corner_radius=12,
                                      border_width=1, border_color=C["border"])
            prop_card.grid(row=r, column=c, padx=8, pady=8, sticky="nsew")
            grid_frame.grid_rowconfigure(r, weight=1)

            # Header
            ctk.CTkLabel(
                prop_card,
                text=prop["proposal_title"],
                font=ctk.CTkFont(family=FONT_TITLE, size=15, weight="bold"),
                text_color=C["text"]
            ).pack(anchor="w", padx=36, pady=(16, 8))

            # Message (restrained with comfortable margins so text is never cut off)
            msg_lbl = ctk.CTkLabel(
                prop_card,
                text=prop["proposal_message"],
                font=ctk.CTkFont(family=FONT_BODY, size=14),
                text_color=C["text_body"],
                justify="left",
                anchor="w"
            )
            msg_lbl.pack(fill="x", padx=36, pady=(0, 8))

            # Tradeoff
            tradeoff_lbl = ctk.CTkLabel(
                prop_card,
                text=prop["suggested_tradeoff"],
                font=ctk.CTkFont(family=FONT_BODY, size=13),
                text_color=C["text_muted"],
                justify="left",
                anchor="w"
            )
            tradeoff_lbl.pack(fill="x", padx=36, pady=(0, 14))

            # Dynamic text wrap binding: restrained wrap calculation with DPI scaling compensation
            def _bind_card(card=prop_card, ml=msg_lbl, tl=tradeoff_lbl):
                def _do_resize(event=None):
                    w = card.winfo_width()
                    if w > 80:
                        scale = ml._get_widget_scaling() if hasattr(ml, "_get_widget_scaling") else 1.0
                        target_physical = max(180, w - 100)
                        wrap_logical = int(target_physical / scale)
                        try:
                            ml.configure(wraplength=wrap_logical)
                            tl.configure(wraplength=wrap_logical)
                        except Exception:
                            pass
                card.bind("<Configure>", _do_resize)
                card.after(30, _do_resize)
                card.after(150, _do_resize)
            _bind_card()

            # Accept button (centered and compact, does not stretch across entire window)
            worker_id = prop["worker_id"]
            ctk.CTkButton(
                prop_card,
                text=f"Accetta proposta per {worker_id}",
                width=300, height=40,
                corner_radius=9,
                fg_color=C["green"],
                hover_color=C["green_bright"],
                text_color=C["text"],
                font=ctk.CTkFont(family=FONT_TITLE, size=14, weight="bold"),
                command=lambda w=[worker_id]: self._on_proposal_accepted(w)
            ).pack(anchor="center", pady=(4, 16))

        # "Accept All" button if multiple proposals (centered)
        if len(proposals) > 1:
            all_workers = [p["worker_id"] for p in proposals]
            ctk.CTkButton(
                proposals_frame,
                text=f"Accetta tutte le proposte ({len(proposals)} lavoratori)",
                width=440, height=46,
                corner_radius=11,
                fg_color=C["accent"],
                hover_color=C["accent_hover"],
                text_color=C["text"],
                font=ctk.CTkFont(family=FONT_TITLE, size=15, weight="bold"),
                command=lambda w=all_workers: self._on_proposal_accepted(w)
            ).pack(anchor="center", pady=(4, 16))

        # Store reference for later cleanup
        node["proposals_frame"] = proposals_frame

        # Scroll to see buttons
        self.root.after(100, lambda: self.scroll_frame._parent_canvas.yview_moveto(1.0))

    def _on_proposal_accepted(self, workers: List[str]):
        """Handle user clicking a proposal button."""
        self._selected_proposal_workers = workers
        self._proposal_event.set()

    # ═══════════════════════════════════════════
    #  SCHEDULE GRID
    # ═══════════════════════════════════════════
    def _build_schedule_section(self):
        """Build the schedule grid after pipeline completes."""
        data = self.result_data
        matrix = data["schedule_matrix"]
        horizon = data["horizon"]
        profiles = data["worker_profiles"]
        scores = data["satisfaction_scores"]
        num_std = data["num_standard"]
        num_sp = data["num_specialized"]
        n_workers = len(matrix)
        n_days = horizon.total_days

        # Section header
        section = ctk.CTkFrame(self.pipeline_container, fg_color="transparent")
        section.pack(fill="x", pady=(28, 0))

        ctk.CTkLabel(
            section, text="Turnazione Finale",
            font=ctk.CTkFont(family=FONT_TITLE, size=28, weight="bold"),
            text_color=C["text"]
        ).pack(anchor="w", pady=(0, 16))

        total_assigned = sum(1 for r in matrix for c in r if c is not None)

        # Schedule card container (Dark minimalist card styled identically to reference image)
        card = ctk.CTkFrame(section, fg_color="#0D0D0D", corner_radius=16,
                            border_width=1, border_color="#1F1F1F")
        card.pack(fill="x", pady=(0, 16))

        # Monospace uppercase title header
        header_frame = ctk.CTkFrame(card, fg_color="transparent")
        header_frame.pack(fill="x", padx=28, pady=(24, 16))

        title_text = f"TURNAZIONE  —  {total_assigned} ASSEGNAZIONI  ·  {n_workers} MEDICI  ·  {n_days} GIORNI"
        ctk.CTkLabel(
            header_frame,
            text=title_text,
            font=ctk.CTkFont(family=FONT_MONO, size=13, weight="bold"),
            text_color="#71717A"
        ).pack(side="left")

        # Main table container holding pinned operator column and scrollable date grid
        table_container = ctk.CTkFrame(card, fg_color="transparent")
        table_container.pack(fill="x", padx=24, pady=(0, 12))

        # Left pinned frame (Workers column)
        left_frame = tk.Frame(table_container, bg="#0D0D0D")
        left_frame.pack(side="left", fill="y", padx=(0, 12))

        # Right scrollable frame (Days + Score)
        right_frame = tk.Frame(table_container, bg="#0D0D0D")
        right_frame.pack(side="left", fill="both", expand=True)

        canvas = tk.Canvas(right_frame, bg="#0D0D0D", highlightthickness=0)
        self.schedule_canvas = canvas
        h_scroll = ctk.CTkScrollbar(
            right_frame,
            orientation="horizontal",
            command=canvas.xview,
            button_color=C["surface3"],
            button_hover_color=C["border_light"],
            height=12
        )
        self.schedule_hscroll = h_scroll
        canvas.configure(xscrollcommand=h_scroll.set)

        grid_frame = tk.Frame(canvas, bg="#0D0D0D")
        canvas.create_window((0, 0), window=grid_frame, anchor="nw")

        def _on_grid_configure(event=None):
            canvas.configure(scrollregion=canvas.bbox("all"))
            req_h = grid_frame.winfo_reqheight()
            if req_h > 10:
                canvas.configure(height=req_h)

        grid_frame.bind("<Configure>", _on_grid_configure)

        canvas.pack(side="top", fill="both", expand=True)
        h_scroll.pack(side="bottom", fill="x", pady=(10, 0))

        def _on_shift_mw(event):
            canvas.xview_scroll(-1 * (event.delta // 120), "units")

        canvas.bind("<Shift-MouseWheel>", _on_shift_mw)
        grid_frame.bind("<Shift-MouseWheel>", _on_shift_mw)

        col_w = 6
        col_pad = 6
        f_size = 11
        ROW_H = 36
        HDR_H = 32

        # Configure row heights identically on left and right grids
        left_frame.rowconfigure(0, minsize=HDR_H)
        grid_frame.rowconfigure(0, minsize=HDR_H)

        # Left Header: OPERATORE
        lbl_empty = tk.Label(
            left_frame, text="OPERATORE", bg="#0D0D0D", fg="#52525B",
            font=(FONT_MONO, f_size, "bold"), width=16, anchor="w"
        )
        lbl_empty.grid(row=0, column=0, pady=(4, 12), sticky="w")

        # Right Header: Real dates (e.g. 01/12, 02/12, ... 07/12, 08/12)
        for d in range(n_days):
            d_str = horizon.index_to_date.get(d, "")
            date_display = d_str[:5].replace("-", "/") if d_str else f"{d+1:02d}"
            is_hol = d in horizon.holiday_indices
            is_wknd = d in horizon.weekend_indices
            
            if is_hol:
                day_fg = "#F59E0B"      # Ambra per festività
                day_bg = "#1F1600"
            elif is_wknd:
                day_fg = "#60A5FA"      # Blu/Azzurro chiaro per weekend
                day_bg = "#0B1528"
            else:
                day_fg = "#71717A"
                day_bg = "#0D0D0D"

            lbl = tk.Label(
                grid_frame,
                text=date_display,
                bg=day_bg,
                fg=day_fg,
                font=(FONT_MONO, f_size, "bold" if (is_hol or is_wknd) else "normal"),
                width=col_w
            )
            lbl.grid(row=0, column=d, padx=col_pad, pady=(4, 12), sticky="nsew")
            lbl.bind("<Shift-MouseWheel>", _on_shift_mw)

        lbl_sc_hdr = tk.Label(
            grid_frame, text="SCORE", bg="#0D0D0D", fg="#52525B",
            font=(FONT_MONO, f_size, "bold"), width=6
        )
        lbl_sc_hdr.grid(row=0, column=n_days, padx=(14, 0), pady=(4, 12), sticky="nsew")
        lbl_sc_hdr.bind("<Shift-MouseWheel>", _on_shift_mw)

        # Shift typography styles matching reference mockup (Coherent Zinc palette, zero pure white)
        SHIFT_STYLE = {
            0: {"letter": "M", "fg": C["text"], "font": (FONT_MONO, f_size + 1, "bold")},        # Mattina: Zinc chiaro (#D4D4D8)
            1: {"letter": "P", "fg": C["text_body"], "font": (FONT_MONO, f_size + 1, "normal")}, # Pomeriggio: Zinc medio (#A1A1AA)
            2: {"letter": "N", "fg": C["text_muted"], "font": (FONT_MONO, f_size + 1, "normal")},# Notte: Zinc scuro (#71717A)
            None: {"letter": "·", "fg": "#27272A", "font": (FONT_MONO, f_size, "normal")}        # Riposo: Punto sobrio
        }

        # Worker rows
        for w in range(n_workers):
            row_idx = w + 1
            left_frame.rowconfigure(row_idx, minsize=ROW_H)
            grid_frame.rowconfigure(row_idx, minsize=ROW_H)

            profile = profiles[w]
            score = scores[w]
            is_spec = (num_sp > 0 and w >= num_std)
            badge = " (S)" if is_spec else ""

            # Left Worker cell: Avatar + Name (pinned)
            w_box = tk.Frame(left_frame, bg="#0D0D0D")
            w_box.grid(row=row_idx, column=0, pady=2, sticky="w")

            av_img = get_avatar_image(w, "#0D0D0D")
            av_lbl = tk.Label(w_box, image=av_img, bg="#0D0D0D", bd=0)
            av_lbl._img = av_img
            av_lbl.pack(side="left", padx=(0, 8))

            w_name = f"{profile.worker_id}{badge}"
            name_lbl = tk.Label(
                w_box,
                text=w_name,
                bg="#0D0D0D",
                fg=C["text"],
                font=(FONT_BODY, 13, "bold" if is_spec else "normal"),
                width=12,
                anchor="w"
            )
            name_lbl.pack(side="left")

            # Day cells (scrollable)
            for d in range(n_days):
                shift_val = matrix[w][d]
                st = SHIFT_STYLE.get(shift_val, SHIFT_STYLE[None])
                is_hol = d in horizon.holiday_indices
                is_wknd = d in horizon.weekend_indices

                if is_hol and shift_val is not None:
                    cell_fg = "#F59E0B"
                elif is_wknd and shift_val is not None:
                    cell_fg = "#60A5FA"
                else:
                    cell_fg = st["fg"]

                c_lbl = tk.Label(
                    grid_frame,
                    text=st["letter"],
                    bg="#0D0D0D",
                    fg=cell_fg,
                    font=st["font"],
                    width=col_w
                )
                c_lbl.grid(row=row_idx, column=d, padx=col_pad, pady=2, sticky="nsew")
                c_lbl.bind("<Shift-MouseWheel>", _on_shift_mw)

                # Smooth hover feedback
                def _on_enter(e, l=c_lbl):
                    l.configure(bg="#1C1C1F")
                def _on_leave(e, l=c_lbl):
                    l.configure(bg="#0D0D0D")
                c_lbl.bind("<Enter>", _on_enter)
                c_lbl.bind("<Leave>", _on_leave)

            # Score column
            sc_color = "#10B981" if score >= 50 else ("#F59E0B" if score >= 30 else "#EF4444")
            sc_lbl = tk.Label(
                grid_frame, text=str(score), bg="#0D0D0D", fg=sc_color,
                font=(FONT_MONO, f_size + 1, "bold"), width=6
            )
            sc_lbl.grid(row=row_idx, column=n_days, padx=(14, 0), pady=2, sticky="nsew")
            sc_lbl.bind("<Shift-MouseWheel>", _on_shift_mw)

        # Footer Legend matching reference image
        footer_frame = ctk.CTkFrame(card, fg_color="transparent")
        footer_frame.pack(fill="x", side="bottom", padx=28, pady=(8, 20))

        ctk.CTkLabel(
            footer_frame,
            text="M = Morning      P = Afternoon      N = Night      · = Riposo          ▎ Arancione = Festività      ▎ Blu = Weekend",
            font=ctk.CTkFont(family=FONT_MONO, size=12),
            text_color="#71717A"
        ).pack(side="left")

    # ═══════════════════════════════════════════
    #  FAIRNESS PANEL
    # ═══════════════════════════════════════════
    def _build_fairness_section(self):
        """Build fairness metrics section."""
        data = self.result_data
        ref = data["refinement_res"]
        frep = data["fairness_report"]

        section = ctk.CTkFrame(self.pipeline_container, fg_color="transparent")
        section.pack(fill="x", pady=(24, 0))

        ctk.CTkLabel(
            section, text="Report di Equità",
            font=ctk.CTkFont(family=FONT_TITLE, size=28, weight="bold"),
            text_color=C["text"]
        ).pack(anchor="w", pady=(0, 16))

        # Metric cards row
        cards_frame = ctk.CTkFrame(section, fg_color="transparent")
        cards_frame.pack(fill="x", pady=(0, 18))

        total_iters = ref.get("completed_iterations", ref.get("total_iterations", 0))
        metrics = [
            ("Min Satisfaction", f"{ref['initial_min_sat']} -> {ref['final_min_sat']}", C["text"]),
            ("Gini Index", f"{ref['initial_gini']} -> {ref['final_gini']}", C["text"]),
            ("Media Soddisfazione", str(frep["mean_satisfaction"]), C["text"]),
            ("Std Deviation", str(frep["std_deviation"]), C["text_muted"]),
            ("Iterazioni Loop", str(total_iters), C["text_muted"]),
        ]

        for title, value, color in metrics:
            card = ctk.CTkFrame(cards_frame, fg_color=C["surface"], corner_radius=14,
                                border_width=1, border_color=C["border"])
            card.pack(side="left", padx=(0, 12), fill="x", expand=True)

            ctk.CTkLabel(card, text=title, font=ctk.CTkFont(family=FONT_BODY, size=14),
                         text_color=C["text_muted"]).pack(padx=20, pady=(16, 4))
            ctk.CTkLabel(card, text=value, font=ctk.CTkFont(family=FONT_TITLE, size=24, weight="bold"),
                         text_color=color).pack(padx=20, pady=(0, 16))

        # Distribution table
        dist_card = ctk.CTkFrame(section, fg_color=C["surface"], corner_radius=14,
                                  border_width=1, border_color=C["border"])
        dist_card.pack(fill="x", pady=(0, 18))

        ctk.CTkLabel(
            dist_card, text="Distribuzione Carichi per Lavoratore",
            font=ctk.CTkFont(family=FONT_TITLE, size=18, weight="bold"),
            text_color=C["text"]
        ).pack(anchor="w", padx=24, pady=(20, 12))

        # Table header
        tbl = ctk.CTkFrame(dist_card, fg_color="transparent")
        tbl.pack(fill="x", padx=24, pady=(0, 20))

        headers = ["Lavoratore", "Notti", "Festivi", "Weekend", "Score"]
        hdr_frame = ctk.CTkFrame(tbl, fg_color=C["surface2"], corner_radius=8)
        hdr_frame.pack(fill="x", pady=(0, 8))

        for i, h in enumerate(headers):
            w_size = 210 if i == 0 else 105
            ctk.CTkLabel(hdr_frame, text=h, font=ctk.CTkFont(family=FONT_TITLE, size=15, weight="bold"),
                         text_color=C["text"], width=w_size).pack(side="left", padx=10, pady=10)

        # Table rows
        for info in frep["workers_shift_distribution"]:
            sc = info["score"]
            row_color = C["red_soft"] if sc < 0 else (C["yellow_soft"] if sc < 30 else "transparent")
            sc_fg = C["red"] if sc < 0 else (C["yellow"] if sc < 30 else C["green_bright"])

            row_f = ctk.CTkFrame(tbl, fg_color=row_color, corner_radius=6)
            row_f.pack(fill="x", pady=2)

            ctk.CTkLabel(row_f, text=info["worker_id"], font=ctk.CTkFont(family=FONT_BODY, size=14),
                         text_color=C["text"], width=210, anchor="w").pack(side="left", padx=10, pady=7)
            ctk.CTkLabel(row_f, text=str(info["nights"]), font=ctk.CTkFont(family=FONT_BODY, size=14),
                         text_color=C["text"], width=105).pack(side="left", padx=10, pady=7)
            ctk.CTkLabel(row_f, text=str(info["holidays"]), font=ctk.CTkFont(family=FONT_BODY, size=14),
                         text_color=C["text"], width=105).pack(side="left", padx=10, pady=7)
            ctk.CTkLabel(row_f, text=str(info.get("weekends", 0)), font=ctk.CTkFont(family=FONT_BODY, size=14),
                         text_color=C["text"], width=105).pack(side="left", padx=10, pady=7)
            ctk.CTkLabel(row_f, text=str(sc), font=ctk.CTkFont(size=14, weight="bold"),
                         text_color=sc_fg, width=105).pack(side="left", padx=10, pady=7)

    # ═══════════════════════════════════════════
    #  FOOTER
    # ═══════════════════════════════════════════
    def _build_footer(self):
        footer = ctk.CTkFrame(self.pipeline_container, fg_color="transparent")
        footer.pack(fill="x", pady=(28, 36))

        ctk.CTkLabel(
            footer,
            text="Progetto per il corso di Intelligenza Artificiale  ·  UNICAL — A.A. 2025/2026",
            font=ctk.CTkFont(size=14),
            text_color=C["text_dim"]
        ).pack()

    # ═══════════════════════════════════════════
    #  TIMER
    # ═══════════════════════════════════════════
    def _start_timer(self):
        self._start_time = time.time()
        self._update_timer()

    def _update_timer(self):
        if not self._running:
            return
        elapsed = int(time.time() - self._start_time)
        minutes = elapsed // 60
        seconds = elapsed % 60
        self.timer_label.configure(text=f"{minutes:02d}:{seconds:02d}")
        self._timer_id = self.root.after(500, self._update_timer)

    def _stop_timer(self):
        self._running = False
        if self._timer_id:
            self.root.after_cancel(self._timer_id)
            self._timer_id = None

    # ═══════════════════════════════════════════
    #  HELPERS
    # ═══════════════════════════════════════════
    def _browse_file(self):
        path = filedialog.askopenfilename(
            title="Seleziona file preferenze lavoratori",
            filetypes=[("File di testo", "*.txt"), ("Tutti i file", "*.*")]
        )
        if path:
            self.txt_file.set(path)

    def _poll_log_queue(self):
        """Process log messages from the background thread."""
        try:
            while True:
                action = self.log_queue.get_nowait()
                if action:
                    action()
        except queue.Empty:
            pass
        self.root.after(60, self._poll_log_queue)

    def _schedule_ui(self, fn):
        """Queue a UI update function to run on the main thread."""
        self.log_queue.put(fn)

    # ═══════════════════════════════════════════
    #  PIPELINE EXECUTION
    # ═══════════════════════════════════════════
    def _start_pipeline(self):
        txt_path = self.txt_file.get().strip()
        if not txt_path:
            messagebox.showerror("File mancante",
                                 "Seleziona il file di testo con le preferenze dei lavoratori.")
            return
        if not os.path.exists(txt_path):
            messagebox.showerror("File non trovato",
                                 f"Il file non esiste:\n{txt_path}")
            return

        self._running = True
        self._build_pipeline_view()
        self._start_timer()

        t = threading.Thread(target=self._run_pipeline, daemon=True)
        t.start()

    def _run_pipeline(self):
        """Run the full pipeline in a background thread."""
        try:
            txt_path = self.txt_file.get().strip()
            uc = self.use_case.get().strip().upper()
            time_lim = self.TIME_LIMIT

            config_name = f"configs_case_{uc.lower()}"
            config_path = PROJECT_ROOT / "configs" / config_name

            # ── STAGE 0: Config ──────────────────
            node_ref = [None]

            def _add_config_node():
                node_ref[0] = self._add_agent_node("config")
            self._schedule_ui(_add_config_node)
            time.sleep(0.15)

            cfg = load_config(str(config_path))

            def _log_config():
                n = node_ref[0]
                self._node_log(n, f"Scenario: {cfg.scenario_type}", "info")
                self._node_log(n, f"Lavoratori: {cfg.num_standard_workers} standard + {cfg.num_specialized_workers} specializzati", "info")
                self._node_log(n, f"Orizzonte: {cfg.start_date} -> {cfg.end_date}", "info")
                self._node_log(n, f"Turni mensili: {cfg.exact_monthly_equivalent_shifts} equiv.", "info")
                self._node_log(n, f"Ore sett. max: {cfg.max_weekly_working_hours}h", "info")
                self._node_log(n, f"Riposo post-notte: {cfg.mandatory_rest_days_after_night} giorni", "info")
                self._node_log(n, "Configurazione caricata.", "ok")
                self._update_node_status(n, "done")
            self._schedule_ui(_log_config)
            time.sleep(0.1)

            # Horizon
            horizon = SchedulingHorizon(cfg.start_date, cfg.end_date)

            # ── STAGE 1: LLM Workers ─────────────
            worker_node_ref = [None]

            def _add_worker_node():
                worker_node_ref[0] = self._add_agent_node("workers")
            self._schedule_ui(_add_worker_node)
            time.sleep(0.15)

            with open(txt_path, "r", encoding="utf-8") as f:
                lines = [l.strip() for l in f if l.strip() and not l.startswith("#")]

            worker_agent = WorkerAgent(horizon)

            def _on_worker_progress(i: int, t: str, p: FormalizedWorkerProfile, is_cached: bool):
                def _update_ui(idx=i, text=t, prof=p, cached=is_cached):
                    n = worker_node_ref[0]
                    cache_tag = " [⚡ CACHE]" if cached else ""
                    self._node_log(n, f"[{idx+1}/{len(lines)}]{cache_tag} \"{text[:90]}{'...' if len(text) > 90 else ''}\"", "phrase")
                    self._log_worker_card(n, prof)
                self._schedule_ui(_update_ui)

            worker_profiles = worker_agent.process_all(
                lines,
                on_progress=_on_worker_progress
            )

            def _done_workers():
                n = worker_node_ref[0]
                self._node_log(n, f"{len(worker_profiles)} profili estratti con successo.", "ok")
                self._update_node_status(n, "done")
            self._schedule_ui(_done_workers)
            time.sleep(0.1)

            # ── STAGE 2: Drafting ─────────────────
            draft_node_ref = [None]

            def _add_draft_node():
                draft_node_ref[0] = self._add_agent_node("drafting")
                self._node_log(draft_node_ref[0], f"Costruzione modello CP-SAT con {len(worker_profiles)} lavoratori...", "info")
            self._schedule_ui(_add_draft_node)
            time.sleep(0.1)

            draft_agent = ScheduleDraftingAgent(
                horizon=horizon,
                worker_profiles=worker_profiles,
                config=cfg,
            )
            draft_result = draft_agent.solve_draft(time_limit_seconds=time_lim)

            if draft_result["status"] not in ("FEASIBLE", "OPTIMAL"):
                # ── INFEASIBLE: Interactive replanning ──
                def _log_infeasible():
                    n = draft_node_ref[0]
                    self._node_log(n, "Bozza non fattibile — Attivazione Diagnostica...", "warn")
                    self._update_node_status(n, "error", "Infeasible")
                self._schedule_ui(_log_infeasible)
                time.sleep(0.1)

                try:
                    from infeasibility_agent import (
                        InfeasibilityDiagnostician,
                        InfeasibilityExplainer,
                        DynamicReplanningAgent
                    )

                    replan_node_ref = [None]

                    def _add_replan_node():
                        replan_node_ref[0] = self._add_agent_node("replanning")
                    self._schedule_ui(_add_replan_node)
                    time.sleep(0.1)

                    # Step 1: Diagnosis
                    def _log_diag_start():
                        self._node_log(replan_node_ref[0], "Analisi dei vincoli e colli di bottiglia...", "stage")
                    self._schedule_ui(_log_diag_start)

                    diagnostician = InfeasibilityDiagnostician(horizon, worker_profiles, cfg)
                    diagnosis = diagnostician.diagnose()

                    def _log_diag_results():
                        n = replan_node_ref[0]
                        self._node_log(n, f"Conflitti rilevati: {diagnosis['total_conflicts_count']}", "value")
                        conflicting = ', '.join(diagnosis['conflicting_workers']) if diagnosis['conflicting_workers'] else 'Nessuno'
                        self._node_log(n, f"Infermieri coinvolti: {conflicting}", "value")

                        # Show bottlenecks
                        for b in diagnosis.get("bottlenecks", []):
                            self._node_log(n, f"  - {b['detail']}", "warn")

                        # Show worker capacity issues
                        for w in diagnosis.get("worker_capacity_issues", []):
                            self._node_log(n, f"  - {w['detail']}", "warn")
                    self._schedule_ui(_log_diag_results)
                    time.sleep(0.1)

                    # Step 2: Explanation
                    def _log_explain_start():
                        self._node_log(replan_node_ref[0], "", "info")
                        self._node_log(replan_node_ref[0], "Generazione spiegazione in linguaggio naturale...", "stage")
                    self._schedule_ui(_log_explain_start)

                    explainer = InfeasibilityExplainer()
                    explanation = explainer.explain(diagnosis)

                    def _log_explanation():
                        n = replan_node_ref[0]
                        self._node_log_markdown(n, explanation)
                    self._schedule_ui(_log_explanation)
                    time.sleep(0.1)

                    # Step 3: Proposals — interactive!
                    replanning_agent = DynamicReplanningAgent(horizon, worker_profiles, cfg)
                    proposals = replanning_agent.formulate_negotiation_proposals(diagnosis)

                    if proposals:
                        def _log_proposals():
                            n = replan_node_ref[0]
                            self._update_node_status(n, "waiting")
                            self._add_proposal_buttons(n, proposals, diagnosis)
                        self._schedule_ui(_log_proposals)

                        # Wait for user to click a proposal
                        self._proposal_event.clear()
                        self._proposal_event.wait()  # blocks background thread

                        selected_workers = self._selected_proposal_workers or []

                        def _log_accepted():
                            n = replan_node_ref[0]
                            # Remove proposal buttons
                            if "proposals_frame" in n and n["proposals_frame"].winfo_exists():
                                n["proposals_frame"].destroy()
                            self._node_log(n, f"Proposta accettata per: {', '.join(selected_workers)}", "ok")
                            self._node_log(n, "Rilassamento vincoli e risoluzione modello alternativo...", "info")
                            # Reset badge to running
                            self._set_node_badge(n, "running")
                        self._schedule_ui(_log_accepted)
                        time.sleep(0.1)

                        # Step 4: Resolve with relaxed profiles
                        relaxed_profiles = replanning_agent.create_relaxed_profiles(
                            workers_to_relax=selected_workers
                        )

                        draft_agent_relaxed = ScheduleDraftingAgent(
                            horizon=horizon,
                            worker_profiles=relaxed_profiles,
                            config=cfg
                        )
                        relaxed_draft = draft_agent_relaxed.solve_draft(time_limit_seconds=time_lim)

                        if relaxed_draft["status"] in ("OPTIMAL", "FEASIBLE"):
                            draft_result = relaxed_draft
                            worker_profiles = relaxed_profiles

                            def _log_replan_ok():
                                n = replan_node_ref[0]
                                self._node_log(n, f"Replanning riuscito: {relaxed_draft['status']}", "ok")
                                self._node_log(n, f"Min Satisfaction: {relaxed_draft['min_satisfaction']}", "value")
                                self._update_node_status(n, "done")
                            self._schedule_ui(_log_replan_ok)
                        else:
                            def _log_replan_fail():
                                n = replan_node_ref[0]
                                self._node_log(n, "Replanning fallito. Modello non risolvibile.", "error")
                                self._update_node_status(n, "error", "Fallito")
                            self._schedule_ui(_log_replan_fail)
                            self._pipeline_done(False)
                            return
                    else:
                        # No proposals available, try automatic relaxation
                        def _log_auto_relax():
                            n = replan_node_ref[0]
                            self._node_log(n, "Nessuna proposta specifica. Tentativo rilassamento automatico...", "info")
                        self._schedule_ui(_log_auto_relax)

                        replanning_res = replanning_agent.run_dynamic_replanning(time_limit_seconds=time_lim)

                        if replanning_res["replanning_success"]:
                            draft_result = replanning_res["relaxed_draft"]
                            worker_profiles = replanning_res["relaxed_profiles"]

                            def _log_auto_ok():
                                n = replan_node_ref[0]
                                self._node_log(n, "Rilassamento automatico riuscito.", "ok")
                                self._update_node_status(n, "done")
                            self._schedule_ui(_log_auto_ok)
                        else:
                            def _log_auto_fail():
                                n = replan_node_ref[0]
                                self._node_log(n, "Impossibile risolvere il modello.", "error")
                                self._update_node_status(n, "error", "Fallito")
                            self._schedule_ui(_log_auto_fail)
                            self._pipeline_done(False)
                            return

                except Exception as replan_exc:
                    import traceback
                    tb = traceback.format_exc()
                    def _log_replan_error(exc=replan_exc, tb_str=tb):
                        if replan_node_ref[0]:
                            n = replan_node_ref[0]
                            self._node_log(n, f"Errore replanning: {exc}", "error")
                            self._node_log(n, tb_str, "info")
                            self._update_node_status(n, "error", "Errore")
                        else:
                            n = draft_node_ref[0]
                            self._node_log(n, f"Errore replanning: {exc}", "error")
                    self._schedule_ui(_log_replan_error)
                    self._pipeline_done(False)
                    return
            else:
                def _log_draft_done():
                    n = draft_node_ref[0]
                    self._node_log(n, f"Bozza risolta: {draft_result['status']}", "ok")
                    self._node_log(n, f"Min Satisfaction: {draft_result['min_satisfaction']}", "value")
                    self._node_log(n, f"Total Satisfaction: {draft_result['total_satisfaction']}", "value")
                    self._update_node_status(n, "done")
                self._schedule_ui(_log_draft_done)
            time.sleep(0.1)

            # ── STAGE 3: Quality Gate ─────────────
            qg_node_ref = [None]

            def _add_qg_node():
                qg_node_ref[0] = self._add_agent_node("quality")
            self._schedule_ui(_add_qg_node)
            time.sleep(0.1)

            # Verification
            def _log_verify_start():
                self._node_log(qg_node_ref[0], "Verifica Hard Constraints...", "stage")
            self._schedule_ui(_log_verify_start)

            hard_ver = HardConstraintVerificationAgent(
                horizon=horizon, worker_profiles=worker_profiles, config=cfg)
            verif = hard_ver.verify(draft_result["schedule_matrix"])

            if not verif["is_valid"]:
                def _log_verify_fail():
                    n = qg_node_ref[0]
                    self._node_log(n, f"REJECTED — {verif['violation_count']} violazione/i:", "error")
                    for v in verif["violations"]:
                        self._node_log(n, f"  - {v}", "warn")
                    self._update_node_status(n, "error", "Rejected")
                self._schedule_ui(_log_verify_fail)
                self._pipeline_done(False)
                return

            def _log_verify_ok():
                self._node_log(qg_node_ref[0], "Hard Constraints: APPROVED (0 violazioni)", "ok")
            self._schedule_ui(_log_verify_ok)

            # Fairness
            def _log_fair_start():
                self._node_log(qg_node_ref[0], "Analisi Equità...", "stage")
            self._schedule_ui(_log_fair_start)

            fairness_agent = SymbolicFairnessVerificationAgent(horizon, worker_profiles)
            init_fair = fairness_agent.evaluate(
                draft_result["schedule_matrix"], draft_result["satisfaction_scores"])

            def _log_fair_done():
                n = qg_node_ref[0]
                self._node_log(n, f"Gini Index iniziale: {init_fair['gini_index']}", "value")
                self._node_log(n, f"Std Dev iniziale: {init_fair['std_deviation']}", "value")
                disadv = [w["worker_id"] for w in init_fair["most_disadvantaged_workers"]]
                self._node_log(n, f"Lavoratori svantaggiati: {disadv}", "warn")
                self._node_log(n, "Quality Gate completato.", "ok")
                self._update_node_status(n, "done")
            self._schedule_ui(_log_fair_done)
            time.sleep(0.1)

            # ── STAGE 4: Refinement ───────────────
            ref_node_ref = [None]

            def _add_ref_node():
                ref_node_ref[0] = self._add_agent_node("refinement")
                self._node_log(ref_node_ref[0], "Avvio loop di raffinamento (fino a convergenza)...", "info")
            self._schedule_ui(_add_ref_node)
            time.sleep(0.1)

            def _on_refine_step(step_info):
                it = step_info.get("iteration")
                n = ref_node_ref[0]
                if not n:
                    return
                if step_info.get("improved"):
                    tgt = step_info.get("target_worker", "")
                    min_s = step_info.get("min_satisfaction", "")
                    self._schedule_ui(lambda: self._node_log(
                        n, f"Iterazione {it}: Migliorato lavoratore {tgt} (Min Sat: {min_s})", "value"
                    ))
                elif step_info.get("status") == "not_improved":
                    self._schedule_ui(lambda: self._node_log(
                        n, f"Iterazione {it}: Nessun ulteriore miglioramento di equità possibile.", "info"
                    ))
                elif step_info.get("status") == "cycle_detected":
                    self._schedule_ui(lambda: self._node_log(
                        n, f"Iterazione {it}: Rilevato stato già esplorato. Convergenza raggiunta.", "info"
                    ))
                elif step_info.get("status") == "infeasible":
                    self._schedule_ui(lambda: self._node_log(
                        n, f"Iterazione {it}: Modello infattibile per vincoli di equità.", "warn"
                    ))

            refiner = ScheduleRefinementAgent(
                horizon=horizon, worker_profiles=worker_profiles, config=cfg)
            ref_res = refiner.run_refinement_loop(
                draft_result, max_iterations=None, step_callback=_on_refine_step)

            def _log_ref_done():
                n = ref_node_ref[0]
                tot_it = ref_res.get("completed_iterations", ref_res.get("total_iterations", 0))
                self._node_log(n, f"Completato in {tot_it} iterazioni effettive", "ok")
                self._node_log(n, f"Min Satisfaction: {ref_res['initial_min_sat']} -> {ref_res['final_min_sat']}", "value")
                self._node_log(n, f"Gini Index: {ref_res['initial_gini']} -> {ref_res['final_gini']}", "value")
                self._update_node_status(n, "done")
            self._schedule_ui(_log_ref_done)
            time.sleep(0.1)

            final_rep = ref_res["final_fairness_report"]

            # ── Report distribuzione ──────────────
            def _log_distribution():
                n = ref_node_ref[0]
                self._node_log(n, "", "info")
                self._node_log(n, "── Distribuzione Turni Faticosi ──", "stage")
                for w_info in final_rep["workers_shift_distribution"]:
                    score = w_info["score"]
                    tag = "error" if score < 0 else ("warn" if score < 30 else "info")
                    self._node_log(n,
                        f"  {w_info['worker_id']:<12}  Notti={w_info['nights']:>2}"
                        f"  Festivi={w_info['holidays']:>2}  Weekend={w_info.get('weekends', 0):>2}  Score={score:>5}",
                        tag)
            self._schedule_ui(_log_distribution)

            # Save results and build final sections
            self.result_data = {
                "schedule_matrix": ref_res["final_schedule"],
                "satisfaction_scores": ref_res["final_scores"],
                "horizon": horizon,
                "worker_profiles": worker_profiles,
                "num_standard": cfg.num_standard_workers,
                "num_specialized": cfg.num_specialized_workers,
                "fairness_report": final_rep,
                "refinement_res": ref_res,
            }

            def _build_results():
                self._build_schedule_section()
                self._build_fairness_section()
                self._build_footer()
                # Scroll to schedule
                self.root.after(200, lambda: self.scroll_frame._parent_canvas.yview_moveto(1.0))
            self._schedule_ui(_build_results)

            self._pipeline_done(True)

        except Exception as exc:
            import traceback
            tb = traceback.format_exc()

            def _log_error():
                if self._pipeline_nodes:
                    n = self._pipeline_nodes[-1]
                    self._node_log(n, f"Errore imprevisto: {exc}", "error")
                    self._node_log(n, tb, "info")
                    self._update_node_status(n, "error", "Errore")
            self._schedule_ui(_log_error)
            self._pipeline_done(False)

    def _pipeline_done(self, success: bool):
        def _done():
            self._stop_timer()
            if success:
                self.status_label.configure(text="Completato", text_color=C["green_bright"])
                self.timer_label.configure(text_color=C["green_bright"])
            else:
                self.status_label.configure(text="Errore", text_color=C["red"])
                self.timer_label.configure(text_color=C["red"])
        self._schedule_ui(_done)


# ─────────────────────────────────────────────
# Entry points
# ─────────────────────────────────────────────
def launch_gui():
    root = ctk.CTk()
    app = SmartSchedulerGUI(root)
    root.mainloop()


def launch_gui_with_results(
    schedule_matrix: List[List[Optional[int]]],
    horizon: SchedulingHorizon,
    worker_profiles: list,
    satisfaction_scores: List[int],
    fairness_report: dict,
    refinement_res: dict,
    num_standard: int = 13,
    num_specialized: int = 0
):
    """Launch GUI directly with pre-computed results (for test scripts)."""
    root = ctk.CTk()
    app = SmartSchedulerGUI(root)

    # Skip welcome, go straight to results
    app.welcome_frame.destroy()

    app.main_frame = ctk.CTkFrame(root, fg_color=C["bg"])
    app.main_frame.pack(fill="both", expand=True)

    # Top bar
    topbar = ctk.CTkFrame(app.main_frame, fg_color=C["surface"], height=70, corner_radius=0)
    topbar.pack(fill="x")
    topbar.pack_propagate(False)

    ctk.CTkLabel(
        topbar, text="SmartScheduler",
        font=ctk.CTkFont(family=FONT_TITLE, size=22, weight="bold"),
        text_color=C["text"]
    ).pack(side="left", padx=24)

    ctk.CTkLabel(
        topbar, text="Schedulazione Caricata",
        font=ctk.CTkFont(family=FONT_TITLE, size=16, weight="bold"),
        text_color=C["text"]
    ).pack(side="right", padx=24)

    # Scrollable area
    app.scroll_frame = ctk.CTkScrollableFrame(
        app.main_frame, fg_color=C["bg"],
        scrollbar_button_color=C["surface3"],
        scrollbar_button_hover_color=C["border_light"],
    )
    app.scroll_frame.pack(fill="both", expand=True)

    app.pipeline_container = ctk.CTkFrame(app.scroll_frame, fg_color=C["bg"])
    app.pipeline_container.pack(padx=40, pady=24, fill="x")

    app.result_data = {
        "schedule_matrix": schedule_matrix,
        "satisfaction_scores": satisfaction_scores,
        "horizon": horizon,
        "worker_profiles": worker_profiles,
        "num_standard": num_standard,
        "num_specialized": num_specialized,
        "fairness_report": fairness_report,
        "refinement_res": refinement_res,
    }

    app._build_schedule_section()
    app._build_fairness_section()
    app._build_footer()

    root.mainloop()


if __name__ == "__main__":
    launch_gui()
