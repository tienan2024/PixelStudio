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

        card = tk.Frame(outer, bg=self.CARD, padx=12, pady=9)
        card.pack(fill="x", padx=12)
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

        kimi = tk.Frame(outer, bg=self.CARD, padx=12, pady=9)
        kimi.pack(fill="x", padx=12, pady=(9, 0))
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
        self.task_rows = []
        for _ in range(3):
            label = self.label(self.details, "—", fg=self.MUTED, size=8, wraplength=282)
            label.pack(fill="x", pady=(3, 0))
            self.task_rows.append(label)
        self.agent_summary = self.label(self.details, "子代理 · 等待读取", bold=True, size=9)
        self.agent_summary.pack(fill="x", pady=(9, 0))
        self.agent_rows = []
        for _ in range(2):
            label = self.label(self.details, "—", fg=self.MUTED, size=8, wraplength=282)
            label.pack(fill="x", pady=(3, 0))
            self.agent_rows.append(label)

        self.footer = tk.Frame(outer, bg=self.BG)
        self.footer.pack(fill="x", padx=12, pady=(8, 8))
        self.status = self.label(self.footer, "正在连接…", fg=self.MUTED, size=8)
        self.status.pack(side="left")
        self.refresh_button = self.button(self.footer, "↻", self.refresh)

    def resize(self):
        self.root.update_idletasks()
        height = self.root.winfo_reqheight()
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
        self.agent_summary.configure(text=f"子代理 · {len(agents)}")
        labels = {"active": "运行中", "idle": "空闲", "notLoaded": "未加载", "systemError": "异常"}
        for rows, entries, empty in ((self.task_rows, tasks, "暂无任务记录"), (self.agent_rows, agents, "暂无子代理记录")):
            entries = sorted(entries, key=lambda t: ((t.get("status") or {}).get("type") == "active", t.get("recencyAt") or t.get("updatedAt") or 0), reverse=True)
            for i, label in enumerate(rows):
                if i >= len(entries):
                    label.configure(text=("状态读取失败" if data.get("threads_error") else empty) if i == 0 else "", fg=self.MUTED)
                    continue
                thread = entries[i]
                state = (thread.get("status") or {}).get("type", "unknown")
                name = thread.get("agentNickname") or thread.get("name") or thread.get("agentRole") or "未命名任务"
                name = " ".join(name.split())
                if len(name) > 17:
                    name = name[:16] + "…"
                label.configure(text=f"{'●' if state == 'active' else '○'}  {name} · {labels.get(state, '未知')}", fg=self.ACCENT if state == "active" else self.MUTED)
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
