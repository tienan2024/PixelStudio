"""Small Windows desktop widget for Codex limits and live thread/agent state."""
from __future__ import annotations

import json
import math
import os
from pathlib import Path
import queue
import shutil
import subprocess
import threading
import time
import tkinter as tk


HOME = Path.home()
PREFERENCES = Path(__file__).resolve().parents[1] / ".runtime/widget-preferences.json"
POLL_SECONDS = 20


def codex_executable() -> str | None:
    configured = os.environ.get("CODEX_CLI_PATH")
    if configured and Path(configured).is_file():
        return configured
    candidates = list((HOME / "AppData/Local/OpenAI/Codex/bin").glob("*/codex.exe"))
    return str(max(candidates, key=lambda p: p.stat().st_mtime)) if candidates else shutil.which("codex.exe")


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
        self._build()
        self.root.update_idletasks()
        height = self.root.winfo_reqheight()
        max_x = max(0, self.root.winfo_screenwidth() - self.WIDTH - 16)
        max_y = max(0, self.root.winfo_screenheight() - height - 48)
        try:
            x = max(0, min(max_x, int(prefs.get("x", max_x))))
            y = max(0, min(max_y, int(prefs.get("y", 80))))
        except (ValueError, TypeError):
            x, y = max_x, 80
        self.root.geometry(f"{self.WIDTH}x{height}+{x}+{y}")
        self.root.deiconify()
        self.root.after(200, self._drain)
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
            target.bind("<ButtonRelease-1>", lambda _: self.save())
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

        kimi = tk.Frame(outer, bg=self.BG)
        kimi.pack(fill="x", padx=14, pady=9)
        self.label(kimi, "Kimi Code", bold=True).pack(side="left")
        self.label(kimi, "额度待接入", fg=self.MUTED, size=8).pack(side="right")
        tk.Frame(outer, bg="#30353e", height=1).pack(fill="x", padx=12)

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
        self.root.geometry(f"{self.WIDTH}x{self.root.winfo_reqheight()}")

    def drag_start(self, event):
        self.drag_origin = (event.x_root, event.y_root, self.root.winfo_x(), self.root.winfo_y())

    def drag_move(self, event):
        sx, sy, x, y = self.drag_origin
        x += event.x_root - sx
        y += event.y_root - sy
        self.root.geometry(f"+{max(0, x)}+{max(0, y)}")

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
            PREFERENCES.parent.mkdir(parents=True, exist_ok=True)
            PREFERENCES.write_text(json.dumps({"x": self.root.winfo_x(), "y": self.root.winfo_y(),
                                              "pinned": self.pinned, "collapsed": self.collapsed}), encoding="utf-8")
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
            self.events.put(("ok", self.client.snapshot()))
        except Exception as exc:
            self.events.put(("error", str(exc)))

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
        has_error = data.get("limits_error") or data.get("threads_error")
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
