"""Small Windows desktop widget for Codex limits, Kimi Code quota and live thread/agent state.

kimi 分支增强：桌面边缘吸附自动对齐、跟随桌面分辨率变化、Kimi Code 额度显示。
"""
from __future__ import annotations

from datetime import datetime
import json
import math
import os
from pathlib import Path
import queue
import re
import shutil
import subprocess
import threading
import time
import tkinter as tk
import urllib.request

from pixel_world import PixelWorld
from character_sprites import character_bank, draw_fallback, member_key


HOME = Path.home()
PREFERENCES = Path(__file__).resolve().parents[1] / ".runtime/widget-preferences.json"
TEAM_STATS = PREFERENCES.parent / "widget-team-stats.json"
POLL_SECONDS = 20

# Kimi Code 配置文件候选位置（环境变量优先，其次为本机运行时与常见用户目录）
KIMI_CONFIG_CANDIDATES = (
    os.environ.get("KIMI_CODE_CONFIG", ""),
    r"D:\KimiData\daimon-share\daimon\runtime\kimi-code\config.toml",
    str(HOME / ".kimi-code" / "config.toml"),
    str(HOME / ".kimi" / "config.toml"),
)

KIMI_LEVELS = {
    "LEVEL_BASIC": "基础版",
    "LEVEL_PRO": "专业版",
    "LEVEL_MAX": "旗舰版",
    "LEVEL_TEAM": "团队版",
    "LEVEL_ENTERPRISE": "企业版",
}


def codex_executable() -> str | None:
    configured = os.environ.get("CODEX_CLI_PATH")
    if configured and Path(configured).is_file():
        return configured
    candidates = list((HOME / "AppData/Local/OpenAI/Codex/bin").glob("*/codex.exe"))
    return str(max(candidates, key=lambda p: p.stat().st_mtime)) if candidates else shutil.which("codex.exe")


def _to_int(value) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError, OverflowError):
        return None


def kimi_config() -> tuple[str, str]:
    """Locate the kimi-code config.toml and return (api_key, base_url)."""
    for candidate in KIMI_CONFIG_CANDIDATES:
        if not candidate:
            continue
        path = Path(candidate)
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        key_m = re.search(r'api_key\s*=\s*"([^"]+)"', text)
        url_m = re.search(r'base_url\s*=\s*"([^"]+)"', text)
        if key_m and url_m:
            return key_m.group(1), url_m.group(1).rstrip("/")
    raise RuntimeError("未找到 Kimi Code 配置")


def kimi_quota() -> dict:
    """Call GET {base_url}/usages and normalize the totalQuota payload."""
    api_key, base_url = kimi_config()
    req = urllib.request.Request(
        f"{base_url}/usages",
        headers={"Authorization": f"Bearer {api_key}", "Accept": "application/json"},
        method="GET",
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
    quota = payload.get("totalQuota") or {}
    user = payload.get("user") or {}
    limit = _to_int(quota.get("limit"))
    used = _to_int(quota.get("used"))
    remaining = _to_int(quota.get("remaining"))
    if remaining is None and limit is not None and used is not None:
        remaining = max(limit - used, 0)
    return {
        "limit": limit,
        "used": used,
        "remaining": remaining,
        "membership": (user.get("membership") or {}).get("level", ""),
        "resetTime": quota.get("resetTime") or "",
    }


class AppServer:
    """Use the supported local app-server protocol; never read auth files."""

    def __init__(self) -> None:
        self.exe = codex_executable()
        self.proc: subprocess.Popen[str] | None = None
        self.responses: queue.Queue[dict] = queue.Queue()
        self.next_id = 0
        self.request_lock = threading.RLock()
        self.stopped = False

    def start(self) -> None:
        if not self.exe:
            raise RuntimeError("找不到 Codex CLI")
        self.responses = queue.Queue()
        self.proc = subprocess.Popen(
            [self.exe, "app-server", "--stdio"], stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
            encoding="utf-8", errors="replace", bufsize=1,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if self.stopped:
            self.close()
            raise RuntimeError("组件已退出")
        threading.Thread(target=self._read, daemon=True).start()
        self.request("initialize", {
            "clientInfo": {"name": "codex-quota-desktop-widget", "title": "Codex Desktop Widget", "version": "1.0.0"},
            "capabilities": {},
        })
        self.notify("initialized", {})

    def _read(self) -> None:
        assert self.proc and self.proc.stdout
        for line in self.proc.stdout:
            try:
                msg = json.loads(line)
                if "id" in msg and ("result" in msg or "error" in msg):
                    self.responses.put(msg)
            except json.JSONDecodeError:
                continue

    def notify(self, method: str, params: dict) -> None:
        if not self.proc or not self.proc.stdin:
            raise RuntimeError("app-server 未启动")
        self.proc.stdin.write(json.dumps({"method": method, "params": params}, ensure_ascii=False) + "\n")
        self.proc.stdin.flush()

    def request(self, method: str, params: dict, timeout: float = 12) -> dict:
        with self.request_lock:
            if self.stopped:
                raise RuntimeError("组件已退出")
            if not self.proc or self.proc.poll() is not None:
                self.start()
            self.next_id += 1
            req_id = self.next_id
            assert self.proc and self.proc.stdin
            self.proc.stdin.write(json.dumps({"id": req_id, "method": method, "params": params}, ensure_ascii=False) + "\n")
            self.proc.stdin.flush()
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                try:
                    msg = self.responses.get(timeout=max(0.05, deadline - time.monotonic()))
                except queue.Empty:
                    break
                if msg.get("id") == req_id:
                    if "error" in msg:
                        raise RuntimeError(msg["error"].get("message", "app-server 请求失败"))
                    return msg.get("result", {})
            raise TimeoutError(f"Codex 未及时响应 {method}")

    def snapshot(self) -> dict:
        result = {}
        for name, method, params in (
            ("limits", "account/rateLimits/read", {}),
            ("threads", "thread/list", {
                "limit": 80, "sortKey": "recency_at", "sortDirection": "desc",
                "sourceKinds": ["cli", "vscode", "exec", "appServer", "subAgent", "subAgentReview", "subAgentCompact", "subAgentThreadSpawn", "subAgentOther"],
            }),
        ):
            try:
                value = self.request(method, params)
                result[name] = value.get("data", []) if name == "threads" else value
            except (OSError, ValueError, RuntimeError, TimeoutError):
                result[name + "_error"] = True
                self.close()
        return result

    def close(self) -> None:
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait(timeout=2)


def pct(window: dict | None) -> str:
    if not window:
        return "—"
    used = window.get("usedPercent")
    if not isinstance(used, (int, float)) or isinstance(used, bool) or not math.isfinite(used):
        return "—"
    return f"{max(0, min(100, 100 - used)):g}%"


def is_agent(thread: dict) -> bool:
    source = thread.get("source")
    return bool(thread.get("parentThreadId") or
                (isinstance(source, dict) and "subAgent" in source) or
                (isinstance(source, str) and source.startswith("subAgent")))


def window_caption(window: dict | None, fallback: str) -> str:
    minutes = (window or {}).get("windowDurationMins")
    if not isinstance(minutes, (int, float)) or minutes <= 0:
        return fallback
    if minutes % 1440 == 0:
        return f"{minutes / 1440:g} 天"
    if minutes % 60 == 0:
        return f"{minutes / 60:g} 小时"
    return f"{minutes:g} 分钟"


def rel_time(ts: float) -> str:
    """把秒/毫秒时间戳转成口语化的相对时间。"""
    if ts > 1e12:
        ts /= 1000
    delta = max(0, time.time() - ts)
    if delta < 60:
        return "刚刚活跃"
    if delta < 3600:
        return f"{int(delta // 60)} 分钟前活跃"
    if delta < 86400:
        return f"{int(delta // 3600)} 小时前活跃"
    return f"{int(delta // 86400)} 天前活跃"


def fmt_duration(seconds: int) -> str:
    """把秒数转成口语化时长。"""
    minutes = int(seconds) // 60
    if minutes < 1:
        return "不到 1 分钟"
    if minutes < 60:
        return f"{minutes} 分钟"
    hours, rest = divmod(minutes, 60)
    return f"{hours} 小时 {rest} 分钟" if rest else f"{hours} 小时"


def load_team_stats() -> dict:
    try:
        data = json.loads(TEAM_STATS.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save_team_stats(stats: dict) -> None:
    try:
        TEAM_STATS.parent.mkdir(parents=True, exist_ok=True)
        TEAM_STATS.write_text(json.dumps(stats, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass


# ---------------------------------------------------------------------------
# 像素小动画（灵感来自 Star-Office-UI：状态驱动动画，AI 干什么就演什么）
# ---------------------------------------------------------------------------

DOC = (
    "............",
    "..KKKKKKKK..",
    ".KDDDDDDDDK.",
    ".KDLLLLDDDK.",
    ".KDDDDDDDDK.",
    ".KDLLLLLLDK.",
    ".KDDDDDDDDK.",
    ".KDLLLDDDDK.",
    ".KDDDDDDDDK.",
    ".KDDDDDDDDK.",
    "..KKKKKKKK..",
    "............",
)


def doc_frames(lines: int) -> tuple:
    """只有前 lines 行有文字的文档帧。"""
    grid = list(DOC)
    for i, r in enumerate((3, 5, 7)):
        if i >= lines:
            grid[r] = ".KDDDDDDDDK."
    return tuple(grid)


def task_frames(state: str) -> list:
    """任务状态 → 文档图标帧序列。"""
    if state == "active":  # 进行中：文字逐行打出
        return [(doc_frames(1), 0, 0, ""), (doc_frames(2), 0, 0, ""), (DOC, 0, 0, "")]
    return [(DOC, 0, 0, "")]


class PixelSprite(tk.Canvas):
    """Grid task icons and shared animated character portraits."""

    SCALE = 3
    TOP = 12  # 头顶动画区高度

    def __init__(self, parent, bg):
        side = 12 * self.SCALE
        super().__init__(parent, width=side, height=side + self.TOP,
                         bg=bg, highlightthickness=0, bd=0)
        self.frames = [(("............",)*12, 0, 0, "")]
        self.palette: dict = {}
        self.overlay_color = "#9199a5"
        self.index = 0
        self.character = None

    def set_animation(self, frames, palette, overlay_color="#9199a5"):
        was_character = self.character is not None
        self.character = None
        if frames != self.frames or palette != self.palette or was_character:
            self.frames = list(frames)
            self.palette = dict(palette)
            self.index = 0
            self.redraw()
        self.overlay_color = overlay_color

    def set_character(self, key, state):
        self.character = (key, state)
        self.redraw()

    def set_empty(self):
        self.set_animation([(("............",)*12, 0, 0, "")], {})

    def advance(self):
        if self.character is not None:
            self.redraw()
        elif len(self.frames) > 1:
            self.index = (self.index + 1) % len(self.frames)
            self.redraw()

    def redraw(self):
        self.delete("all")
        if self.character is not None:
            key, state = self.character
            bank = character_bank(self)
            frame, dx, dy = bank.sample(key, state, "card")
            if frame is not None:
                self.create_image(18+dx, 47+dy, image=frame, anchor="s")
            else:
                draw_fallback(self, 18+dx, 47+dy, bank.assignments.get(key, 0), "portrait")
            badge = "?" if state in {"unknown", "notLoaded"} else "!" if state == "systemError" else ""
            if badge:
                self.create_text(33, 2, text=badge, anchor="ne",
                                 fill="#efad83" if state == "systemError" else "#9199a5",
                                 font=("Microsoft YaHei UI", 7, "bold"))
            return
        grid, dx, dy, overlay = self.frames[self.index]
        s = self.SCALE
        for r, row in enumerate(grid):
            for c, ch in enumerate(row):
                color = self.palette.get(ch)
                if color:
                    x0 = c * s + dx
                    y0 = r * s + dy + self.TOP
                    self.create_rectangle(x0, y0, x0 + s, y0 + s, fill=color, outline="")
        if overlay.strip():
            self.create_text(12 * s - 2, 2, text=overlay, anchor="ne",
                             fill=self.overlay_color, font=("Microsoft YaHei UI", 7, "bold"))



class RoundedCard(tk.Canvas):
    """圆角卡片容器：内容放进 .body，圆角背景自动跟随尺寸。"""

    def __init__(self, parent, radius=9, bg="#16191e", fill="#20242b"):
        super().__init__(parent, width=1, height=1, bg=bg, highlightthickness=0, bd=0)
        self.radius = radius
        self.fill_color = fill
        self.body = tk.Frame(self, bg=fill)
        self._window = self.create_window(0, 0, anchor="nw", window=self.body)
        self.body.bind("<Configure>", self._fit_height)
        self.bind("<Configure>", self._redraw)

    def _fit_height(self, _event=None):
        self.configure(height=self.body.winfo_reqheight())

    def _redraw(self, _event=None):
        w = self.winfo_width()
        h = self.body.winfo_reqheight()
        r = self.radius
        fill = self.fill_color
        self.delete("bg")
        for (x1, y1, x2, y2, start) in ((0, 0, 2 * r, 2 * r, 90), (w - 2 * r, 0, w, 2 * r, 0),
                                        (0, h - 2 * r, 2 * r, h, 180), (w - 2 * r, h - 2 * r, w, h, 270)):
            self.create_arc(x1, y1, x2, y2, start=start, extent=90, style="pieslice",
                            fill=fill, outline="", tags="bg")
        self.create_rectangle(r, 0, w - r, h, fill=fill, outline="", tags="bg")
        self.create_rectangle(0, r, w, h - r, fill=fill, outline="", tags="bg")
        self.itemconfigure(self._window, width=w)
        self.tag_lower("bg")


class RoundedWindow(tk.Canvas):
    """Window chrome with genuinely transparent corners on Windows."""

    def __init__(self, parent, width, *, radius=14, bg, fill):
        super().__init__(parent, width=width, height=1, bg=bg, highlightthickness=0, bd=0)
        self.radius = radius
        self.fill_color = fill
        self.body = tk.Frame(self, bg=fill)
        self._window = self.create_window(1, radius, anchor="nw", window=self.body)
        self.body.bind("<Configure>", self._fit_height)
        self.bind("<Configure>", self._redraw)

    def _fit_height(self, _event=None):
        height = self.body.winfo_reqheight() + 2 * self.radius
        if int(self.cget("height")) != height:
            self.configure(height=height)

    def _rounded_rect(self, inset, radius, color):
        w = self.winfo_width()
        h = self.winfo_height()
        x1, y1, x2, y2 = inset, inset, w - inset, h - inset
        r = radius
        for ax1, ay1, ax2, ay2, start in (
            (x1, y1, x1 + 2*r, y1 + 2*r, 90),
            (x2 - 2*r, y1, x2, y1 + 2*r, 0),
            (x1, y2 - 2*r, x1 + 2*r, y2, 180),
            (x2 - 2*r, y2 - 2*r, x2, y2, 270),
        ):
            self.create_arc(ax1, ay1, ax2, ay2, start=start, extent=90,
                            style="pieslice", fill=color, outline="", tags="chrome")
        self.create_rectangle(x1 + r, y1, x2 - r, y2,
                              fill=color, outline="", tags="chrome")
        self.create_rectangle(x1, y1 + r, x2, y2 - r,
                              fill=color, outline="", tags="chrome")

    def _redraw(self, _event=None):
        self.delete("chrome")
        self._rounded_rect(0, self.radius, "#3a3e45")
        self._rounded_rect(1, self.radius - 1, self.fill_color)
        self.itemconfigure(self._window, width=max(1, self.winfo_width() - 2))
        self.tag_lower("chrome")


class Widget:
    BG = "#16191e"
    CARD = "#20242b"
    TEXT = "#eeeeef"
    MUTED = "#9199a5"
    ACCENT = "#99ddb6"
    WIDTH = 320
    TRANSPARENT = "#010203"
    SNAP = 14      # 吸附触发距离（像素）
    MARGIN = 16    # 吸附后与屏幕左右/顶部的间距
    TASKBAR = 48   # 底部为任务栏预留的高度

    def __init__(self) -> None:
        try:
            prefs = json.loads(PREFERENCES.read_text(encoding="utf-8"))
            if not isinstance(prefs, dict):
                prefs = {}
        except (OSError, ValueError):
            prefs = {}
        self.root = tk.Tk()
        self.root.withdraw()
        self.root.title("模型额度 · 桌面小组件")
        self.root.overrideredirect(True)
        self.root.resizable(False, False)
        self.root.configure(bg=self.BG)
        self.chrome_bg = self.BG
        try:
            self.root.wm_attributes("-transparentcolor", self.TRANSPARENT)
            self.root.configure(bg=self.TRANSPARENT)
            self.chrome_bg = self.TRANSPARENT
        except tk.TclError:
            pass
        self.pinned = bool(prefs.get("pinned", True))
        self.collapsed = bool(prefs.get("collapsed", False))
        self.root.attributes("-topmost", self.pinned)
        self.root.attributes("-alpha", 0.97)
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        self.client = AppServer()
        self.events: queue.Queue[tuple[str, object]] = queue.Queue()
        self.busy = False
        self.closing = False
        self.refresh_timer = None
        self.last_data = {}
        self.world_expanded = False
        self.world_progress = 0.0
        self.world_animation = None
        self.layout_timer = None
        self.panel_x = 0
        self.panel_y = 0
        self.panel_height = 1
        self.current_agents = []
        self.team_stats = load_team_stats()
        self.last_sample: float | None = None
        self.last_busy = False
        self.snapped: set[str] = set(prefs.get("snapped") or []) & {"left", "right", "top", "bottom"}
        self._build()
        self.root.update_idletasks()
        self.chrome._fit_height()
        self.root.update_idletasks()
        height = self.chrome.winfo_reqheight()
        sw = self.root.winfo_screenwidth()
        sh = self.root.winfo_screenheight()
        self._screen = (sw, sh)
        max_x = max(0, sw - self.WIDTH - self.MARGIN)
        max_y = max(0, sh - height - self.TASKBAR)
        try:
            if not prefs:
                # 首次启动：默认吸附到右上区域
                x, y = max_x, min(80, max_y)
                self.snapped = {"right"}
            elif prefs.get("screen") == [sw, sh]:
                # 分辨率未变：恢复上次绝对位置
                x = max(0, min(max(0, sw - self.WIDTH), int(prefs.get("x", max_x))))
                y = max(0, min(max(0, sh - height), int(prefs.get("y", 80))))
            else:
                # 分辨率变化：吸附边保持吸附，其余轴按比例换算
                if "left" in self.snapped:
                    x = self.MARGIN
                elif "right" in self.snapped:
                    x = max_x
                else:
                    x = round(float(prefs.get("x_ratio", 1.0)) * max(0, sw - self.WIDTH))
                if "top" in self.snapped:
                    y = self.MARGIN
                elif "bottom" in self.snapped:
                    y = max_y
                else:
                    y = round(float(prefs.get("y_ratio", 0.1)) * max(0, sh - height))
                x = max(0, min(x, max(0, sw - self.WIDTH)))
                y = max(0, min(y, max(0, sh - height)))
        except (ValueError, TypeError):
            x, y = max_x, 80
        self.panel_x, self.panel_y, self.panel_height = x, y, height
        self._layout_world()
        self.root.deiconify()
        self.root.after(200, self._drain)
        self.root.after(1500, self._watch_screen)
        self.root.after(280, self._tick)
        self.refresh()

    def label(self, parent, text="", *, fg=None, bg=None, size=9, bold=False, **kw):
        return tk.Label(parent, text=text, bg=bg or self.BG, fg=fg or self.TEXT,
                        font=("Microsoft YaHei UI", size, "bold" if bold else "normal"),
                        anchor="w", borderwidth=0, **kw)

    def button(self, parent, text, command):
        button = tk.Button(parent, text=text, command=command, bg=self.BG,
                           fg=self.MUTED, activebackground=self.CARD,
                           activeforeground=self.TEXT, relief="flat", borderwidth=0,
                           cursor="hand2", padx=5, pady=1, font=("Microsoft YaHei UI", 9))
        button.pack(side="right")
        return button

    def _member_card(self, parent):
        """一张圆角成员卡片：左侧像素动画，右侧名称 + 状态 + 副行。"""
        shell = RoundedCard(parent, bg=self.BG, fill=self.CARD)
        shell.pack(fill="x", pady=(4, 0))
        card = shell.body
        card.configure(padx=8, pady=5)
        sprite = PixelSprite(card, self.CARD)
        sprite.pack(side="left")
        right = tk.Frame(card, bg=self.CARD)
        right.pack(side="left", fill="x", expand=True, padx=(8, 0))
        top = tk.Frame(right, bg=self.CARD)
        top.pack(fill="x")
        name = self.label(top, "—", bg=self.CARD, bold=True, size=9)
        name.pack(side="left")
        status = self.label(top, "", bg=self.CARD, size=8)
        status.pack(side="right")
        sub = self.label(right, "", bg=self.CARD, fg=self.MUTED, size=8, wraplength=224)
        sub.pack(fill="x", pady=(2, 0))
        return shell, sprite, name, status, sub

    def _tick(self) -> None:
        """全局动画心跳：推进所有像素小人与办公室场景的帧。"""
        if self.closing:
            return
        for rows in (self.task_rows, self.agent_rows):
            for row in rows:
                row[1].advance()
        if not self.collapsed:
            self.world_view.advance()
        self.root.after(280, self._tick)

    def _build(self) -> None:
        self.chrome = RoundedWindow(self.root, self.WIDTH, bg=self.chrome_bg, fill=self.BG)
        self.chrome.place(x=0, y=0, width=self.WIDTH)
        outer = self.chrome.body
        header = tk.Frame(outer, bg=self.BG, cursor="fleur")
        header.pack(fill="x", padx=12, pady=(9, 7))
        title = self.label(header, "●  模型额度", fg=self.ACCENT, bold=True)
        title.pack(side="left")
        self.button(header, "×", self.close)
        self.fold_button = self.button(header, "展开" if self.collapsed else "收起", self.toggle_fold)
        self.pin_button = self.button(header, "已置顶" if self.pinned else "置顶", self.toggle_pin)
        for target in (title, header):
            target.bind("<ButtonPress-1>", self.drag_start)
            target.bind("<B1-Motion>", self.drag_move)
            target.bind("<ButtonRelease-1>", self.drag_end)
            target.bind("<Double-Button-1>", lambda _: self.toggle_fold())

        card_shell = RoundedCard(outer, bg=self.BG, fill=self.CARD)
        card_shell.pack(fill="x", padx=12)
        card = card_shell.body
        card.configure(padx=12, pady=9)
        line = tk.Frame(card, bg=self.CARD)
        line.pack(fill="x")
        self.label(line, "Codex", bg=self.CARD, bold=True, size=11).pack(side="left")
        self.label(line, "剩余额度", bg=self.CARD, fg=self.MUTED, size=8).pack(side="right")
        self.quota_rows = []
        for caption in ("短周期", "长周期"):
            row = tk.Frame(card, bg=self.CARD)
            row.pack(fill="x", pady=(7, 0))
            name = self.label(row, caption, bg=self.CARD, fg=self.MUTED, size=8, width=7)
            name.pack(side="left")
            bar = tk.Canvas(row, width=137, height=5, bg="#363b43", highlightthickness=0)
            bar.pack(side="left", padx=7)
            value = self.label(row, "—", bg=self.CARD, bold=True, size=10, width=5)
            value.configure(anchor="e")
            value.pack(side="right")
            self.quota_rows.append((name, bar, value))
        self.reset_label = self.label(card, "正在读取…", bg=self.CARD, fg=self.MUTED, size=8)
        self.reset_label.pack(anchor="w", pady=(5, 0))

        kimi_shell = RoundedCard(outer, bg=self.BG, fill=self.CARD)
        kimi_shell.pack(fill="x", padx=12, pady=(9, 0))
        kimi = kimi_shell.body
        kimi.configure(padx=12, pady=9)
        line = tk.Frame(kimi, bg=self.CARD)
        line.pack(fill="x")
        self.label(line, "Kimi Code", bg=self.CARD, bold=True, size=11).pack(side="left")
        self.kimi_membership = self.label(line, "", bg=self.CARD, fg=self.MUTED, size=8)
        self.kimi_membership.pack(side="right")
        row = tk.Frame(kimi, bg=self.CARD)
        row.pack(fill="x", pady=(7, 0))
        self.label(row, "剩余额度", bg=self.CARD, fg=self.MUTED, size=8, width=7).pack(side="left")
        self.kimi_bar = tk.Canvas(row, width=137, height=5, bg="#363b43", highlightthickness=0)
        self.kimi_bar.pack(side="left", padx=7)
        self.kimi_value = self.label(row, "—", bg=self.CARD, bold=True, size=10, width=7)
        self.kimi_value.configure(anchor="e")
        self.kimi_value.pack(side="right")
        self.kimi_reset = self.label(kimi, "正在读取…", bg=self.CARD, fg=self.MUTED, size=8)
        self.kimi_reset.pack(anchor="w", pady=(5, 0))
        tk.Frame(outer, bg="#30353e", height=1).pack(fill="x", padx=12, pady=(9, 0))

        self.details = tk.Frame(outer, bg=self.BG)
        if not self.collapsed:
            self.details.pack(fill="x", padx=12, pady=(8, 0))
        self.task_summary = self.label(self.details, "最近任务", bold=True, size=9)
        self.task_summary.pack(fill="x")
        self.task_rows = [self._member_card(self.details) for _ in range(3)]
        team_header = tk.Frame(self.details, bg=self.BG)
        team_header.pack(fill="x", pady=(9, 0))
        self.agent_summary = self.label(team_header, "AI 团队 · 等待读取", bold=True, size=9)
        self.agent_summary.pack(side="left")
        self.world_button = self.button(team_header, "展开画卷", self.toggle_world)
        # The slot participates in the narrow column's layout. The real canvas is
        # a sibling of the chrome so it can unroll beyond that column's bounds.
        self.world_slot = tk.Frame(self.details, width=294, height=PixelWorld.HEIGHT, bg=self.BG)
        self.world_slot.pack(pady=(6, 0))
        self.world_slot.pack_propagate(False)
        self.world_view = PixelWorld(self.root, bg=self.chrome_bg,
                                     on_toggle=self.toggle_world, on_select=self._world_select)
        self.root.bind("<Escape>", lambda _event: self.close_world())
        self.memo = self.label(self.details, "小记 · 等待读取", fg=self.MUTED, size=8, wraplength=282)
        self.memo.pack(fill="x", pady=(4, 0))
        self.agent_rows = [self._member_card(self.details) for _ in range(3)]

        self.footer = tk.Frame(outer, bg=self.BG)
        self.footer.pack(fill="x", padx=12, pady=(8, 8))
        self.status = self.label(self.footer, "正在连接…", fg=self.MUTED, size=8)
        self.status.pack(side="left")
        self.refresh_button = self.button(self.footer, "↻", self.refresh)

    def toggle_world(self):
        if self.collapsed or self.closing:
            return
        self.world_view.focus_set()
        self._animate_world(not self.world_expanded)

    def open_world(self):
        if not self.collapsed and not self.closing:
            self._animate_world(True)

    def close_world(self, *, immediate=False):
        if self.world_animation is not None:
            self.root.after_cancel(self.world_animation)
            self.world_animation = None
        if immediate or self.closing:
            self.world_expanded = False
            self.world_progress = 0.0
            self.world_button.configure(text="展开画卷")
            return
        self._animate_world(False)

    def _animate_world(self, expanded):
        if self.world_animation is not None:
            self.root.after_cancel(self.world_animation)
            self.world_animation = None
        self.world_expanded = expanded
        self.world_button.configure(text="收起画卷" if expanded else "展开画卷")
        origin, target = self.world_progress, float(expanded)
        started = time.monotonic()
        duration = max(0.10, 0.42 * abs(target - origin))

        def step():
            self.world_animation = None
            if self.closing or self.collapsed:
                return
            t = min(1.0, (time.monotonic() - started) / duration)
            ease = 1 - (1 - t) ** 3
            self.world_progress = origin + (target - origin) * ease
            self._layout_world()
            if t < 1:
                self.world_animation = self.root.after(16, step)

        step()

    def _world_select(self, actor):
        # Member identity is shown inside the canvas; the quota/task column is
        # deliberately unaffected by scene interaction.
        self.world_view.focus_set()

    def _layout_world(self):
        """Keep the panel fixed on screen; only the scene's visible bounds grow."""
        if self.closing:
            return
        sw = self.root.winfo_screenwidth()
        height = self.panel_height
        panel_x, panel_y = self.panel_x, self.panel_y
        slot_x = slot_y = 0
        node = self.world_slot
        while node is not self.chrome:
            slot_x += node.winfo_x()
            slot_y += node.winfo_y()
            node = node.master
        compact = 294
        maximum = max(compact, min(PixelWorld.WIDTH, sw - 2 * self.MARGIN))
        maximum -= maximum % 2
        width = compact + round((maximum - compact) * self.world_progress / 2) * 2
        scene_x = panel_x + slot_x - (width - compact) // 2
        margin = round(self.MARGIN * self.world_progress)
        scene_x = max(margin, min(scene_x, sw - width - margin))
        if self.collapsed:
            root_x, root_right = panel_x, panel_x + self.WIDTH
            self.world_view.place_forget()
        else:
            root_x = min(panel_x, scene_x)
            root_right = max(panel_x + self.WIDTH, scene_x + width)
        self.chrome.place_configure(x=panel_x - root_x, y=0, width=self.WIDTH, height=height)
        self.root.geometry(f"{root_right - root_x}x{height}+{root_x}+{panel_y}")
        if not self.collapsed:
            self.world_view.place(x=scene_x - root_x, y=slot_y, width=width, height=PixelWorld.HEIGHT)
            self.world_view.set_viewport(
                width, expanded=self.world_expanded,
                left_bg=self.BG if panel_x <= scene_x <= panel_x + self.WIDTH - 12 else self.chrome_bg,
                right_bg=self.BG if panel_x + 12 <= scene_x + width <= panel_x + self.WIDTH else self.chrome_bg,
            )
            # Canvas.lift is a canvas-item method; explicitly lift the widget.
            self.root.tk.call("raise", self.world_view._w)

    def resize(self):
        if self.layout_timer is not None:
            self.root.after_cancel(self.layout_timer)
            self.layout_timer = None
        self.root.update_idletasks()
        self.chrome._fit_height()
        self.root.update_idletasks()
        self.panel_height = self.chrome.winfo_reqheight()
        self._resize_settle()
        self.layout_timer = self.root.after_idle(self._resize_settle)

    def _resize_settle(self):
        """Settle card height changes before positioning the scene slot."""
        if self.closing:
            return
        self.layout_timer = None
        self.root.update_idletasks()
        self.panel_height = self.chrome.winfo_reqheight()
        if "bottom" in self.snapped:
            self.panel_y = max(0, self.root.winfo_screenheight() - self.panel_height - self.TASKBAR)
        else:
            self.panel_y = max(0, min(self.panel_y, self.root.winfo_screenheight() - self.panel_height))
        self._layout_world()

    def snap_position(self, x: int, y: int) -> tuple[int, int, set[str]]:
        """把候选位置吸附到桌面边缘或水平中线，返回 (x, y, 吸附边集合)。"""
        sw = self.root.winfo_screenwidth()
        sh = self.root.winfo_screenheight()
        w = self.WIDTH
        h = self.panel_height
        left = self.MARGIN
        right = max(self.MARGIN, sw - w - self.MARGIN)
        top = self.MARGIN
        bottom = max(self.MARGIN, sh - h - self.TASKBAR)
        snapped: set[str] = set()
        if x <= left + self.SNAP:
            x = left
            snapped.add("left")
        elif x >= right - self.SNAP:
            x = right
            snapped.add("right")
        elif abs(x - (sw - w) // 2) <= self.SNAP:
            x = (sw - w) // 2  # 水平居中对齐
        if y <= top + self.SNAP:
            y = top
            snapped.add("top")
        elif y >= bottom - self.SNAP:
            y = bottom
            snapped.add("bottom")
        return max(0, min(x, max(0, sw - w))), max(0, min(y, max(0, sh - h))), snapped

    def drag_start(self, event):
        self.drag_origin = (event.x_root, event.y_root, self.panel_x, self.panel_y)

    def drag_move(self, event):
        sx, sy, x, y = self.drag_origin
        x += event.x_root - sx
        y += event.y_root - sy
        x, y, self.snapped = self.snap_position(x, y)
        self.panel_x, self.panel_y = x, y
        self._layout_world()

    def drag_end(self, _event):
        self.save()

    def _watch_screen(self) -> None:
        """分辨率变化时重新定位：吸附边保持吸附，其余轴按比例换算。"""
        if self.closing:
            return
        sw = self.root.winfo_screenwidth()
        sh = self.root.winfo_screenheight()
        if (sw, sh) != self._screen:
            old_sw, old_sh = self._screen
            self._screen = (sw, sh)
            w = self.WIDTH
            h = self.panel_height
            x, y = self.panel_x, self.panel_y
            if "left" in self.snapped:
                nx = self.MARGIN
            elif "right" in self.snapped:
                nx = max(self.MARGIN, sw - w - self.MARGIN)
            else:
                nx = round(x / max(1, old_sw - w) * max(0, sw - w))
            if "top" in self.snapped:
                ny = self.MARGIN
            elif "bottom" in self.snapped:
                ny = max(self.MARGIN, sh - h - self.TASKBAR)
            else:
                ny = round(y / max(1, old_sh - h) * max(0, sh - h))
            nx = max(0, min(nx, max(0, sw - w)))
            ny = max(0, min(ny, max(0, sh - h)))
            self.panel_x, self.panel_y = nx, ny
            self._layout_world()
            self.save()
        self.root.after(1500, self._watch_screen)

    def toggle_fold(self):
        self.collapsed = not self.collapsed
        self.fold_button.configure(text="展开" if self.collapsed else "收起")
        if self.collapsed:
            self.close_world(immediate=True)
            self.details.pack_forget()
        else:
            self.details.pack(fill="x", padx=12, pady=(8, 0), before=self.footer)
        self.resize()
        self.save()

    def toggle_pin(self):
        self.pinned = not self.pinned
        self.root.attributes("-topmost", self.pinned)
        self.pin_button.configure(text="已置顶" if self.pinned else "置顶")
        self.save()

    def save(self):
        try:
            sw = self.root.winfo_screenwidth()
            sh = self.root.winfo_screenheight()
            w = self.WIDTH
            h = self.panel_height
            x, y = self.panel_x, self.panel_y
            PREFERENCES.parent.mkdir(parents=True, exist_ok=True)
            PREFERENCES.write_text(json.dumps({
                "x": x, "y": y,
                "pinned": self.pinned, "collapsed": self.collapsed,
                "snapped": sorted(self.snapped),
                "screen": [sw, sh],
                "x_ratio": x / max(1, sw - w),
                "y_ratio": y / max(1, sh - h),
            }), encoding="utf-8")
        except OSError:
            pass

    def refresh(self) -> None:
        if self.busy or self.closing:
            return
        if self.refresh_timer:
            self.root.after_cancel(self.refresh_timer)
            self.refresh_timer = None
        self.busy = True
        self.refresh_button.configure(state="disabled")
        self.status.configure(text="正在更新…")
        threading.Thread(target=self._fetch, daemon=True).start()

    def _fetch(self) -> None:
        try:
            data = self.client.snapshot()
        except Exception as exc:
            self.events.put(("error", str(exc)))
            return
        try:
            data["kimi"] = kimi_quota()
        except Exception:
            data["kimi_error"] = True
        self.events.put(("ok", data))

    def _drain(self) -> None:
        if self.closing:
            return
        try:
            while True:
                kind, value = self.events.get_nowait()
                self.busy = False
                self.refresh_button.configure(state="normal")
                if kind == "ok":
                    self._render(value)  # type: ignore[arg-type]
                else:
                    self.status.configure(text="连接失败 · 20 秒后重试")
                self.refresh_timer = self.root.after(POLL_SECONDS * 1000, self.refresh)
        except queue.Empty:
            pass
        self.root.after(200, self._drain)

    def _render_kimi(self, data: dict) -> None:
        kimi = data.get("kimi") or {}
        if not kimi:
            self.kimi_membership.configure(text="")
            self.kimi_value.configure(text="—", fg=self.TEXT)
            self.kimi_bar.delete("all")
            self.kimi_reset.configure(text="读取失败 · 检查 Kimi Code 配置" if data.get("kimi_error") else "暂无数据")
            return
        membership = kimi.get("membership") or ""
        level = KIMI_LEVELS.get(membership, membership.replace("LEVEL_", "").title() if membership else "")
        self.kimi_membership.configure(text=level)
        remaining, limit, used = kimi.get("remaining"), kimi.get("limit"), kimi.get("used")
        self.kimi_bar.delete("all")
        if remaining is not None:
            color = self.ACCENT
            if limit:
                ratio = max(0.0, min(1.0, remaining / limit))
                color = self.ACCENT if ratio > 0.2 else "#efad83"
                self.kimi_bar.create_rectangle(0, 0, 137 * ratio, 5, fill=color, outline="")
                text = f"{remaining}/{limit}"
            else:
                text = str(remaining)
            self.kimi_value.configure(text=text, fg=color)
        else:
            self.kimi_value.configure(text="—", fg=self.TEXT)
        parts = []
        if used is not None:
            parts.append(f"已用 {used}")
        reset = kimi.get("resetTime") or ""
        if reset:
            try:
                dt = datetime.fromisoformat(reset.replace("Z", "+00:00")).astimezone()
                parts.append(f"重置 {dt.strftime('%m/%d %H:%M')}")
            except ValueError:
                pass
        self.kimi_reset.configure(text=" · ".join(parts) or "暂无重置信息")

    def _render(self, data: dict) -> None:
        self.last_data = data
        limits = data.get("limits", {})
        buckets = limits.get("rateLimitsByLimitId") or {}
        snapshot = buckets.get("codex") or limits.get("rateLimits") or {}
        primary, secondary = snapshot.get("primary"), snapshot.get("secondary")
        resets = []
        for row, window, fallback in zip(self.quota_rows, (primary, secondary), ("短周期", "长周期")):
            name, bar, value = row
            name.configure(text=window_caption(window, fallback))
            text = pct(window)
            value.configure(text=text)
            bar.delete("all")
            if text != "—":
                remaining = float(text[:-1])
                color = self.ACCENT if remaining > 20 else "#efad83"
                bar.create_rectangle(0, 0, 137 * remaining / 100, 5, fill=color, outline="")
                value.configure(fg=color)
            if window and isinstance(window.get("resetsAt"), (int, float)):
                resets.append(time.strftime("%m/%d %H:%M", time.localtime(window["resetsAt"])))
        self.reset_label.configure(text=("读取失败 · 检查 Codex 登录" if data.get("limits_error") else
                                        "重置 " + " / ".join(resets) if resets else "暂无重置信息"))

        self._render_kimi(data)

        threads = data.get("threads", [])
        agents = [t for t in threads if is_agent(t)]
        tasks = [t for t in threads if not is_agent(t)]
        self.task_summary.configure(text=f"最近任务 · {len(tasks)}")
        self._render_tasks(tasks, data)
        self._render_agents(agents, threads, data)
        if not data.get("threads_error"):
            self._sample_team_stats(tasks, agents)
        else:
            self.last_sample = None
            self.last_busy = False
        self.memo.configure(text=self._memo_text())

    def _sample_team_stats(self, tasks: list, agents: list) -> None:
        """按刷新节奏累积今日团队统计：任务数、成员数、忙碌时长。"""
        today = time.strftime("%Y-%m-%d")
        rec = self.team_stats.setdefault(today, {"busy": 0, "peak": 0, "tasks": [], "agents": []})
        now = time.time()
        day_start = datetime.now().astimezone().replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
        recent_tasks = [t for t in tasks if (t.get("recencyAt") or t.get("updatedAt") or 0) >= day_start]
        recent_agents = [t for t in agents if (t.get("recencyAt") or t.get("updatedAt") or 0) >= day_start]
        busy = any((t.get("status") or {}).get("type") == "active" for t in tasks + agents)
        if self.last_sample and self.last_busy and busy:
            rec["busy"] = rec.get("busy", 0) + int(min(now - self.last_sample, POLL_SECONDS * 2))
        self.last_sample = now
        self.last_busy = busy
        rec["peak"] = max(rec.get("peak", 0), len(recent_agents))
        rec["tasks"] = sorted(set(rec.get("tasks", [])) | {str(t["id"]) for t in recent_tasks if t.get("id")})
        rec["agents"] = sorted(set(rec.get("agents", [])) | {str(t["id"]) for t in recent_agents if t.get("id")})
        for day in sorted(self.team_stats)[:-7]:  # 只保留最近 7 天
            del self.team_stats[day]
        save_team_stats(self.team_stats)

    def _memo_text(self) -> str:
        """优先昨日小记；昨天没数据就展示今日进展。"""
        today = time.strftime("%Y-%m-%d")
        for day in sorted(self.team_stats, reverse=True):
            if day >= today:
                continue
            rec = self.team_stats[day]
            if rec.get("tasks") or rec.get("busy"):
                return (f"昨日小记 · 处理 {len(rec.get('tasks', []))} 项任务 · "
                        f"{max(rec.get('peak', 0), len(rec.get('agents', [])))} 名成员上岗 · "
                        f"忙碌 {fmt_duration(rec.get('busy', 0))}")
        rec = self.team_stats.get(today)
        if rec and (rec.get("tasks") or rec.get("agents")):
            return (f"今日小记 · 已处理 {len(rec.get('tasks', []))} 项任务 · "
                    f"{max(rec.get('peak', 0), len(rec.get('agents', [])))} 名成员上岗 · "
                    f"忙碌 {fmt_duration(rec.get('busy', 0))}")
        return "小记 · 团队还没有留下今天的足迹"

    def _render_tasks(self, tasks: list, data: dict) -> None:
        """任务卡片：像素文档图标，进行中时文字逐行打出。"""
        status_map = {
            "active": ("进行中", self.ACCENT),
            "idle": ("空闲", self.MUTED),
            "notLoaded": ("未加载", self.MUTED),
            "systemError": ("异常", "#efad83"),
        }
        tasks = sorted(tasks, key=lambda t: ((t.get("status") or {}).get("type") == "active",
                                             t.get("recencyAt") or t.get("updatedAt") or 0), reverse=True)
        for i, (card, sprite, name, status, sub) in enumerate(self.task_rows):
            if i >= len(tasks):
                if i == 0:
                    card.pack(fill="x", pady=(4, 0))
                    sprite.set_animation(task_frames(""), {"K": "#0d0f12", "D": "#6b7280", "L": "#4a5160"})
                    name.configure(text="状态读取失败" if data.get("threads_error") else "暂无任务记录", fg=self.MUTED)
                    status.configure(text="")
                    sub.configure(text="")
                else:
                    card.pack_forget()
                continue
            card.pack(fill="x", pady=(4, 0))
            thread = tasks[i]
            state = (thread.get("status") or {}).get("type", "unknown")
            state_text, color = status_map.get(state, ("未知", self.MUTED))
            nm = thread.get("agentNickname") or thread.get("name") or thread.get("agentRole") or "未命名任务"
            nm = " ".join(str(nm).split())
            if len(nm) > 14:
                nm = nm[:13] + "…"
            name.configure(text=nm, fg=self.TEXT)
            status.configure(text=state_text, fg=color)
            sprite.set_animation(task_frames(state),
                                 {"K": "#0d0f12", "D": "#c8cdd5",
                                  "L": self.ACCENT if state == "active" else "#5a6270"})
            ts = thread.get("recencyAt") or thread.get("updatedAt")
            sub.configure(text=rel_time(ts) if isinstance(ts, (int, float)) and ts > 0 else " ")

    def _render_agents(self, agents: list, threads: list, data: dict) -> None:
        """马维斯风格的 AI 团队面板：成员卡片 + 口语化状态 + 角色/归属/活跃时间。"""
        character_bank(self.root).register(agents)
        self.agent_summary.configure(
            text=f"AI 团队 · {len(agents)} 位成员" if agents else
            ("AI 团队 · 状态读取失败" if data.get("threads_error") else "AI 团队 · 暂无成员"))
        by_id = {t.get("id"): t for t in threads if t.get("id")}
        status_map = {
            "active": ("干活中", self.ACCENT),
            "idle": ("待命", self.MUTED),
            "notLoaded": ("状态未知", self.MUTED),
            "systemError": ("异常", "#efad83"),
        }
        agents = sorted(agents, key=lambda t: ((t.get("status") or {}).get("type") == "active",
                                               t.get("recencyAt") or t.get("updatedAt") or 0), reverse=True)
        for i, (card, sprite, name, status, sub) in enumerate(self.agent_rows):
            if i >= len(agents):
                if i == 0:
                    card.pack(fill="x", pady=(4, 0))
                    sprite.set_empty()
                    name.configure(text="状态读取失败" if data.get("threads_error") else "暂无团队成员", fg=self.MUTED)
                    status.configure(text="")
                    sub.configure(text="")
                else:
                    card.pack_forget()
                continue
            card.pack(fill="x", pady=(4, 0))
            thread = agents[i]
            state = (thread.get("status") or {}).get("type", "unknown")
            state_text, color = status_map.get(state, ("未知", self.MUTED))
            nm = thread.get("agentNickname") or thread.get("name") or thread.get("agentRole") or "未命名成员"
            nm = " ".join(str(nm).split())
            if len(nm) > 14:
                nm = nm[:13] + "…"
            name.configure(text=nm, fg=self.TEXT)
            status.configure(text=state_text, fg=color)
            sprite.set_character(member_key(thread), state)
            parts = []
            role = thread.get("agentRole")
            if role:
                parts.append(str(role))
            parent = by_id.get(thread.get("parentThreadId"))
            if parent:
                pn = parent.get("agentNickname") or parent.get("name") or "主任务"
                pn = " ".join(str(pn).split())
                if len(pn) > 12:
                    pn = pn[:11] + "…"
                parts.append(f"归属 {pn}")
            ts = thread.get("recencyAt") or thread.get("updatedAt")
            if isinstance(ts, (int, float)) and ts > 0:
                parts.append(rel_time(ts))
            sub.configure(text=" · ".join(parts) or " ")
        self.current_agents = agents
        self.world_view.set_agents(agents)
        has_error = data.get("limits_error") or data.get("threads_error") or data.get("kimi_error")
        self.status.configure(text="部分数据不可用 · 自动重试" if has_error else f"{time.strftime('%H:%M:%S')} 更新 · 每 20 秒")
        self.resize()

    def close(self) -> None:
        if self.closing:
            return
        self.closing = True
        self.save()
        self.close_world(immediate=True)
        if self.layout_timer is not None:
            self.root.after_cancel(self.layout_timer)
        self.client.stopped = True
        self.client.close()
        self.root.destroy()

    def run(self) -> None:
        self.root.mainloop()


if __name__ == "__main__":
    Widget().run()
