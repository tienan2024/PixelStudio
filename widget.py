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

AGENT_OPEN = (
    "............",
    "...KKKKKK...",
    "..KCCCCCCK..",
    ".KCWWKKWWCK.",
    ".KCCCCCCCCK.",
    ".KCCCCCCCCK.",
    "..KCCCCCCK..",
    "...KKKKKK...",
    "...K.K.K....",
    "...K.K.K....",
    "............",
    "............",
)

AGENT_ARMS_UP = AGENT_OPEN[:4] + ("KKCCCCCCCCKK",) + AGENT_OPEN[5:]

AGENT_CLOSED = AGENT_OPEN[:3] + (".KCKKKKKKCK.",) + AGENT_OPEN[4:]

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


def agent_frames(state: str) -> list:
    """子代理状态 → 帧序列 (grid, dx, dy, 头顶文字)。"""
    if state == "active":  # 干活中：举手打字 + 身体起伏
        return [(AGENT_ARMS_UP, 0, 0, ""), (AGENT_OPEN, 0, 1, "")]
    if state == "notLoaded":  # 休息中：闭眼 + Zzz 飘出
        return [(AGENT_CLOSED, 0, 0, ""), (AGENT_CLOSED, 0, 0, "z"),
                (AGENT_CLOSED, 0, 0, "z Z"), (AGENT_CLOSED, 0, 0, "z Z z")]
    if state == "systemError":  # 异常：左右摇晃 + 感叹号闪烁
        return [(AGENT_OPEN, -1, 0, "!"), (AGENT_OPEN, 1, 0, ""),
                (AGENT_OPEN, -1, 0, "!"), (AGENT_OPEN, 1, 0, "")]
    # 待命：约 1.7 秒眨一次眼
    return [(AGENT_OPEN, 0, 0, "")] * 6 + [(AGENT_CLOSED, 0, 0, "")]


def task_frames(state: str) -> list:
    """任务状态 → 文档图标帧序列。"""
    if state == "active":  # 进行中：文字逐行打出
        return [(doc_frames(1), 0, 0, ""), (doc_frames(2), 0, 0, ""), (DOC, 0, 0, "")]
    return [(DOC, 0, 0, "")]


class PixelSprite(tk.Canvas):
    """12x12 像素画布，按帧列表循环播放。"""

    SCALE = 3
    TOP = 12  # 头顶动画区高度

    def __init__(self, parent, bg):
        side = 12 * self.SCALE
        super().__init__(parent, width=side, height=side + self.TOP,
                         bg=bg, highlightthickness=0, bd=0)
        self.frames = [(AGENT_CLOSED, 0, 0, "")]
        self.palette: dict = {}
        self.overlay_color = "#9199a5"
        self.index = 0

    def set_animation(self, frames, palette, overlay_color="#9199a5"):
        if frames != self.frames or palette != self.palette:
            self.frames = list(frames)
            self.palette = dict(palette)
            self.index = 0
            self.redraw()
        self.overlay_color = overlay_color

    def advance(self):
        if len(self.frames) > 1:
            self.index = (self.index + 1) % len(self.frames)
            self.redraw()

    def redraw(self):
        self.delete("all")
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


def draw_grid(canvas, grid, palette, x, y, scale, tag):
    """在画布上按字符网格画像素块。"""
    for r, row in enumerate(grid):
        for c, ch in enumerate(row):
            color = palette.get(ch)
            if color:
                x0 = x + c * scale
                y0 = y + r * scale
                canvas.create_rectangle(x0, y0, x0 + scale, y0 + scale, fill=color, outline="", tags=tag)


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


class Office(tk.Canvas):
    """迷你像素办公室：子代理按状态走到工位、沙发或 Bug 区。"""

    WALL = "#1b1f26"
    FLOOR = "#262c36"
    FLOOR_Y = 96
    WORK_SPOTS = ((28, 72), (64, 72), (100, 72))
    REST_SPOTS = ((212, 68), (250, 68))
    BUG_SPOTS = ((162, 70), (178, 82))
    SPAWN = (6, 84)
    MONITORS = ((44, 50, 62, 62), (90, 50, 108, 62))

    def __init__(self, parent, bg):
        super().__init__(parent, width=296, height=112, bg=bg, highlightthickness=0, bd=0)
        self.actors: dict = {}
        self._draw_room()

    def _draw_room(self):
        W, H, r = 296, 112, 10
        wall = self.WALL
        for (x1, y1, x2, y2, start) in ((0, 0, 2 * r, 2 * r, 90), (W - 2 * r, 0, W, 2 * r, 0),
                                        (0, H - 2 * r, 2 * r, H, 180), (W - 2 * r, H - 2 * r, W, H, 270)):
            self.create_arc(x1, y1, x2, y2, start=start, extent=90, style="pieslice",
                            fill=wall, outline="", tags="bg")
        self.create_rectangle(r, 0, W - r, H, fill=wall, outline="", tags="bg")
        self.create_rectangle(0, r, W, H - r, fill=wall, outline="", tags="bg")
        # 地板与踢脚线
        self.create_rectangle(6, self.FLOOR_Y, W - 6, H - 6, fill=self.FLOOR, outline="", tags="bg")
        self.create_rectangle(6, self.FLOOR_Y, W - 6, self.FLOOR_Y + 2, fill="#2e3540", outline="", tags="bg")
        # 窗户
        self.create_rectangle(120, 14, 168, 44, fill="#0d0f12", outline="#3a4150", tags="bg")
        self.create_rectangle(124, 18, 164, 40, fill="#23303f", outline="", tags="bg")
        self.create_line(144, 18, 144, 40, fill="#0d0f12", tags="bg")
        # 海报
        self.create_rectangle(186, 16, 220, 40, fill="#2b313d", outline="#3a4150", tags="bg")
        self.create_text(203, 28, text="AI", fill="#5a6270", font=("Microsoft YaHei UI", 8, "bold"), tags="bg")
        # 办公桌
        self.create_rectangle(20, 62, 132, 68, fill="#3a4150", outline="", tags="bg")
        self.create_rectangle(24, 68, 28, 96, fill="#2b313d", outline="", tags="bg")
        self.create_rectangle(124, 68, 128, 96, fill="#2b313d", outline="", tags="bg")
        # 沙发
        self.create_rectangle(200, 60, 284, 76, fill="#323a48", outline="", tags="bg")
        self.create_rectangle(196, 74, 288, 92, fill="#3a4150", outline="", tags="bg")
        self.create_rectangle(196, 92, 200, 96, fill="#2b313d", outline="", tags="bg")
        self.create_rectangle(284, 92, 288, 96, fill="#2b313d", outline="", tags="bg")
        # 绿植
        self.create_rectangle(146, 84, 158, 96, fill="#4a3a2f", outline="", tags="bg")
        self.create_rectangle(149, 72, 155, 84, fill="#3f6b4f", outline="", tags="bg")
        # Bug 区地毯
        self.create_rectangle(160, 100, 196, 106, fill="#4a3436", outline="", tags="bg")

    def update_agents(self, agents):
        """agents: [(key, state, color)]，为每个成员分配目标位置。"""
        spots = {"work": list(self.WORK_SPOTS), "rest": list(self.REST_SPOTS), "bug": list(self.BUG_SPOTS)}
        overflow = 0
        new = {}
        for key, state, color in sorted(agents, key=lambda a: a[0]):
            zone = "work" if state == "active" else "bug" if state == "systemError" else "rest"
            if spots[zone]:
                tx, ty = spots[zone].pop(0)
            else:  # 工位满了就在绿植旁排队
                tx, ty = 136 + 20 * overflow, 88
                overflow += 1
            actor = self.actors.get(key) or {"x": self.SPAWN[0], "y": self.SPAWN[1], "frame_i": 0}
            actor.update(tx=tx, ty=ty, state=state, color=color)
            new[key] = actor
        self.actors = new
        self.redraw_actors()

    def advance(self):
        for a in self.actors.values():
            moving = False
            for axis in ("x", "y"):
                d = a["t" + axis] - a[axis]
                if d:
                    a[axis] = a["t" + axis] if abs(d) <= 3 else a[axis] + (3 if d > 0 else -3)
                    moving = True
            a["moving"] = moving
            a["frame_i"] += 1
        self.redraw_actors()

    def redraw_actors(self):
        self.delete("actor")
        self.delete("screen")
        active_count = sum(1 for a in self.actors.values() if a["state"] == "active")
        for i, (x1, y1, x2, y2) in enumerate(self.MONITORS):
            on = i < active_count
            self.create_rectangle(x1, y1, x2, y2, fill="#0d0f12", outline="#3a4150", tags="screen")
            self.create_rectangle(x1 + 2, y1 + 2, x2 - 2, y2 - 2,
                                  fill="#2f4a3a" if on else "#141920", outline="", tags="screen")
            self.create_rectangle((x1 + x2) // 2 - 1, y2, (x1 + x2) // 2 + 1, y2 + 4,
                                  fill="#3a4150", outline="", tags="screen")
        for key, a in sorted(self.actors.items()):
            i = a["frame_i"]
            state = a["state"]
            if a.get("moving"):  # 走路：摆臂 + 上下颠
                grid, dy, overlay = (AGENT_ARMS_UP if i % 2 else AGENT_OPEN), -(i % 2), ""
            elif state == "active":  # 在工位打字
                grid, dy, overlay = (AGENT_ARMS_UP if i % 2 else AGENT_OPEN), i % 2, ""
            elif state == "systemError":  # 面壁 + 感叹号
                grid, dy, overlay = AGENT_OPEN, 0, ("!" if i % 2 else "")
            elif state == "notLoaded":  # 在沙发上睡觉
                grid, dy = AGENT_CLOSED, 0
                overlay = ("", "z", "z Z", "z Z z")[i % 4]
            else:  # 在沙发上休息眨眼
                grid, dy, overlay = (AGENT_CLOSED if i % 8 == 7 else AGENT_OPEN), 0, ""
            dx = (i % 2) * 2 - 1 if state == "systemError" and not a.get("moving") else 0
            draw_grid(self, grid, {"K": "#0d0f12", "C": a["color"], "W": "#16191e"},
                      a["x"] + dx, a["y"] + dy, 2, "actor")
            if overlay:
                self.create_text(a["x"] + 22, a["y"] - 8, text=overlay, anchor="e",
                                 fill="#efad83" if state == "systemError" else "#9199a5",
                                 font=("Microsoft YaHei UI", 7, "bold"), tags="actor")


class Widget:
    BG = "#16191e"
    CARD = "#20242b"
    TEXT = "#eeeeef"
    MUTED = "#9199a5"
    ACCENT = "#99ddb6"
    WIDTH = 320
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
        self.team_stats = load_team_stats()
        self.last_sample: float | None = None
        self.snapped: set[str] = set(prefs.get("snapped") or []) & {"left", "right", "top", "bottom"}
        self._build()
        self.root.update_idletasks()
        height = self.root.winfo_reqheight()
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
        self.root.geometry(f"{self.WIDTH}x{height}+{x}+{y}")
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
        self.office.advance()
        self.root.after(280, self._tick)

    def _build(self) -> None:
        outer = tk.Frame(self.root, bg=self.BG, highlightbackground="#3a3e45", highlightthickness=1)
        outer.pack(fill="both", expand=True)
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
        self.agent_summary = self.label(self.details, "AI 团队 · 等待读取", bold=True, size=9)
        self.agent_summary.pack(fill="x", pady=(9, 0))
        self.office = Office(self.details, self.BG)
        self.office.pack(pady=(6, 0))
        self.memo = self.label(self.details, "小记 · 等待读取", fg=self.MUTED, size=8, wraplength=282)
        self.memo.pack(fill="x", pady=(4, 0))
        self.agent_rows = [self._member_card(self.details) for _ in range(3)]

        self.footer = tk.Frame(outer, bg=self.BG)
        self.footer.pack(fill="x", padx=12, pady=(8, 8))
        self.status = self.label(self.footer, "正在连接…", fg=self.MUTED, size=8)
        self.status.pack(side="left")
        self.refresh_button = self.button(self.footer, "↻", self.refresh)

    def resize(self):
        self.root.update_idletasks()
        self.root.geometry(f"{self.WIDTH}x{self.root.winfo_reqheight()}")
        self.root.after_idle(self._resize_settle)

    def _resize_settle(self):
        """Configure 事件（圆角卡片高度回写）处理完后，按最终内容高度再校一次。"""
        if self.closing:
            return
        self.root.update_idletasks()
        height = self.root.winfo_reqheight()
        if height != self.root.winfo_height():
            self.root.geometry(f"{self.WIDTH}x{height}")
        if "bottom" in self.snapped:
            # 高度变化（折叠/展开）后保持底边吸附
            y = max(0, self.root.winfo_screenheight() - height - self.TASKBAR)
            self.root.geometry(f"+{self.root.winfo_x()}+{y}")

    def snap_position(self, x: int, y: int) -> tuple[int, int, set[str]]:
        """把候选位置吸附到桌面边缘或水平中线，返回 (x, y, 吸附边集合)。"""
        sw = self.root.winfo_screenwidth()
        sh = self.root.winfo_screenheight()
        w = self.root.winfo_width() or self.WIDTH
        h = self.root.winfo_height() or 1
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
        self.drag_origin = (event.x_root, event.y_root, self.root.winfo_x(), self.root.winfo_y())

    def drag_move(self, event):
        sx, sy, x, y = self.drag_origin
        x += event.x_root - sx
        y += event.y_root - sy
        x, y, self.snapped = self.snap_position(x, y)
        self.root.geometry(f"+{x}+{y}")

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
            w = self.root.winfo_width() or self.WIDTH
            h = self.root.winfo_height() or 1
            x, y = self.root.winfo_x(), self.root.winfo_y()
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
            self.root.geometry(f"+{nx}+{ny}")
            self.save()
        self.root.after(1500, self._watch_screen)

    def toggle_fold(self):
        self.collapsed = not self.collapsed
        self.fold_button.configure(text="展开" if self.collapsed else "收起")
        if self.collapsed:
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
            w = self.root.winfo_width() or self.WIDTH
            h = self.root.winfo_height() or 1
            x, y = self.root.winfo_x(), self.root.winfo_y()
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
        self._sample_team_stats(tasks, agents)
        self.memo.configure(text=self._memo_text())

    def _sample_team_stats(self, tasks: list, agents: list) -> None:
        """按刷新节奏累积今日团队统计：任务数、成员数、忙碌时长。"""
        today = time.strftime("%Y-%m-%d")
        rec = self.team_stats.setdefault(today, {"busy": 0, "peak": 0, "tasks": [], "agents": []})
        now = time.time()
        busy = any((t.get("status") or {}).get("type") == "active" for t in tasks + agents)
        if self.last_sample and busy:
            rec["busy"] = rec.get("busy", 0) + int(min(now - self.last_sample, POLL_SECONDS * 2))
        self.last_sample = now
        rec["peak"] = max(rec.get("peak", 0), len(agents))
        rec["tasks"] = sorted(set(rec.get("tasks", [])) | {str(t["id"]) for t in tasks if t.get("id")})
        rec["agents"] = sorted(set(rec.get("agents", [])) | {str(t["id"]) for t in agents if t.get("id")})
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
        active = sum(1 for t in agents if (t.get("status") or {}).get("type") == "active")
        self.agent_summary.configure(
            text=f"AI 团队 · {active} 干活中 / 共 {len(agents)} 个" if agents else
            ("AI 团队 · 状态读取失败" if data.get("threads_error") else "AI 团队 · 暂无成员"))
        by_id = {t.get("id"): t for t in threads if t.get("id")}
        status_map = {
            "active": ("干活中", self.ACCENT),
            "idle": ("待命", self.MUTED),
            "notLoaded": ("休息中", self.MUTED),
            "systemError": ("异常", "#efad83"),
        }
        agents = sorted(agents, key=lambda t: ((t.get("status") or {}).get("type") == "active",
                                               t.get("recencyAt") or t.get("updatedAt") or 0), reverse=True)
        for i, (card, sprite, name, status, sub) in enumerate(self.agent_rows):
            if i >= len(agents):
                if i == 0:
                    card.pack(fill="x", pady=(4, 0))
                    sprite.set_animation([(AGENT_CLOSED, 0, 0, "")],
                                         {"K": "#0d0f12", "C": "#6b7280", "W": "#16191e"})
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
            sprite.set_animation(agent_frames(state),
                                 {"K": "#0d0f12", "C": color, "W": "#16191e"},
                                 overlay_color="#efad83" if state == "systemError" else self.MUTED)
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
        self.office.update_agents([
            (str(t.get("id") or t.get("agentNickname") or t.get("name") or f"agent{i}"),
             (t.get("status") or {}).get("type", "unknown"),
             status_map.get((t.get("status") or {}).get("type", "unknown"), ("", self.MUTED))[1])
            for i, t in enumerate(agents)
        ])
        has_error = data.get("limits_error") or data.get("threads_error") or data.get("kimi_error")
        self.status.configure(text="部分数据不可用 · 自动重试" if has_error else f"{time.strftime('%H:%M:%S')} 更新 · 每 20 秒")
        self.resize()

    def close(self) -> None:
        if self.closing:
            return
        self.closing = True
        self.save()
        self.client.stopped = True
        self.client.close()
        self.root.destroy()

    def run(self) -> None:
        self.root.mainloop()


if __name__ == "__main__":
    Widget().run()
