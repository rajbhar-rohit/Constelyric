"""
Constelyric - word-by-word lyrics that bloom into constellations on your Windows screen.

Click the tray icon to open the panel. Works with anything that reports to the
Windows media controls (Spotify, Apple Music, browsers, VLC, ...).
Lyrics: LRCLIB (line-synced); word timings are estimated within each line.
"""
import asyncio
import copy
import ctypes
import json
import math
import os
import random
import re
import sys
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import requests
from PyQt6.QtCore import (QEasingCurve, QEvent, QObject, QPoint, QPointF, QPropertyAnimation, QRectF, Qt,
                          QTimer, QUrl, QVariantAnimation, pyqtSignal)
from PyQt6.QtGui import (QAction, QBrush, QColor, QConicalGradient, QFont, QFontMetrics,
                         QDesktopServices, QGuiApplication, QIcon, QImage, QLinearGradient, QPainter,
                         QPainterPath, QPen, QPixmap, QRadialGradient)
from PyQt6.QtWidgets import (QApplication, QColorDialog, QFrame, QGraphicsDropShadowEffect,
                             QHBoxLayout, QLabel, QLineEdit, QMenu, QPushButton, QSystemTrayIcon,
                             QVBoxLayout, QWidget)

from winsdk.windows.media.control import (
    GlobalSystemMediaTransportControlsSessionManager as SessionManager,
    GlobalSystemMediaTransportControlsSessionPlaybackStatus as PlayStatus,
)
from winsdk.windows.storage.streams import DataReader

from lyric_text import clean_word, code_segments, typed_text
from symbols import symbol_for
from lyrics_sources import (INFO, Line, build_lines, clean_title, default_sources, find_lyrics,
                            first_artist, ordered_sources)

APP_VERSION = "1.1.1"                      # stamped automatically from the git tag in CI
GITHUB_REPO = "rajbhar-rohit/Constelyric"   # stamped automatically in CI; edit if building by hand
UA = {"User-Agent": "Constelyric/1.0 (open-source lyrics app)"}
ES_CONTINUOUS, ES_DISPLAY_REQUIRED = 0x80000000, 0x00000002
FONT_FAMILIES = ["Segoe UI Variable Text", "Segoe UI", "Nirmala UI", "Arial"]


# --------------------------------------------------------------------------
# Settings (saved to %APPDATA%\Constelyric\settings.json)
# --------------------------------------------------------------------------
class Settings(dict):
    _ROOT = Path(os.environ.get("APPDATA", str(Path.home())))
    PATH = _ROOT / "Constelyric" / "settings.json"
    _OLD = _ROOT / "LyricFlow" / "settings.json"          # pre-rename builds
    DEFAULTS = {"style": "Stack", "show": True, "auto": True, "awake": True, "glow": True,
                "art_bg": False, "icons": True, "clock": True, "skip_version": "", "sources": default_sources(), "musixmatch_key": "", "lyric": "#FFFFFF", "highlight": "#FFFFFF", "secondary": "#A0A0AA"}

    def __init__(self):
        super().__init__(copy.deepcopy(self.DEFAULTS))
        for path in (self.PATH, self._OLD):
            try:
                self.update(json.loads(path.read_text(encoding="utf-8")))
                break
            except Exception:
                continue
        if self.get("style") == "Fullscreen":          # old name
            self["style"] = "Constellation"

    def save(self):
        try:
            self.PATH.parent.mkdir(parents=True, exist_ok=True)
            self.PATH.write_text(json.dumps(self, indent=2), encoding="utf-8")
        except Exception as e:
            print("settings save error:", e)


S = Settings()


# --------------------------------------------------------------------------
# Shared player state (written by a background thread)
# --------------------------------------------------------------------------
class State:
    key = None
    lines: list = []
    playing = False
    pos = 0.0
    stamp = 0.0
    palette = None
    art = None            # raw image bytes
    title = artist = ""
    status = "idle"       # idle | searching | found | none
    source = ""           # which service the lyrics came from
    word_synced = False

    def now(self):
        return self.pos + (time.monotonic() - self.stamp if self.playing else 0)


STATE = State()


def dominant_color(data: bytes):
    img = QImage.fromData(data)
    if img.isNull():
        return None
    img = img.scaled(24, 24)
    best, best_score = None, -1
    for x in range(24):
        for y in range(24):
            c = img.pixelColor(x, y)
            score = c.saturationF() * c.valueF()
            if score > best_score:
                best, best_score = c, score
    if best:
        h, s, v, _ = best.getHsvF()
        return QColor.fromHsvF(max(h, 0), min(1.0, s + 0.1), max(v, 0.85))
    return None


async def media_loop():
    mgr = await SessionManager.request_async()
    while True:
        try:
            s = mgr.get_current_session()
            if s:
                props = await s.try_get_media_properties_async()
                tl = s.get_timeline_properties()
                info = s.get_playback_info()
                key = (props.artist, props.title)
                if key != STATE.key:
                    STATE.key, STATE.lines = key, []
                    STATE.title, STATE.artist = props.title, props.artist
                    STATE.status, STATE.palette, STATE.art = "searching", None, None
                    STATE.source, STATE.word_synced = "", False
                    dur = tl.end_time.total_seconds() - tl.start_time.total_seconds()
                    try:
                        st = await props.thumbnail.open_read_async()
                        rd = DataReader(st)
                        await rd.load_async(st.size)
                        buf = bytearray(st.size)
                        rd.read_bytes(buf)
                        STATE.art = bytes(buf)
                        STATE.palette = dominant_color(STATE.art)
                    except Exception:
                        pass
                    res = await asyncio.to_thread(find_lyrics, props.artist, props.title, dur, S)
                    if key == STATE.key:
                        STATE.lines = res.lines if res else []
                        STATE.source = res.source if res else ""
                        STATE.word_synced = bool(res and res.word_synced)
                        STATE.status = "found" if res else "none"
                pos = tl.position.total_seconds()
                playing = info.playback_status == PlayStatus.PLAYING
                if playing:
                    pos += (datetime.now(timezone.utc) - tl.last_updated_time).total_seconds()
                STATE.pos, STATE.stamp, STATE.playing = max(pos, 0), time.monotonic(), playing
            else:
                STATE.key, STATE.lines, STATE.playing, STATE.status = None, [], False, "idle"
                STATE.title = STATE.artist = ""
        except Exception as e:
            print("media error:", e)
        await asyncio.sleep(0.25)


def start_media_thread():
    threading.Thread(target=lambda: asyncio.run(media_loop()), daemon=True).start()


def apply_awake():
    flags = ES_CONTINUOUS | (ES_DISPLAY_REQUIRED if S["awake"] else 0)
    ctypes.windll.kernel32.SetThreadExecutionState(flags)


# --------------------------------------------------------------------------
# Lyrics overlay
# --------------------------------------------------------------------------
FS_STYLES = ("Fisheye", "Code", "Constellation")          # the fullscreen styles
STYLES = ["Stack", "Focus", "Fisheye", "Code", "Constellation"]
CODE_FONTS = ["Cascadia Mono", "Consolas", "SF Mono", "Menlo", "DejaVu Sans Mono", "Courier New"]
SYMBOL_FONTS = ["Segoe UI Symbol", "Segoe UI Emoji", "Noto Sans Symbols2", "Noto Sans Symbols", "DejaVu Sans"]
FS_STYLE = "Constellation"


def clamp01(v):
    return max(0.0, min(1.0, v))


def alpha(c, a):
    c = QColor(c)
    c.setAlpha(int(max(0, min(255, a))))
    return c


def interp(table, x):
    """Piecewise-linear lookup in [(x, y), ...]."""
    if x <= table[0][0]:
        return table[0][1]
    for (x0, y0), (x1, y1) in zip(table, table[1:]):
        if x <= x1:
            return y0 + (y1 - y0) * (x - x0) / (x1 - x0)
    return table[-1][1]


# Fisheye: size / vertical offset (fraction of screen height) / opacity of a word by its slot
# (0 = current, -1 = previous, +1 = next, as a continuous scroll position)
FISH_SIZE = [(-2.5, 0.10), (-2, 0.20), (-1, 0.46), (0, 1.0), (1, 0.40), (2, 0.20), (2.5, 0.10)]
FISH_Y = [(-2.5, -0.37), (-2, -0.32), (-1, -0.19), (0, 0.0), (1, 0.185), (2, 0.30), (2.5, 0.34)]
FISH_A = [(-2.5, 0.0), (-2, 0.18), (-1, 0.88), (0, 1.0), (1, 1.0), (2, 0.0), (2.5, 0.0)]
# Code: size / opacity of a line by its slot (0 = the line being typed, negative = older lines)
CODE_SIZE = [(-4, 0.58), (-3, 0.64), (-2, 0.72), (-1, 0.82), (0, 1.0), (1, 1.0)]
CODE_A = [(-4, 0.0), (-3, 0.30), (-2, 0.52), (-1, 0.76), (0, 1.0), (1, 0.15), (1.5, 0.0)]


class Scroll:
    """A scroll position that glides to its target (ease-out); can be sampled at any moment,
    which is what lets the styles render real motion blur."""

    def __init__(self):
        self.a = self.b = 0.0
        self.t0, self.dur = -10.0, 0.2

    def at(self, t):
        x = clamp01((t - self.t0) / self.dur)
        return self.a + (self.b - self.a) * (1 - (1 - x) ** 3)

    def go(self, target, t, dur, snap=False):
        if target == self.b and not snap:
            return
        self.a = target if snap else self.at(t)
        self.b, self.t0, self.dur = target, t, dur

    def speed(self, t):
        return abs(self.at(t) - self.at(t - 0.016)) / 0.016


@dataclass
class Node:
    id: int
    x: float
    y: float
    text: str
    shape: str          # star | dot
    side: str           # where the word sits relative to its marker
    born: float
    emph: float         # 0..1, how long the word is held
    t1: float           # media time when the word ends
    links: list = field(default_factory=list)
    ended: float = None


def star_path(r):
    """Four-point sparkle, like the markers in the reference style."""
    path = QPainterPath()
    path.moveTo(0, -r)
    path.quadTo(0, 0, r, 0)
    path.quadTo(0, 0, 0, r)
    path.quadTo(0, 0, -r, 0)
    path.quadTo(0, 0, 0, -r)
    return path


class Overlay(QWidget):
    MAX_NODES = 9

    def __init__(self):
        super().__init__()
        self._fs = None
        self.on_escape = lambda: None
        self.nodes = []
        self.ghosts = []                # (time retired, nodes) - old skies fading out
        self._clock = 0.0               # animation clock, only runs while music plays
        self._last_t = time.monotonic()
        self._last_key = None
        self._song = None
        self._next_id = 0
        self._shown_idx, self._change_t = None, -10.0   # line-change animation
        self._pulse = 0.0                               # border flash on every new word
        self._flat_key, self._flat, self._line_first = None, [], {}      # all words in order (Fisheye)
        self._fg_scroll, self._cd_scroll = Scroll(), Scroll()
        self._fg_line, self._fg_appear, self._fg_old, self._fg_u = None, -10.0, None, 0.0
        self._cd_line = None
        self._glyph_cache = {}
        self._bg_key, self._bg_pm = None, None          # album-art backdrop cache
        self._perim_key, self._perim_path, self._perim_len, self._stops = None, None, 1.0, []
        self._glow_img, self._bloom_pm, self._bloom_n = None, None, 0
        scr = QGuiApplication.primaryScreen()
        scr.geometryChanged.connect(self.refit)
        QGuiApplication.instance().primaryScreenChanged.connect(self.refit)
        QGuiApplication.instance().screenAdded.connect(self.refit)
        QGuiApplication.instance().screenRemoved.connect(self.refit)
        self.apply_mode()
        t = QTimer(self)
        t.timeout.connect(self.update)
        t.setTimerType(Qt.TimerType.PreciseTimer)
        t.start(16)

    def apply_mode(self):
        fs = S["style"] in FS_STYLES
        if not S["show"]:
            self.hide()
            self._fs = None
            return
        if fs == self._fs and self.isVisible():
            return
        self._fs = fs
        self.hide()
        flags = Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.Tool
        if not fs:
            flags |= Qt.WindowType.WindowTransparentForInput
        self.setWindowFlags(flags)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, not fs)
        self.setGeometry(QGuiApplication.primaryScreen().geometry())
        if fs:
            self.showFullScreen()
            self.activateWindow()
        else:
            self.show()

    def refit(self, *_):
        """Resolution / monitor changed: re-fit to the primary screen and rebuild the border."""
        self._perim_key = None
        self.setGeometry(QGuiApplication.primaryScreen().geometry())
        if self._fs and self.isVisible():
            self.showFullScreen()

    @staticmethod
    def mix(a, b, f):
        return QColor(int(a.red() + (b.red() - a.red()) * f),
                      int(a.green() + (b.green() - a.green()) * f),
                      int(a.blue() + (b.blue() - a.blue()) * f),
                      int(a.alpha() + (b.alpha() - a.alpha()) * f))

    def hi_color(self):
        return QColor(STATE.palette) if (S["auto"] and STATE.palette) else QColor(S["highlight"])

    # ---- Constellation style -----------------------------------------------
    def perimeter(self, w, h, sc):
        """Outline of THIS screen, plus a map from gradient angle -> position along the outline,
        so light can be driven by distance along the edge (even speed on every side)."""
        if self._perim_key != (w, h):
            inset, r = 3 * sc, min(w, h) * 0.028
            path = QPainterPath()
            path.addRoundedRect(QRectF(inset, inset, w - 2 * inset, h - 2 * inset), r, r)
            n, cx, cy = 720, w / 2, h / 2
            pts = [path.pointAtPercent(i / n) for i in range(0, n, 6)]
            items = sorted((math.degrees(math.atan2(cy - q.y(), q.x() - cx)) % 360, (i * 6) / n)
                           for i, q in enumerate(pts))
            a_l, s_l = items[-1]
            a_f, s_f = items[0]
            t0 = (360 - a_l) / (((360 - a_l) + a_f) or 1e-6)
            s0 = (s_l + t0 * (((s_f - s_l + 0.5) % 1.0) - 0.5)) % 1.0
            stops, last = [(0.0, s0)], 0.0
            for a, sv in items:
                if a / 360 > last + 1e-6:
                    stops.append((a / 360, sv))
                    last = a / 360
            stops.append((1.0, s0))
            self._perim_path, self._perim_len = path, path.length()
            self._stops, self._perim_key = stops, (w, h)
        return self._stops

    @staticmethod
    def analog(c, shift):
        """A neighbouring hue of the theme colour, for a richer multi-tone glow."""
        hh, ss, vv, _ = c.getHsvF()
        if ss < 0.12:                                   # white-ish theme: go cool / warm instead
            return QColor(150, 190, 255) if shift > 0 else QColor(255, 196, 150)
        return QColor.fromHsvF((max(hh, 0) + shift / 360.0) % 1.0, min(1.0, ss), 1.0)

    def draw_border(self, p, hi, t):
        """Soft ambient bloom that bleeds in from the screen edge. A few wide, slowly drifting
        patches of light (in neighbouring hues, whitening at the peaks) travel at an even speed
        along the edge; everything is blurred, with only a hairline core staying crisp."""
        w, h = self.width(), self.height()
        sc = min(2.0, max(0.75, h / 1080))
        stops = self.perimeter(w, h, sc)
        length, pulse = self._perim_len, self._pulse
        white = QColor(255, 255, 255)
        hi_rgb = (hi.red(), hi.green(), hi.blue())
        lobes = []                                      # (centre, sigma, amplitude, rgb)
        for speed, off, amp, sig, col in (
                (+0.30, 0.00, 0.80, 0.24, self.mix(hi, white, 0.40)),
                (-0.19, 0.37, 0.62, 0.30, self.analog(hi, +18)),
                (+0.12, 0.68, 0.50, 0.36, self.analog(hi, -18))):
            lobes.append(((off + speed * h / length * t) % 1.0, sig * h / length,
                          amp * (1 + 0.35 * pulse), (col.red(), col.green(), col.blue())))
        base = 0.22 + 0.05 * math.sin(t * 0.9) + 0.22 * pulse

        g = QConicalGradient(w / 2, h / 2, 0)
        for pos, sv in stops:
            tot, r, gr, b = base, hi_rgb[0] * base, hi_rgb[1] * base, hi_rgb[2] * base
            peak = 0.0
            for c, sg, amp, rgb in lobes:
                d = abs(sv - c)
                d = min(d, 1 - d)
                wgt = amp * math.exp(-0.5 * (d / sg) ** 2)
                tot += wgt
                r += rgb[0] * wgt
                gr += rgb[1] * wgt
                b += rgb[2] * wgt
                peak += wgt
            wh = clamp01(peak * 0.35)                  # peaks glow whiter
            r, gr, b = r / tot, gr / tot, b / tot
            col = QColor(int(r + (255 - r) * wh), int(gr + (255 - gr) * wh), int(b + (255 - b) * wh))
            shimmer = 1 + 0.07 * math.sin(2 * math.pi * (sv * 9 - t * 0.25))
            col.setAlpha(int(255 * clamp01(tot * shimmer)))
            g.setColorAt(pos, col)
        brush = QBrush(g)

        # blurred bloom, refreshed at 20 fps and cached: concentric strokes whose opacities are
        # solved so the glow falls off smoothly (exponentially) with distance from the edge
        reach = h * 0.09
        th = min(reach * 1.1, h / 2, w / 2)
        strips = ((0, 0, w, th), (0, h - th, w, th), (0, th, th, h - 2 * th), (w - th, th, th, h - 2 * th))
        self._bloom_n += 1
        if (self._bloom_pm is None or self._bloom_pm.width() != w or self._bloom_pm.height() != h
                or self._bloom_n % 3 == 0):
            iw, ih = max(8, w // 6), max(8, h // 6)
            if self._glow_img is None or self._glow_img.width() != iw or self._glow_img.height() != ih:
                self._glow_img = QImage(iw, ih, QImage.Format.Format_ARGB32_Premultiplied)
            img = self._glow_img
            img.fill(QColor(0, 0, 0, 0))
            gp = QPainter(img)
            gp.setRenderHint(QPainter.RenderHint.Antialiasing)
            gp.scale(iw / w, ih / h)
            gp.setBrush(Qt.BrushStyle.NoBrush)
            n_layers, acc_next = 10, 0.0
            for k in range(n_layers, 0, -1):               # outermost layer first
                frac = k / n_layers
                acc = 0.85 * math.exp(-(frac - 0.5 / n_layers) / 0.27)
                op = clamp01(1 - (1 - acc) / (1 - acc_next))
                acc_next = acc
                gp.setOpacity(op)
                gp.setPen(QPen(brush, 2 * reach * frac))
                gp.drawPath(self._perim_path)
            gp.end()
            if self._bloom_pm is None or self._bloom_pm.width() != w or self._bloom_pm.height() != h:
                self._bloom_pm = QPixmap(w, h)
            self._bloom_pm.fill(QColor(0, 0, 0, 0))
            bp = QPainter(self._bloom_pm)
            bp.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
            kx, ky = iw / w, ih / h
            for x, y, sw, sh in strips:
                bp.drawImage(QRectF(x, y, sw, sh), img, QRectF(x * kx, y * ky, sw * kx, sh * ky))
            bp.end()
        for x, y, sw, sh in strips:
            p.drawPixmap(QRectF(x, y, sw, sh), self._bloom_pm, QRectF(x, y, sw, sh))

        # hairline core, crisp at full resolution
        p.setOpacity(0.8)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QPen(brush, 1.7 * sc))
        p.drawPath(self._perim_path)
        p.setOpacity(1.0)

    def update_nodes(self, lines, now):
        if STATE.key != self._song:                     # new song: fresh sky
            self._retire_nodes()
            self._song, self._last_key = STATE.key, None
        if not lines:
            return
        idx = -1
        for i, l in enumerate(lines):
            if l.start <= now:
                idx = i
        if idx < 0 or not lines[idx].words:
            return
        cur = -1
        for i, (_, t0, _) in enumerate(lines[idx].words):
            if t0 <= now:
                cur = i
        if cur < 0 or (idx, cur) == self._last_key:
            return
        lk = self._last_key
        adjacent = lk and ((idx == lk[0] and 0 < cur - lk[1] <= 3) or (idx == lk[0] + 1 and cur <= 2))
        reset = not adjacent                            # seeked: don't link across a jump
        if lk and idx != lk[0] and idx > 0:             # long instrumental gap: new sky
            pl = lines[idx - 1]
            prev_end = pl.words[-1][2] if pl.words else pl.start
            if lines[idx].start - prev_end > 2.0:
                reset = True
        if len(self.nodes) >= 16:                       # sky is full: start over
            reset = True
        if reset:
            self._retire_nodes()
        self._last_key = (idx, cur)
        word, t0, t1 = lines[idx].words[cur]
        self.add_node(idx, cur, word, clamp01((t1 - t0 - 0.2) / 0.9), t1)

    def _retire_nodes(self):
        """Don't cut the old constellation: let it blur and fade out while the new one begins."""
        if self.nodes:
            for nd in self.nodes:
                if nd.ended is None:
                    nd.ended = self._clock
            self.ghosts.append((self._clock, self.nodes))
        self.nodes = []

    def add_node(self, idx, cur, text, emph, t1):
        w, h = self.width(), self.height()
        rng = random.Random(idx * 1009 + cur * 31 + (hash(STATE.key) & 0xFFFF))
        prev = self.nodes[-1] if self.nodes else None
        if prev:
            x0, x1, y0, y1 = w * 0.16, w * 0.84, h * 0.38, h * 0.72
            if prev.ended is None:
                prev.ended = self._clock
        else:                                           # first word of a fresh sky: near the middle
            x0, x1, y0, y1 = w * 0.36, w * 0.64, h * 0.46, h * 0.62
        best, best_score = ((x0 + x1) / 2, (y0 + y1) / 2), -1.0
        for _ in range(80):
            x, y = rng.uniform(x0, x1), rng.uniform(y0, y1)
            score = min([math.hypot(x - n.x, y - n.y) for n in self.nodes] or [h])
            if prev:
                d = math.hypot(x - prev.x, y - prev.y)
                if d < w * 0.08 or d > w * 0.30:        # keep the connecting lines readable
                    score *= 0.25
            if score > best_score:
                best, best_score = (x, y), score
        links = [prev.id] if prev else []
        if len(self.nodes) >= 3 and rng.random() < 0.4:  # extra link back to a nearby older word
            near = sorted(self.nodes[:-1], key=lambda n: math.hypot(n.x - best[0], n.y - best[1]))[:3]
            links.append(rng.choice(near).id)
        shape = "dot" if emph < 0.15 and rng.random() < 0.18 else "star"
        side = rng.choice(["below", "left", "right", "above"])
        self.nodes.append(Node(self._next_id, best[0], best[1], text, shape, side,
                               self._clock, emph, t1, links))
        self._next_id += 1
        self._pulse = 1.0

    @staticmethod
    def rank_alpha(rank, current):
        return 1.0 if current else max(0.42, 0.78 - 0.1 * (rank - 1))

    def draw_word(self, p, text, font, col, a, tx, ty, blur):
        """Draw a word, optionally defocused. Blur = render small, scale down and back up smoothly."""
        if blur < 0.7:
            p.setFont(font)
            p.setPen(alpha(col, 255 * a))
            p.drawText(QPointF(tx, ty), text)
            return
        fm = QFontMetrics(font)
        pad = int(blur * 2) + 4
        wpx, hpx = fm.horizontalAdvance(text) + 2 * pad, fm.height() + 2 * pad
        pm = QPixmap(wpx, hpx)
        pm.fill(QColor(0, 0, 0, 0))
        q = QPainter(pm)
        q.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        q.setFont(font)
        q.setPen(QColor(col.red(), col.green(), col.blue()))
        q.drawText(pad, pad + fm.ascent(), text)
        q.end()
        f = 1.0 / (1.0 + blur * 0.55)
        small = pm.scaled(max(2, int(wpx * f)), max(2, int(hpx * f)),
                          Qt.AspectRatioMode.IgnoreAspectRatio, Qt.TransformationMode.SmoothTransformation)
        big = small.scaled(wpx, hpx, Qt.AspectRatioMode.IgnoreAspectRatio, Qt.TransformationMode.SmoothTransformation)
        p.save()
        p.setOpacity(p.opacity() * clamp01(a) * (1 - 0.3 * clamp01(blur / 10)))
        p.drawPixmap(QPointF(tx - pad, ty - pad - fm.ascent()), big)
        p.restore()

    def draw_constellation(self, p, hi, t, media_now):
        self.ghosts = [g for g in self.ghosts if self._clock - g[0] < 1.0]
        for born, nodes in self.ghosts:                    # the previous sky melting away
            self._draw_nodes(p, nodes, hi, t, media_now, clamp01((self._clock - born) / 0.9), True)
        self._draw_nodes(p, self.nodes, hi, t, media_now, 0.0, False)

    def _draw_nodes(self, p, nodes, hi, t, media_now, out, frozen):
        n = len(nodes)
        if not n:
            return
        w, h = self.width(), self.height()
        k = max(0.8, h / 800)
        cream = self.mix(hi, QColor(255, 255, 255), 0.5)
        ease_out = 1 - (1 - out) ** 3
        fade, lift, blur_out = 1 - ease_out, -ease_out * 22 * k, ease_out * 12
        for i, nd in enumerate(nodes):                      # a word stops being "current" when it ends
            if nd.ended is None and (frozen or i < n - 1 or media_now > nd.t1 + 0.35):
                nd.ended = self._clock
        alphas = [self.rank_alpha(n - 1 - i, nd.ended is None) for i, nd in enumerate(nodes)]
        pos = {nd.id: (nd.x + 2.4 * k * math.sin(t * 0.7 + nd.id * 1.3),          # gentle idle drift
                       nd.y + 2.4 * k * math.cos(t * 0.6 + nd.id * 0.9) + lift) for nd in nodes}
        index = {nd.id: i for i, nd in enumerate(nodes)}

        # connecting lines (a new line grows from the older word to the new one)
        for i, nd in enumerate(nodes):
            for lid in nd.links:
                j = index.get(lid)
                if j is None:
                    continue
                ax, ay = pos[nodes[j].id]
                bx, by = pos[nd.id]
                g = clamp01((self._clock - nd.born) / 0.55)
                g = 1 - (1 - g) ** 3
                la = 0.8 if nd.ended is None else 0.4 * min(alphas[i], alphas[j]) / 0.45
                p.setPen(QPen(alpha(hi, 150 * min(la, 0.8) * fade), 1.1))
                p.drawLine(QPointF(ax, ay), QPointF(ax + (bx - ax) * g, ay + (by - ay) * g))

        for i, nd in enumerate(nodes):
            is_cur = nd.ended is None
            age = self._clock - nd.born
            enter = clamp01(age / 0.5)
            enter = 1 - (1 - enter) ** 3                    # eases in; words also come into focus
            al = alphas[i] * enter * fade
            if al <= 0.01:
                continue
            px, py = pos[nd.id]
            e = nd.emph
            settle = 0.0 if is_cur else clamp01((self._clock - nd.ended) / 0.35)
            settle = 1 - (1 - settle) ** 3
            pop = 1 + 0.18 * max(0.0, 1 - age / 0.5) ** 2
            blur = 9 * (1 - enter) + blur_out

            # sizes: big while the word is sung (more so if it is held), small once it has passed
            font_cur, font_set = h * (0.034 + 0.050 * e), h * (0.020 + 0.022 * e)
            r_cur, r_set = (10 + 12 * e) * k, (6 + 6 * e) * k
            font_px = font_cur + (font_set - font_cur) * settle
            r = r_cur + (r_set - r_cur) * settle
            col = cream if is_cur else hi
            twinkle = 0.85 + 0.15 * math.sin(t * 3 + i * 1.7)

            if is_cur:
                gr = QRadialGradient(QPointF(px, py), 56 * k)
                gr.setColorAt(0, alpha(hi, 100 * al))
                gr.setColorAt(1, alpha(hi, 0))
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(QBrush(gr))
                p.drawEllipse(QPointF(px, py), 56 * k, 56 * k)

            p.save()
            p.translate(px, py)
            p.scale(pop, pop)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(alpha(col, 255 * al * twinkle))
            if nd.shape == "dot" and not is_cur:
                p.drawEllipse(QPointF(0, 0), r * 0.5, r * 0.5)
            else:
                p.drawPath(star_path(r))
            if is_cur:
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.setPen(QPen(alpha(col, 230 * al), 1.5))
                rr = r + (7 + 1.5 * math.sin(t * 4)) * k
                p.drawEllipse(QPointF(0, 0), rr, rr)
            p.restore()

            f = QFont()
            f.setFamilies(FONT_FAMILIES)
            f.setPixelSize(max(9, int(font_px)))
            f.setWeight(QFont.Weight.Bold if is_cur else QFont.Weight.Medium)
            fm = QFontMetrics(f)
            tw = fm.horizontalAdvance(nd.text)
            gap = r + 7 * k
            if nd.side == "below":
                tx, ty = px - tw / 2, py + gap + fm.ascent()
            elif nd.side == "above":
                tx, ty = px - tw / 2, py - gap
            elif nd.side == "left":
                tx, ty = px - gap - tw, py + fm.ascent() * 0.35
            else:
                tx, ty = px + gap, py + fm.ascent() * 0.35
            tx = min(max(tx, 14), w - 14 - tw)
            ty = min(max(ty, fm.ascent() + 10), h - 14)
            p.save()
            if is_cur and pop > 1.001:
                cx, cy = tx + tw / 2, ty - fm.ascent() * 0.35
                sc = 1 + (pop - 1) * 0.6
                p.translate(cx, cy)
                p.scale(sc, sc)
                p.translate(-cx, -cy)
            if is_cur and not frozen:                       # soft out-of-focus halo behind the word
                self.draw_word(p, nd.text, f, hi, 0.5 * al, tx, ty, 9.0)
            self.draw_word(p, nd.text, f, col, al, tx, ty, blur)
            p.restore()

    # ---- Fisheye -------------------------------------------------------------
    def draw_art_bg(self, p):
        """Album cover filling the screen, softened and dimmed so the words stay readable."""
        if not STATE.art:
            return
        w, h = self.width(), self.height()
        key = (id(STATE.art), w, h)
        if self._bg_key != key:
            self._bg_key, self._bg_pm = key, None
            src = QPixmap()
            if src.loadFromData(STATE.art):
                sc = src.scaled(w, h, Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                                Qt.TransformationMode.SmoothTransformation)
                pm = sc.copy((sc.width() - w) // 2, (sc.height() - h) // 2, w, h)
                pm = pm.scaled(max(2, w // 2), max(2, h // 2), Qt.AspectRatioMode.IgnoreAspectRatio,
                               Qt.TransformationMode.SmoothTransformation).scaled(
                    w, h, Qt.AspectRatioMode.IgnoreAspectRatio, Qt.TransformationMode.SmoothTransformation)
                q = QPainter(pm)
                q.fillRect(pm.rect(), QColor(0, 0, 0, 105))
                vg = QRadialGradient(QPointF(w / 2, h / 2), max(w, h) * 0.75)
                vg.setColorAt(0.45, QColor(0, 0, 0, 0))
                vg.setColorAt(1.0, QColor(0, 0, 0, 170))
                q.fillRect(pm.rect(), QBrush(vg))
                q.end()
                self._bg_pm = pm
        if self._bg_pm:
            p.drawPixmap(0, 0, self._bg_pm)

    def _flat_words(self, lines):
        key = (STATE.key, id(lines), len(lines))
        if self._flat_key != key:
            self._flat_key = key
            self._flat = [(w, t0, t1, li) for li, l in enumerate(lines) for (w, t0, t1) in l.words]
            self._line_first = {}
            for i, wd in enumerate(self._flat):
                self._line_first.setdefault(wd[3], i)
        return self._flat

    def _blur_draw(self, p, draw, t, speed):
        """Real motion blur: draw the scene at several moments inside a short 'shutter' and average
        them. Only runs while things are moving; still frames are drawn directly and stay crisp."""
        if speed < 0.8:
            draw(p, t)
            return
        n = 6 if speed > 4 else 4
        shutter = min(0.06, 0.03 + 0.003 * speed)
        w, h = self.width(), self.height()
        sc = 0.75
        iw, ih = int(w * sc), int(h * sc)
        acc = QImage(iw, ih, QImage.Format.Format_ARGB32_Premultiplied)
        acc.fill(QColor(0, 0, 0, 0))
        layer = QImage(iw, ih, QImage.Format.Format_ARGB32_Premultiplied)
        ap = QPainter(acc)
        ap.setCompositionMode(QPainter.CompositionMode.CompositionMode_Plus)
        ap.setOpacity(1.0 / n)
        for j in range(n):
            layer.fill(QColor(0, 0, 0, 0))
            lp = QPainter(layer)
            lp.setRenderHint(QPainter.RenderHint.Antialiasing)
            lp.setRenderHint(QPainter.RenderHint.TextAntialiasing)
            lp.scale(sc, sc)
            draw(lp, t - shutter * j / (n - 1))
            lp.end()
            ap.drawImage(0, 0, layer)
        ap.end()
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        p.drawImage(QRectF(0, 0, w, h), acc)

    def draw_clock(self, p):
        w, h = self.width(), self.height()
        f = QFont()
        f.setFamilies(["Segoe UI Variable Display", "Segoe UI Light", "Segoe UI"])
        f.setPixelSize(int(h * 0.095))
        f.setWeight(QFont.Weight.Light)
        p.setFont(f)
        p.setPen(QColor(215, 215, 222, 225))
        p.drawText(QRectF(0, h * 0.10, w, h * 0.12), Qt.AlignmentFlag.AlignCenter, time.strftime("%I:%M").lstrip("0"))
        f2 = QFont()
        f2.setFamilies(FONT_FAMILIES)
        f2.setPixelSize(int(h * 0.024))
        f2.setWeight(QFont.Weight.Bold)
        p.setFont(f2)
        p.setPen(QColor(230, 230, 236, 215))
        p.drawText(QRectF(0, h * 0.062, w, h * 0.04), Qt.AlignmentFlag.AlignCenter, time.strftime("%a %b %d"))

    # ---- Fisheye -------------------------------------------------------------
    def draw_fisheye(self, p, hi, t, now):
        """Previous word small & italic on top, the current word big in the middle, the next word as a
        small orange box below. Words glide up a slot at a time (the next word's box fills in as it
        reaches the middle), with motion blur and a stretch while moving. A new line starts with its
        first word waiting in the middle as an orange block."""
        words = self._flat_words(STATE.lines)
        if S["clock"]:
            self.draw_clock(p)
        n = len(words)
        if not n:
            return
        idx = -1
        for i, wd in enumerate(words):
            if wd[1] > now:
                break
            idx = i
        pending, nxt = None, idx + 1
        if nxt < n and (idx < 0 or words[nxt][3] != words[idx][3]):
            if now >= words[nxt][1] - 0.7 and (idx < 0 or now > words[idx][2]):
                pending = nxt                                    # first word of a line, waiting
        if idx < 0 and pending is None:
            return
        target = pending if pending is not None else idx
        group = words[target][3]
        if group != self._fg_line:                               # a new line: old words drop away
            if self._fg_line is not None:
                self._fg_old = (self._fg_line, self._fg_u, t)
            self._fg_line, self._fg_appear = group, t
            self._fg_scroll.go(float(target), t, 0.2, snap=True)
        else:
            dur = min(0.22, max(0.09, 0.6 * (words[target][2] - words[target][1])))
            self._fg_scroll.go(float(target), t, dur, snap=abs(target - self._fg_scroll.at(t)) > 3.5)
        self._fg_u = self._fg_scroll.at(t)
        enter = clamp01((t - self._fg_appear) / 0.18)
        speed = self._fg_scroll.speed(t)
        sp = clamp01(speed / 9.0)
        self._blur_draw(p, lambda lp, tt: self._fish_items(lp, group, self._fg_scroll.at(tt), now, 1.0, sp, enter, hi),
                        t, speed)
        if self._fg_old and t - self._fg_old[2] < 0.14:
            og, ou, ot = self._fg_old
            self._fish_items(p, og, ou, now, 1 - (t - ot) / 0.14, 0.0, 1.0, hi)

    def _fish_items(self, lp, group, u, now, am, sp, enter, hi):
        words = self._flat
        lo, hi_i = max(0, int(u) - 3), min(len(words), int(u) + 4)
        for i in range(lo, hi_i):                                # past words first, next word on top
            if words[i][3] == group and abs(i - u) <= 2.5:
                self._fish_item(lp, i, i - u, now, am, sp, enter, hi)

    def _glyph_pm(self, glyph, px, rgb):
        """The icon as a silhouette filled with the theme colour (colour-emoji fonts would otherwise
        ignore it). Drawn at 2x and cached."""
        key = (glyph, px, rgb)
        pm = self._glyph_cache.get(key)
        if pm is None:
            f = QFont()
            f.setFamilies(SYMBOL_FONTS)
            f.setPixelSize(px * 2)
            fm = QFontMetrics(f)
            pm = QPixmap(fm.horizontalAdvance(glyph) + 4, fm.height() + 4)
            pm.fill(QColor(0, 0, 0, 0))
            q = QPainter(pm)
            q.setRenderHint(QPainter.RenderHint.TextAntialiasing)
            q.setFont(f)
            q.setPen(QColor(255, 255, 255))
            q.drawText(2, 2 + fm.ascent(), glyph)
            q.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceIn)
            q.fillRect(pm.rect(), QColor(*rgb))
            q.end()
            if len(self._glyph_cache) > 64:
                self._glyph_cache.clear()
            self._glyph_cache[key] = pm
        return pm

    def _fish_item(self, lp, i, pos, now, am, sp, enter, hi):
        text, t0, t1, li = self._flat[i]
        w, h = self.width(), self.height()
        f, a = interp(FISH_SIZE, pos), interp(FISH_A, pos) * am
        if f <= 0.02 or a <= 0.01:
            return
        box = clamp01(pos)                                       # waiting words are orange boxes...
        sx, sy = 1 + 0.28 * sp, 1 - 0.14 * sp                    # ...stretched wide while moving
        if i == self._line_first.get(li):
            box = 1.0 if now < t0 else max(box, 1 - clamp01((now - t0) / 0.14))
            e = (1 - enter) ** 2
            a *= enter
            sx *= 1 + 0.45 * e
            sy *= 1 - 0.30 * e
        ital = clamp01(abs(pos))
        big = int(h * 0.19)
        font = QFont()
        font.setFamilies(FONT_FAMILIES)
        font.setPixelSize(big)
        font.setWeight(QFont.Weight.Bold)
        fm = QFontMetrics(font)
        tw = fm.horizontalAdvance(text)
        glyph = symbol_for(text) if S["icons"] else None
        gpm = self._glyph_pm(glyph, int(big * 0.78), (hi.red(), hi.green(), hi.blue())) if glyph else None
        gw = gpm.width() / 2 if gpm else 0
        gap = big * 0.16 if glyph else 0
        total = tw + gw + gap
        fit = min(1.0, w * 0.80 / max(total, 1))
        left = glyph in ("♥", "♡")
        xg = -total / 2 if left else -total / 2 + tw + gap
        xt = -total / 2 + gw + gap if left else -total / 2
        base = fm.ascent() * 0.36
        cream, dark = self.mix(hi, QColor(255, 255, 255), 0.55), QColor(48, 26, 6)
        lp.save()
        lp.translate(w / 2, h * 0.54 + interp(FISH_Y, pos) * h)
        lp.scale(f * fit * sx, f * fit * sy)
        lp.shear(-0.2 * ital, 0)
        if box > 0.01:
            path = QPainterPath()
            path.addRoundedRect(QRectF(xt - big * 0.12, base - fm.ascent() * 0.86, tw + big * 0.24,
                                       fm.ascent() * 1.02), big * 0.07, big * 0.07)
            lp.setPen(Qt.PenStyle.NoPen)
            lp.setBrush(alpha(hi, 255 * box * a))
            lp.drawPath(path)
        lp.setFont(font)
        lp.setPen(alpha(self.mix(cream, dark, box), 255 * a))
        lp.drawText(QPointF(xt, base), text)
        if gpm:                                                  # icon: a solid shape in the theme colour
            lp.setOpacity(lp.opacity() * clamp01(a))
            lp.drawPixmap(QRectF(xg - 1, base - gpm.height() / 2 * 0.78, gpm.width() / 2, gpm.height() / 2), gpm,
                          QRectF(gpm.rect()))
        lp.restore()

    # ---- Code ----------------------------------------------------------------
    def draw_code(self, p, hi, t, now):
        """Lyrics typed like code with a red block cursor. When the next line starts, the finished line
        becomes dotted.words, a // comment or a call(...); and everything scrolls up with motion blur."""
        lines = STATE.lines
        if not lines:
            return
        idx = -1
        for i, l in enumerate(lines):
            if l.start <= now:
                idx = i
        if idx < 0 or not lines[idx].words:
            return
        if idx != self._cd_line:
            self._cd_scroll.go(float(idx), t, 0.20, snap=(self._cd_line is None or abs(idx - self._cd_scroll.at(t)) > 2.5))
            self._cd_line = idx
        self._blur_draw(p, lambda lp, tt: self._code_stack(lp, tt, now, hi, idx), t, self._cd_scroll.speed(t))

    def _code_stack(self, lp, tt, now, hi, idx):
        lines, (w, h) = STATE.lines, (self.width(), self.height())
        u = self._cd_scroll.at(tt)
        font = QFont()
        font.setFamilies(CODE_FONTS)
        font.setStyleHint(QFont.StyleHint.Monospace)
        font.setPixelSize(int(h * 0.072))
        font.setWeight(QFont.Weight.Bold)
        fm = QFontMetrics(font)
        adv = fm.horizontalAdvance
        white, black = QColor(255, 255, 255), QColor(0, 0, 0)
        accent, dim, coral = self.mix(hi, white, 0.35), self.mix(hi, black, 0.30), QColor(255, 92, 102)
        cols = {"word": hi, "pron": coral, "dot": dim, "comment": dim, "name": self.mix(hi, white, 0.15),
                "arg": accent, "punct": dim}
        base_y = fm.ascent() * 0.35
        for j in range(max(0, idx - 4), idx + 1):
            words = [(clean_word(x), t0, t1) for x, t0, t1 in lines[j].words if clean_word(x)]
            if not words:
                continue
            pos = j - u
            f, a = interp(CODE_SIZE, pos), interp(CODE_A, pos)
            if a < 0.02:
                continue
            seg, kind = code_segments(words)
            full = " ".join(x for x, _, _ in words)
            prefix = "// " if kind == "comment" else ""
            fit = min(1.0, w * 0.88 / max(adv(prefix + full), sum(adv(s) for s, _ in seg), 1))
            lp.save()
            lp.translate(w / 2, h * 0.60 + pos * h * 0.118)
            lp.scale(f * fit, f * fit)
            lp.setFont(font)
            if j == idx:                                         # the line being typed
                typed = typed_text(words, now)
                x0 = -adv(prefix + full) / 2
                if prefix and len(typed) >= 2:
                    lp.setPen(alpha(dim, 255 * a))
                    lp.drawText(QPointF(x0, base_y), prefix)
                lp.setPen(alpha(accent, 255 * a))
                lp.drawText(QPointF(x0 + adv(prefix), base_y), typed)
                if typed != full or int(now * 2) % 2 == 0:       # solid while typing, blinks when idle
                    cx = x0 + adv(prefix + typed)
                    lp.setPen(Qt.PenStyle.NoPen)
                    lp.setBrush(alpha(coral, 240 * a))
                    lp.drawRect(QRectF(cx + 1, base_y - fm.ascent() * 0.93, adv("M") * 0.62, fm.ascent() * 1.05))
            else:                                                # finished line
                prog = clamp01(u - j) if j == idx - 1 else 1.0   # cross-fade from the typed form
                if prog < 1:
                    lp.setPen(alpha(accent, 255 * a * (1 - prog)))
                    lp.drawText(QPointF(-adv(prefix + full) / 2, base_y), prefix + full)
                x = -sum(adv(s) for s, _ in seg) / 2
                for text, role in seg:
                    lp.setPen(alpha(cols[role], 255 * a * prog))
                    lp.drawText(QPointF(x, base_y), text)
                    x += adv(text)
            lp.restore()

    # ---- Stack / Focus styles -------------------------------------------------
    def draw_line(self, p, line, y, now, scale, amul, state, k=1.0, rise=False):
        """state: active (karaoke wipe + lift) | past | future.  amul: overall opacity."""
        font = QFont()
        font.setFamilies(FONT_FAMILIES)
        font.setWeight(QFont.Weight.Bold)
        size = max(8, int(self.height() * 0.055 * scale))
        font.setPixelSize(size)
        fm = QFontMetrics(font)
        text_w = fm.horizontalAdvance(" ".join(w for w, _, _ in line.words))
        max_w = self.width() * 0.88
        if text_w > max_w:
            size = max(8, int(size * max_w / text_w))
            font.setPixelSize(size)
            fm = QFontMetrics(font)
        p.setFont(font)
        space = fm.horizontalAdvance(" ")
        widths = [fm.horizontalAdvance(w) for w, _, _ in line.words]
        x = (self.width() - (sum(widths) + space * (len(widths) - 1))) / 2
        xs = []
        for wd in widths:
            xs.append(x)
            x += wd + space

        hi = self.hi_color()
        unlit = QColor(S["lyric"]); unlit.setAlpha(int(110 * amul))
        side = QColor(S["secondary"]); side.setAlpha(int(150 * amul))
        past = self.mix(side, alpha(hi, 150 * amul), 1 - k)   # just-finished line cools down

        for i, (w, t0, t1) in enumerate(line.words):
            x, wd = xs[i], widths[i]
            if state != "active":
                p.setPen(past if state == "past" else side)
                p.drawText(QPointF(x, y), w)
                continue
            f = clamp01((now - t0) / max(t1 - t0, 0.05))
            dy, wa = 0.0, 1.0
            if rise:                                    # Focus: words float up into place
                ap = clamp01((now - (t0 - 0.5)) / 0.5)
                ap = 1 - (1 - ap) ** 3
                dy += (1 - ap) * size * 0.3
                wa = 0.35 + 0.65 * ap
            bump = math.sin(math.pi * f) if 0 < f < 1 else 0.0
            dy -= size * 0.10 * bump                    # the word being sung lifts and swells
            cx, cy = x + wd / 2, y - fm.ascent() * 0.35 + dy
            sw = 1 + 0.07 * bump
            p.save()
            p.translate(cx, cy)
            p.scale(sw, sw)
            p.translate(-cx, -cy)
            if bump > 0.03:                             # soft glow behind it
                path = QPainterPath()
                path.addText(QPointF(x, y + dy), font, w)
                for gw, ga in ((0.42, 14), (0.28, 22), (0.15, 34)):     # layered = soft falloff
                    p.strokePath(path, QPen(alpha(hi, ga * bump * amul), size * gw,
                                            Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap,
                                            Qt.PenJoinStyle.RoundJoin))
            un = QColor(unlit); un.setAlpha(int(un.alpha() * wa))
            p.setPen(un)
            p.drawText(QPointF(x, y + dy), w)
            if f > 0:                                   # karaoke wipe, left to right
                if f < 1:
                    p.setClipRect(QRectF(x - 4, y + dy - fm.ascent() - 8, wd * f + 4, fm.height() + 16))
                p.setPen(alpha(hi, 255 * amul * wa))
                p.drawText(QPointF(x, y + dy), w)
            p.restore()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        now_t = time.monotonic()
        dt = min(0.05, now_t - self._last_t)
        self._last_t = now_t
        self._pulse = max(0.0, self._pulse - dt * 1.6)
        if STATE.playing:
            self._clock += dt
        lines, now = STATE.lines, STATE.now()
        style = S["style"]

        if style in FS_STYLES:
            p.fillRect(self.rect(), QColor(0, 0, 0))
            hi = self.hi_color()
            if style == "Fisheye" and S["art_bg"]:
                self.draw_art_bg(p)
            if S["glow"]:
                self.draw_border(p, hi, now_t)
            if style == "Constellation":
                self.update_nodes(lines, now)
                self.draw_constellation(p, hi, now_t, now)
            elif style == "Fisheye":
                self.draw_fisheye(p, hi, now_t, now)
            else:
                self.draw_code(p, hi, now_t, now)
            return

        if not lines or not STATE.playing:
            return
        idx = -1
        for i, l in enumerate(lines):
            if l.start <= now:
                idx = i
        if idx < 0 or not lines[idx].words:
            return

        # animate the move from one line to the next (no animation after a seek)
        if idx != self._shown_idx:
            old, self._shown_idx = self._shown_idx, idx
            self._change_t = now_t if (old is not None and abs(idx - old) == 1) else -10.0
        k = clamp01((now_t - self._change_t) / 0.5)
        k = 1 - (1 - k) ** 3                            # ease-out

        h = self.height()
        if style == "Stack":
            cy, step = h * 0.76, h * 0.085
            for slot in (-2, -1, 0, 1, 2):
                li = idx + slot
                if li < 0 or li >= len(lines) or not lines[li].words:
                    continue
                pos = slot + (1 - k)                    # lines glide up as the song moves on
                a = clamp01(2 - abs(pos))
                if a < 0.03:
                    continue
                state = "active" if slot == 0 else ("past" if slot < 0 else "future")
                self.draw_line(p, lines[li], cy + pos * step, now,
                               1.0 - 0.25 * min(abs(pos), 1.0), a, state,
                               k if slot == -1 else 1.0)
        else:                                           # Focus
            cy, step = h * 0.80, h * 0.07
            for slot in (-1, 0):
                li = idx + slot
                if li < 0 or not lines[li].words:
                    continue
                a = k if slot == 0 else 1 - k
                if a < 0.03:
                    continue
                pos = slot + (1 - k)
                self.draw_line(p, lines[li], cy + pos * step, now, 1.15, a,
                               "active" if slot == 0 else "past", k if slot == -1 else 1.0,
                               rise=True)

    def keyPressEvent(self, e):
        if e.key() == Qt.Key.Key_Escape and S["style"] in FS_STYLES:
            S["style"] = "Stack"
            S.save()
            self.apply_mode()
            self.on_escape()


# --------------------------------------------------------------------------
# macOS-style controls
# --------------------------------------------------------------------------
class Switch(QWidget):
    toggled = pyqtSignal(bool)

    def __init__(self, on=False):
        super().__init__()
        self.setFixedSize(42, 26)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._on, self._p = on, 1.0 if on else 0.0
        self.anim = QVariantAnimation(self, duration=140)
        self.anim.valueChanged.connect(self._tick)

    def _tick(self, v):
        self._p = float(v)
        self.update()

    def set_on(self, on):
        self._on = on
        self.anim.stop()
        self.anim.setStartValue(self._p)
        self.anim.setEndValue(1.0 if on else 0.0)
        self.anim.start()

    def mousePressEvent(self, _):
        self.set_on(not self._on)
        self.toggled.emit(self._on)

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        off, on = QColor(90, 90, 94), QColor(52, 199, 89)
        p.setBrush(Overlay.mix(off, on, self._p))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawRoundedRect(0, 0, 42, 26, 13, 13)
        p.setBrush(QColor(255, 255, 255))
        p.drawEllipse(int(2 + 16 * self._p), 2, 22, 22)


class Segmented(QWidget):
    """Pill selector; wraps into rows of `cols`."""
    changed = pyqtSignal(str)

    def __init__(self, options, current, cols=None):
        super().__init__()
        self.options, self.current = options, current
        self.cols = cols or len(options)
        self.rows = -(-len(options) // self.cols)
        self.setFixedHeight(32 * self.rows + 4 * (self.rows - 1))
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def set(self, v):
        self.current = v
        self.update()

    def _cell(self, i):
        r, c = divmod(i, self.cols)
        in_row = min(self.cols, len(self.options) - r * self.cols)      # last row stretches to fill
        cw = self.width() / in_row
        return QRectF(c * cw, r * 36, cw, 32)

    def mousePressEvent(self, e):
        for i, name in enumerate(self.options):
            if self._cell(i).contains(e.position()) and name != self.current:
                self.current = name
                self.update()
                self.changed.emit(name)
                return

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        f = QFont(); f.setFamilies(FONT_FAMILIES); f.setPixelSize(13)
        p.setFont(f)
        for i, name in enumerate(self.options):
            cell = self._cell(i)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(255, 255, 255, 52 if name == self.current else 18))
            p.drawRoundedRect(cell.adjusted(1, 1, -1, -1), 8, 8)
            p.setPen(QColor(255, 255, 255, 255 if name == self.current else 160))
            p.drawText(cell, Qt.AlignmentFlag.AlignCenter, name)


class ColorWell(QWidget):
    clicked = pyqtSignal()

    def __init__(self, color):
        super().__init__()
        self.color, self._enabled = QColor(color), True
        self.setFixedSize(30, 22)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def set_color(self, c):
        self.color = QColor(c)
        self.update()

    def set_active(self, on):
        self._enabled = on
        self.setCursor(Qt.CursorShape.PointingHandCursor if on else Qt.CursorShape.ArrowCursor)
        self.update()

    def mousePressEvent(self, _):
        if self._enabled:
            self.clicked.emit()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setOpacity(1.0 if self._enabled else 0.35)
        p.setBrush(self.color)
        p.setPen(QPen(QColor(255, 255, 255, 90), 1.2))
        p.drawRoundedRect(QRectF(1, 1, 28, 20), 6, 6)


def logo_pixmap(size, radius=None):
    """The Constelyric logo: a glowing four-point star ringed like the 'current word',
    with a few linked stars around it, on a deep-space tile."""
    radius = size * 0.23 if radius is None else radius
    pm = QPixmap(size, size)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    clip = QPainterPath()
    clip.addRoundedRect(0, 0, size, size, radius, radius)
    p.setClipPath(clip)
    bg = QLinearGradient(0, 0, size, size)
    bg.setColorAt(0, QColor("#1D1B27"))
    bg.setColorAt(1, QColor("#0A0A0F"))
    p.fillRect(0, 0, size, size, bg)
    c = size / 2
    glow = QRadialGradient(QPointF(c, c), size * 0.5)
    glow.setColorAt(0, QColor(255, 206, 90, 125))
    glow.setColorAt(1, QColor(255, 206, 90, 0))
    p.fillRect(0, 0, size, size, QBrush(glow))
    if size >= 40:                                      # tiny constellation around the star
        sat = [(0.20, 0.72), (0.82, 0.24), (0.78, 0.80)]
        p.setPen(QPen(QColor(255, 214, 110, 95), max(1.0, size * 0.012)))
        for x, y in sat:
            p.drawLine(QPointF(c, c), QPointF(x * size, y * size))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(255, 226, 140, 215))
        for x, y in sat:
            p.drawEllipse(QPointF(x * size, y * size), size * 0.024, size * 0.024)
    if size >= 32:                                      # the ring
        p.setPen(QPen(QColor(255, 226, 140, 175), max(1.0, size * 0.014)))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawEllipse(QPointF(c, c), size * 0.275, size * 0.275)
    p.translate(c, c)                                   # the star
    r = size * 0.31
    sg = QRadialGradient(QPointF(0, 0), r)
    sg.setColorAt(0.0, QColor(255, 250, 232))
    sg.setColorAt(0.55, QColor(255, 224, 124))
    sg.setColorAt(1.0, QColor(242, 172, 44))
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QBrush(sg))
    p.drawPath(star_path(r))
    p.end()
    return pm


def rounded_art(data, size, radius):
    if not data:
        return logo_pixmap(size, radius)
    pm = QPixmap(size, size)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    path = QPainterPath()
    path.addRoundedRect(0, 0, size, size, radius, radius)
    p.setClipPath(path)
    src = QPixmap()
    if src.loadFromData(data):
        sc = src.scaled(size, size, Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                        Qt.TransformationMode.SmoothTransformation)
        p.drawPixmap(-(sc.width() - size) // 2, -(sc.height() - size) // 2, sc)
    p.end()
    return pm


# --------------------------------------------------------------------------
# The panel (what opens from the tray icon)
# --------------------------------------------------------------------------
class Panel(QWidget):
    STYLE_CSS = """
    QFrame#card { background: rgba(30,30,33,246); border: 1px solid rgba(255,255,255,26); border-radius: 16px; }
    QLabel { color: #F2F2F7; background: transparent; font-size: 13px; }
    QLabel#title { font-size: 14px; font-weight: 600; }
    QLabel#brand { font-size: 13px; font-weight: 600; color: #F2F2F7; }
    QLabel#sub { color: #98989F; font-size: 12px; }
    QLabel#section { color: #8E8E93; font-size: 11px; font-weight: 600; }
    QFrame#group { background: rgba(255,255,255,15); border-radius: 10px; }
    QFrame#sep { background: rgba(255,255,255,22); }
    QPushButton#quit { color: #F2F2F7; background: rgba(255,255,255,15); border: none;
                       border-radius: 8px; padding: 8px; font-size: 13px; }
    QPushButton#quit:hover { background: rgba(255,69,58,200); }
    QPushButton#link { color: #FFD666; background: transparent; border: none; font-size: 12px; padding: 2px; }
    QPushButton#link:hover { text-decoration: underline; }
    """

    def __init__(self, overlay, app, updater=None):
        super().__init__()
        self.overlay, self.app, self.updater = overlay, app, updater
        self._dialog, self._hidden_at, self._art_key = False, 0.0, None
        self.setWindowFlags(Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint |
                            Qt.WindowType.WindowStaysOnTopHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setFixedWidth(356)
        self.setStyleSheet(self.STYLE_CSS)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(18, 14, 18, 22)
        card = QFrame(); card.setObjectName("card")
        sh = QGraphicsDropShadowEffect(self)
        sh.setBlurRadius(34); sh.setOffset(0, 8); sh.setColor(QColor(0, 0, 0, 160))
        card.setGraphicsEffect(sh)
        outer.addWidget(card)
        v = QVBoxLayout(card)
        v.setContentsMargins(14, 14, 14, 14)
        v.setSpacing(10)

        # Brand
        head = QHBoxLayout(); head.setSpacing(8)
        mark = QLabel(); mark.setFixedSize(22, 22); mark.setPixmap(logo_pixmap(22, 6))
        brand = QLabel("Constelyric"); brand.setObjectName("brand")
        head.addWidget(mark); head.addWidget(brand); head.addStretch(1)
        v.addLayout(head)

        # Now playing
        top = QHBoxLayout(); top.setSpacing(12)
        self.art = QLabel(); self.art.setFixedSize(56, 56)
        self.art.setPixmap(rounded_art(None, 56, 12))
        info = QVBoxLayout(); info.setSpacing(2)
        self.t_title, self.t_artist, self.t_status = QLabel("Nothing playing"), QLabel(""), QLabel("")
        self.t_title.setObjectName("title"); self.t_artist.setObjectName("sub"); self.t_status.setObjectName("sub")
        self.t_title.setMaximumWidth(240); self.t_artist.setMaximumWidth(240)
        for w in (self.t_title, self.t_artist, self.t_status):
            info.addWidget(w)
        top.addWidget(self.art); top.addLayout(info, 1)
        v.addLayout(top)

        # Display
        v.addWidget(self._section("DISPLAY"))
        self.seg = Segmented(STYLES, S["style"], cols=3)
        self.seg.changed.connect(self._style)
        v.addWidget(self.seg)

        # Toggles
        v.addWidget(self._section("OPTIONS"))
        self.sw_show, self.sw_auto, self.sw_awake = Switch(S["show"]), Switch(S["auto"]), Switch(S["awake"])
        self.sw_clock = Switch(S["clock"])
        self.sw_clock.toggled.connect(lambda b: self._set("clock", b))
        self.sw_art, self.sw_icons = Switch(S["art_bg"]), Switch(S["icons"])
        self.sw_art.toggled.connect(lambda b: self._set("art_bg", b))
        self.sw_icons.toggled.connect(lambda b: self._set("icons", b))
        self.sw_glow = Switch(S["glow"])
        self.sw_glow.toggled.connect(lambda b: self._set("glow", b))
        self.src_btn = QPushButton("Choose…"); self.src_btn.setObjectName("link")
        self.src_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.src_btn.clicked.connect(self._open_sources)
        self.sources = SourcesDialog()
        self.sw_show.toggled.connect(lambda b: self._set("show", b))
        self.sw_auto.toggled.connect(self._auto)
        self.sw_awake.toggled.connect(lambda b: (self._set("awake", b), apply_awake()))
        v.addWidget(self._group([("Show lyrics", self.sw_show),
                                 ("Auto Sync colours", self.sw_auto),
                                 ("Border glow", self.sw_glow)]))

        self.more_btn = QPushButton("More options  ▾"); self.more_btn.setObjectName("link")
        self.more_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.more_btn.clicked.connect(self._toggle_more)
        v.addWidget(self.more_btn, 0, Qt.AlignmentFlag.AlignLeft)

        # less-used options + colours live in a section that starts folded so the panel stays short
        self.more = QWidget()
        mv = QVBoxLayout(self.more)
        mv.setContentsMargins(0, 0, 0, 0)
        mv.setSpacing(10)
        mv.addWidget(self._group([("Keep display awake", self.sw_awake),
                                  ("Album art backdrop", self.sw_art),
                                  ("Word icons", self.sw_icons),
                                  ("Lock-screen clock", self.sw_clock),
                                  ("Lyrics sources", self.src_btn)]))
        mv.addWidget(self._section("COLOURS"))
        self.wells = {k: ColorWell(S[k]) for k in ("lyric", "highlight", "secondary")}
        for k, w in self.wells.items():
            w.clicked.connect(lambda k=k: self._pick(k))
        self.wells["highlight"].set_active(not S["auto"])
        mv.addWidget(self._group([("Lyric", self.wells["lyric"]),
                                  ("Highlight", self.wells["highlight"]),
                                  ("Secondary text", self.wells["secondary"])]))
        self.more.setVisible(False)
        v.addWidget(self.more)

        ver = QHBoxLayout()
        self.up_label = QLabel(f"Version {APP_VERSION}"); self.up_label.setObjectName("sub")
        self.up_btn = QPushButton("Check for updates"); self.up_btn.setObjectName("link")
        self.up_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.up_btn.clicked.connect(self._check)
        ver.addWidget(self.up_label); ver.addStretch(1); ver.addWidget(self.up_btn)
        v.addLayout(ver)

        hint = QLabel("Windows can't draw over the lock screen, so lyrics show on your desktop.\n"
                      "Fisheye, Code and Constellation are fullscreen: press Esc to exit.")
        hint.setObjectName("sub"); hint.setWordWrap(True)
        v.addWidget(hint)

        q = QPushButton("Quit Constelyric"); q.setObjectName("quit")
        q.setCursor(Qt.CursorShape.PointingHandCursor)
        q.clicked.connect(self.app.quit)
        v.addWidget(q)

        self.timer = QTimer(self); self.timer.timeout.connect(self.refresh); self.timer.start(500)

    # -- builders ----------------------------------------------------------
    def _section(self, text):
        l = QLabel(text); l.setObjectName("section")
        l.setContentsMargins(4, 4, 0, 0)
        return l

    def _group(self, rows):
        g = QFrame(); g.setObjectName("group")
        lay = QVBoxLayout(g); lay.setContentsMargins(12, 4, 12, 4); lay.setSpacing(0)
        for i, (label, ctrl) in enumerate(rows):
            r = QHBoxLayout(); r.setContentsMargins(0, 7, 0, 7)
            r.addWidget(QLabel(label)); r.addStretch(1); r.addWidget(ctrl)
            lay.addLayout(r)
            if i < len(rows) - 1:
                s = QFrame(); s.setObjectName("sep"); s.setFixedHeight(1)
                lay.addWidget(s)
        return g

    # -- actions -----------------------------------------------------------
    def _set(self, key, val):
        S[key] = val
        S.save()
        if key == "show":
            self.overlay.apply_mode()

    def _style(self, v):
        self._set("style", v)
        self.overlay.apply_mode()

    def _auto(self, on):
        self._set("auto", on)
        self.wells["highlight"].set_active(not on)

    def _pick(self, key):
        self._dialog = True
        c = QColorDialog.getColor(QColor(S[key]), self, f"{key.title()} colour")
        self._dialog = False
        if c.isValid():
            S[key] = c.name()
            S.save()
            self.wells[key].set_color(c)
        self.activateWindow()

    def _toggle_more(self):
        show = not self.more.isVisible()
        self.more.setVisible(show)
        self.more_btn.setText("Fewer options  ▴" if show else "More options  ▾")
        self.adjustSize()
        geo = QGuiApplication.primaryScreen().availableGeometry()
        self.move(geo.right() - self.width() + 1, geo.bottom() - self.height() + 1)   # stay anchored bottom-right

    def _open_sources(self):
        self.hide()
        self.sources.show_near()

    def _check(self):
        if self.updater:
            self.set_update_status("Checking…")
            self.updater.check(True)

    def set_update_status(self, text):
        self.up_label.setText(text)

    def sync_style(self):
        self.seg.set(S["style"])

    # -- live refresh ------------------------------------------------------
    def refresh(self):
        if not self.isVisible():
            return
        self.t_title.setText(self._elide(STATE.title or "Nothing playing", self.t_title))
        self.t_artist.setText(self._elide(STATE.artist, self.t_artist))
        txt, col = {"idle": ("Play something to begin", "#8E8E93"),
                    "searching": ("Finding lyrics…", "#FF9F0A"),
                    "found": (("Word-synced" if STATE.word_synced else "Line-synced") + f" lyrics · {STATE.source}",
                              "#34C759" if STATE.word_synced else "#FF9F0A"),
                    "none": ("No lyrics available", "#FF453A")}[STATE.status]
        self.t_status.setText(f'<span style="color:{col}">●</span>&nbsp; {txt}')
        key = (STATE.title, STATE.artist, bool(STATE.art))
        if key != self._art_key:
            self._art_key = key
            self.art.setPixmap(rounded_art(STATE.art, 56, 12))

    @staticmethod
    def _elide(text, label):
        f = QFont(label.font())
        if label.objectName() == "title":
            f.setBold(True)
            f.setPixelSize(14)
        return QFontMetrics(f).elidedText(text, Qt.TextElideMode.ElideRight, label.maximumWidth() - 12)

    # -- show / hide -------------------------------------------------------
    def toggle(self):
        if self.isVisible():
            self.hide()
            return
        if time.monotonic() - self._hidden_at < 0.3:
            return                      # tray click that just closed the panel
        self.sync_style()
        self.adjustSize()
        geo = QGuiApplication.primaryScreen().availableGeometry()
        self.move(geo.right() - self.width() + 1, geo.bottom() - self.height() + 1)
        self.refresh()
        self.show()
        self.raise_()
        self.activateWindow()

    def hideEvent(self, e):
        self._hidden_at = time.monotonic()
        super().hideEvent(e)

    def event(self, e):
        if e.type() == QEvent.Type.WindowDeactivate and not self._dialog:
            self.hide()
        return super().event(e)


# --------------------------------------------------------------------------
# Update checker (GitHub Releases) + pop-up
# --------------------------------------------------------------------------
def parse_ver(s):
    nums = [int(x) for x in re.findall(r"\d+", str(s).split("-")[0])[:3]]
    return tuple(nums + [0] * (3 - len(nums)))


def is_newer(tag, current):
    """True if release `tag` is newer than the running version.  Handles labelled releases such as
    1.0.0-bug-fixes, which count as newer than a plain 1.0.0."""
    t, c = parse_ver(tag), parse_ver(current)
    if t != c:
        return t > c
    t_sfx = str(tag).lstrip("vV").partition("-")[2]
    c_sfx = str(current).lstrip("vV").partition("-")[2]
    return bool(t_sfx) and t_sfx != c_sfx


class Updater(QObject):
    result = pyqtSignal(dict)       # state: new | latest | error

    def check(self, manual=False):
        threading.Thread(target=self._run, args=(manual,), daemon=True).start()

    def _run(self, manual):
        out = {"state": "error", "manual": manual}
        try:
            if GITHUB_REPO.startswith("YOUR-"):
                raise RuntimeError("update source not configured")
            r = requests.get(f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest", headers=UA, timeout=8)
            if r.status_code == 404:                      # no release published yet
                out["state"] = "latest"
            else:
                d = r.json()
                tag = str(d.get("tag_name", ""))
                setup = next((a["browser_download_url"] for a in d.get("assets", [])
                              if re.search(r"setup.*\.exe$", a.get("name", ""), re.I)), d.get("html_url"))
                out.update(state="new" if is_newer(tag, APP_VERSION) else "latest",
                           version=tag.lstrip("vV"), notes=d.get("body") or "", url=setup)
        except Exception as e:
            print("[update] check failed:", e)
        self.result.emit(out)


class UpdatePopup(QWidget):
    CSS = """
    QPushButton#ghost { color: #F2F2F7; background: rgba(255,255,255,15); border: none; border-radius: 8px;
                        padding: 8px 12px; font-size: 13px; }
    QPushButton#ghost:hover { background: rgba(255,255,255,32); }
    QPushButton#primary { color: #241A05; background: #FFD666; border: none; border-radius: 8px;
                          padding: 8px 18px; font-size: 13px; font-weight: 600; }
    QPushButton#primary:hover { background: #FFE08A; }
    """

    def __init__(self):
        super().__init__()
        self.info, self.on_skip, self._anims = {}, None, ()
        self.setWindowFlags(Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)    # never steal focus
        self.setFixedWidth(392)
        self.setStyleSheet(Panel.STYLE_CSS + self.CSS)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(18, 14, 18, 22)
        card = QFrame(); card.setObjectName("card")
        sh = QGraphicsDropShadowEffect(self)
        sh.setBlurRadius(34); sh.setOffset(0, 8); sh.setColor(QColor(0, 0, 0, 160))
        card.setGraphicsEffect(sh)
        outer.addWidget(card)
        v = QVBoxLayout(card)
        v.setContentsMargins(16, 14, 16, 14)
        v.setSpacing(8)
        head = QHBoxLayout(); head.setSpacing(8)
        mark = QLabel(); mark.setFixedSize(22, 22)
        mark.setPixmap(logo_pixmap(22, 6))
        title = QLabel("Update available"); title.setObjectName("title")
        head.addWidget(mark); head.addWidget(title); head.addStretch(1)
        v.addLayout(head)
        self.msg = QLabel(); self.msg.setWordWrap(True)
        self.notes = QLabel(); self.notes.setObjectName("sub"); self.notes.setWordWrap(True)
        v.addWidget(self.msg); v.addWidget(self.notes)
        row = QHBoxLayout(); row.setSpacing(8)
        later, skip, dl = QPushButton("Later"), QPushButton("Skip version"), QPushButton("Download")
        later.setObjectName("ghost"); skip.setObjectName("ghost"); dl.setObjectName("primary")
        for b in (later, skip, dl):
            b.setCursor(Qt.CursorShape.PointingHandCursor)
        later.clicked.connect(self.hide)
        skip.clicked.connect(self._skip)
        dl.clicked.connect(self._download)
        row.addWidget(later); row.addWidget(skip); row.addStretch(1); row.addWidget(dl)
        v.addSpacing(4); v.addLayout(row)

    def _skip(self):
        if self.on_skip:
            self.on_skip(self.info.get("version", ""))
        self.hide()

    def _download(self):
        QDesktopServices.openUrl(QUrl(self.info.get("url") or f"https://github.com/{GITHUB_REPO}/releases/latest"))
        self.hide()

    def show_for(self, info, on_skip):
        self.info, self.on_skip = info, on_skip
        self.msg.setText(f"Constelyric {info['version']} is out. You have {APP_VERSION}.")
        lines = [re.sub(r"^[#>*\-\s]+", "", l).strip() for l in info.get("notes", "").splitlines()]
        lines = [l for l in lines if l][:3]
        self.notes.setText("\n".join("•  " + l[:72] for l in lines))
        self.notes.setVisible(bool(lines))
        self.adjustSize()
        geo = QGuiApplication.primaryScreen().availableGeometry()
        end = QPoint(geo.right() - self.width() + 1, geo.bottom() - self.height() + 1)
        self.move(end.x(), end.y() + 28)
        self.setWindowOpacity(0.0)
        self.show()
        a1 = QPropertyAnimation(self, b"windowOpacity", self); a1.setDuration(350); a1.setEndValue(1.0)
        a2 = QPropertyAnimation(self, b"pos", self); a2.setDuration(500); a2.setEndValue(end)
        a2.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._anims = (a1, a2)
        a1.start(); a2.start()


# --------------------------------------------------------------------------
# Lyrics sources window: enable / disable / reorder, Musixmatch key
# --------------------------------------------------------------------------
class SourcesDialog(QWidget):
    CSS = """
    QPushButton#mini { color: #F2F2F7; background: rgba(255,255,255,15); border: none; border-radius: 6px;
                       padding: 3px 7px; font-size: 10px; }
    QPushButton#mini:hover { background: rgba(255,255,255,34); }
    QPushButton#mini:disabled { color: rgba(255,255,255,60); }
    QLineEdit { color: #F2F2F7; background: rgba(255,255,255,15); border: 1px solid rgba(255,255,255,26);
                border-radius: 8px; padding: 7px 9px; font-size: 13px; }
    """

    def __init__(self):
        super().__init__()
        self.setWindowFlags(Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setFixedWidth(450)
        self.setStyleSheet(Panel.STYLE_CSS + UpdatePopup.CSS + self.CSS)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(18, 14, 18, 22)
        card = QFrame(); card.setObjectName("card")
        sh = QGraphicsDropShadowEffect(self)
        sh.setBlurRadius(34); sh.setOffset(0, 8); sh.setColor(QColor(0, 0, 0, 160))
        card.setGraphicsEffect(sh)
        outer.addWidget(card)
        v = QVBoxLayout(card)
        v.setContentsMargins(16, 14, 16, 14)
        v.setSpacing(8)
        head = QHBoxLayout(); head.setSpacing(8)
        mark = QLabel(); mark.setFixedSize(22, 22); mark.setPixmap(logo_pixmap(22, 6))
        title = QLabel("Lyrics sources"); title.setObjectName("title")
        head.addWidget(mark); head.addWidget(title); head.addStretch(1)
        v.addLayout(head)
        intro = QLabel("Tried from top to bottom. The first word-by-word result is used; if none has "
                       "word timing, the first line-by-line one is.")
        intro.setObjectName("sub"); intro.setWordWrap(True)
        v.addWidget(intro)
        group = QFrame(); group.setObjectName("group")
        self.rows = QVBoxLayout(group)
        self.rows.setContentsMargins(12, 2, 12, 2); self.rows.setSpacing(0)
        v.addWidget(group)
        lab = QLabel("Musixmatch API key (optional)"); lab.setObjectName("sub")
        self.key = QLineEdit(S["musixmatch_key"])
        self.key.setEchoMode(QLineEdit.EchoMode.Password)
        self.key.setPlaceholderText("your own key from developer.musixmatch.com")
        self.key.editingFinished.connect(self._save_key)
        v.addWidget(lab); v.addWidget(self.key)
        note = QLabel("Your song title and artist are sent to each enabled service. Most of these are "
                      "free community services, so please keep to a few.")
        note.setObjectName("sub"); note.setWordWrap(True)
        v.addWidget(note)
        row = QHBoxLayout()
        reset, done = QPushButton("Reset order"), QPushButton("Done")
        reset.setObjectName("ghost"); done.setObjectName("primary")
        for b in (reset, done):
            b.setCursor(Qt.CursorShape.PointingHandCursor)
        reset.clicked.connect(self._reset)
        done.clicked.connect(self.hide)
        row.addWidget(reset); row.addStretch(1); row.addWidget(done)
        v.addLayout(row)
        self.rebuild()

    def rebuild(self):
        while self.rows.count():
            w = self.rows.takeAt(0).widget()
            if w:
                w.hide()
                w.setParent(None)
                w.deleteLater()
        items = ordered_sources(S)
        for i, src in enumerate(items):
            sid, name_txt = src["id"], INFO[src["id"]]
            row = QWidget()
            h = QHBoxLayout(row); h.setContentsMargins(0, 7, 0, 7); h.setSpacing(8)
            up, dn = QPushButton("▲"), QPushButton("▼")
            for b in (up, dn):
                b.setObjectName("mini"); b.setFixedWidth(26)
                b.setCursor(Qt.CursorShape.PointingHandCursor)
            up.setEnabled(i > 0); dn.setEnabled(i < len(items) - 1)
            up.clicked.connect(lambda _=False, i=i: self._move(i, -1))
            dn.clicked.connect(lambda _=False, i=i: self._move(i, +1))
            col = QVBoxLayout(); col.setSpacing(0)
            nm = QLabel(name_txt[0]); sub = QLabel(name_txt[1]); sub.setObjectName("sub")
            col.addWidget(nm); col.addWidget(sub)
            sw = Switch(src["on"])
            sw.toggled.connect(lambda on, sid=sid: self._toggle(sid, on))
            h.addWidget(up); h.addWidget(dn); h.addLayout(col, 1); h.addWidget(sw)
            self.rows.addWidget(row)
            if i < len(items) - 1:
                sep = QFrame(); sep.setObjectName("sep"); sep.setFixedHeight(1)
                self.rows.addWidget(sep)

    def _store(self, items):
        S["sources"] = items
        S.save()

    def _toggle(self, sid, on):
        items = ordered_sources(S)
        for it in items:
            if it["id"] == sid:
                it["on"] = on
        self._store(items)

    def _move(self, i, d):
        items = ordered_sources(S)
        items[i], items[i + d] = items[i + d], items[i]
        self._store(items)
        self.rebuild()

    def _reset(self):
        self._store(default_sources())
        self.rebuild()

    def _save_key(self):
        S["musixmatch_key"] = self.key.text().strip()
        S.save()

    def show_near(self):
        self.rebuild()
        self.adjustSize()
        geo = QGuiApplication.primaryScreen().availableGeometry()
        self.move(geo.right() - self.width() + 1, geo.bottom() - self.height() + 1)
        self.show()
        self.raise_()
        self.activateWindow()

    def event(self, e):
        if e.type() == QEvent.Type.WindowDeactivate:
            self._save_key()
            self.hide()
        return super().event(e)


# --------------------------------------------------------------------------
def main():
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    app.setApplicationName("Constelyric")
    icon = QIcon()
    for sz in (16, 20, 24, 32, 48, 64, 128):
        icon.addPixmap(logo_pixmap(sz))
    app.setWindowIcon(icon)
    f = QFont(); f.setFamilies(FONT_FAMILIES); f.setPointSize(10)
    app.setFont(f)
    apply_awake()

    overlay = Overlay()
    updater = Updater()
    popup = UpdatePopup()
    panel = Panel(overlay, app, updater)

    def on_update(info):
        if info["state"] == "new":
            panel.set_update_status(f"Update available: v{info['version']}")
            if info["manual"] or S["skip_version"] != info["version"]:
                popup.show_for(info, lambda ver: (S.__setitem__("skip_version", ver), S.save()))
        elif info["manual"]:
            panel.set_update_status("You’re up to date ✓" if info["state"] == "latest"
                                    else "Couldn’t check for updates")

    updater.result.connect(on_update)
    QTimer.singleShot(10000, lambda: updater.check(False))        # shortly after launch...
    recheck = QTimer(); recheck.timeout.connect(lambda: updater.check(False)); recheck.start(6 * 3600 * 1000)
    overlay.on_escape = panel.sync_style
    start_media_thread()

    tray = QSystemTrayIcon(icon)
    tray.setToolTip("Constelyric")
    menu = QMenu()
    a_open = QAction("Open Constelyric"); a_open.triggered.connect(panel.toggle)
    a_quit = QAction("Quit"); a_quit.triggered.connect(app.quit)
    a_upd = QAction("Check for updates"); a_upd.triggered.connect(lambda: (panel.set_update_status("Checking…"), updater.check(True)))
    menu.addAction(a_open); menu.addAction(a_upd); menu.addAction(a_quit)
    tray.setContextMenu(menu)
    tray.activated.connect(lambda r: panel.toggle() if r == QSystemTrayIcon.ActivationReason.Trigger else None)
    tray.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
