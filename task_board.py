"""Compact, actionable task records; no message bodies or model requests."""

from datetime import datetime
import math
import os
import time
import tkinter as tk

from task_records import task_url


class TaskBoard(tk.Frame):
    """A collapsed summary and a paginated list that keeps the world prominent."""

    FILTERS = (("attention", "待处理"), ("active", "进行中"), ("recent", "最近"))

    def __init__(self, parent, *, card_factory, palette, on_resize, on_preference,
                 collapsed=True, selected_filter=None):
        super().__init__(parent, bg=palette["bg"])
        self.colors = palette
        self.on_resize, self.on_preference = on_resize, on_preference
        self.collapsed = bool(collapsed)
        self.selected_filter = (selected_filter if isinstance(selected_filter, str)
                                and selected_filter in dict(self.FILTERS) else None)
        self.snapshot = {"rows": [], "counts": {}, "source": "unavailable"}
        self.page, self.notice_timer = 0, None
        self.page_size = 2 if self.winfo_screenheight() < 1200 else 3
        self.display_rows = []

        header = tk.Frame(self, bg=palette["bg"])
        header.pack(fill="x")
        self._label(header, "任务记录", bold=True).pack(side="left")
        self.summary = self._label(header, "正在读取…", muted=True, size=8)
        self.summary.pack(side="left", padx=7)
        self.toggle_button = self._button(header, "查看", self.toggle)
        self.toggle_button.pack(side="right")

        self.viewport = tk.Canvas(self, bg=palette["bg"], highlightthickness=0,
                                  width=282, height=1, yscrollincrement=18)
        self.body = tk.Frame(self.viewport, bg=palette["bg"])
        self.body_window = self.viewport.create_window(0, 0, window=self.body, anchor="nw")
        self.scrollbar = tk.Scrollbar(self.viewport, orient="vertical", width=11,
                                      command=self.viewport.yview)
        self.viewport.configure(yscrollcommand=self.scrollbar.set)
        self.viewport.bind("<Configure>", self._viewport_size)
        self.viewport.bind("<MouseWheel>", self._scroll)
        self.body.bind("<Configure>", self._body_size)
        tabs = tk.Frame(self.body, bg=palette["bg"])
        tabs.pack(fill="x", pady=(5, 0))
        self.tabs = {}
        for key, text in self.FILTERS:
            button = self._button(tabs, text, lambda key=key: self.select_filter(key))
            button.pack(side="left", fill="x", expand=True)
            self.tabs[key] = button
        self.empty = self._label(self.body, "正在读取任务状态…", muted=True,
                                 size=8, justify="left", wraplength=272)
        self.cards = []
        for slot in range(self.page_size):
            shell = card_factory(self.body, bg=palette["bg"], fill=palette["card"])
            card = shell.body
            card.configure(padx=8, pady=6)
            top = tk.Frame(card, bg=palette["card"])
            top.pack(fill="x")
            state = self._label(top, "", bg=palette["card"], size=8)
            state.pack(side="right", anchor="n", padx=(4, 0))
            title = self._label(top, "", bold=True, bg=palette["card"],
                                wraplength=198, justify="left")
            title.pack(side="left", fill="x", expand=True)
            detail = self._label(card, "", muted=True, bg=palette["card"],
                                 size=8, wraplength=270, justify="left")
            detail.pack(fill="x", pady=(2, 0))
            actions = tk.Frame(card, bg=palette["card"])
            actions.pack(fill="x", pady=(3, 0))
            hint = self._label(actions, "", muted=True, bg=palette["card"], size=8)
            hint.pack(side="left")
            copy = self._button(actions, "复制信息", lambda slot=slot: self.copy_record(slot),
                                bg=palette["card"], size=8)
            copy.pack(side="right")
            open_button = self._button(actions, "打开会话", lambda slot=slot: self.open_record(slot),
                                       bg=palette["card"], size=8)
            open_button.pack(side="right", padx=(0, 3))
            self.cards.append((shell, title, state, detail, hint, open_button))
        self.pager = tk.Frame(self.body, bg=palette["bg"])
        self.prev = self._button(self.pager, "‹ 上页", lambda: self.move_page(-1), size=8)
        self.prev.pack(side="left")
        self.next = self._button(self.pager, "下页 ›", lambda: self.move_page(1), size=8)
        self.next.pack(side="right")
        self.page_label = self._label(self.pager, "", muted=True, size=8)
        self.page_label.pack(expand=True)
        self.notice = self._label(self.body, "", muted=True, size=8,
                                  wraplength=272, justify="left")
        if not self.collapsed:
            self.viewport.pack(fill="x")
        self._bind_wheel(self.body)
        self._render()

    def _viewport_size(self, event):
        self.viewport.itemconfigure(self.body_window, width=max(1, event.width-12))

    def _body_size(self, _event=None):
        self.viewport.configure(scrollregion=self.viewport.bbox(self.body_window))
        self.on_resize()

    def _bind_wheel(self, widget):
        widget.bind("<MouseWheel>", self._scroll)
        for child in widget.winfo_children():
            self._bind_wheel(child)

    def _scroll(self, event):
        if event.delta:
            self.viewport.yview_scroll(-1 if event.delta > 0 else 1, "units")
        return "break"

    def fit_height(self, total_height, max_height):
        """Limit the expanded records to the screen space left by the widget."""
        if self.collapsed:
            return False
        current = int(self.viewport.cget("height"))
        content = self.body.winfo_reqheight()
        available = max(48, max_height-(total_height-current))
        target = max(1, min(content, available))
        if target < content:
            self.scrollbar.place(relx=1, x=-11, y=0, width=11, relheight=1)
        else:
            self.scrollbar.place_forget()
        if current == target:
            return False
        self.viewport.configure(height=target)
        self.on_resize()
        return True

    def _label(self, parent, text, *, bg=None, muted=False, bold=False, size=9, **kw):
        return tk.Label(parent, text=text, bg=bg or self.colors["bg"],
                        fg=self.colors["muted"] if muted else self.colors["text"],
                        font=("Microsoft YaHei UI", size, "bold" if bold else "normal"),
                        anchor="w", borderwidth=0, **kw)

    def _button(self, parent, text, command, *, bg=None, size=9):
        return tk.Button(parent, text=text, command=command, bg=bg or self.colors["bg"],
                         fg=self.colors["muted"], activebackground=self.colors["card"],
                         activeforeground=self.colors["text"], relief="flat", borderwidth=0,
                         cursor="hand2", padx=4, pady=1, font=("Microsoft YaHei UI", size))

    def _preferred_filter(self):
        counts = self.snapshot.get("counts", {})
        return "attention" if counts.get("attention") else "active" if counts.get("active") else "recent"

    def toggle(self):
        self.collapsed = not self.collapsed
        if self.collapsed:
            self.viewport.pack_forget()
        else:
            if self.selected_filter is None:
                self.selected_filter = self._preferred_filter()
            self.viewport.pack(fill="x")
        self._render()
        self.on_preference()

    def select_filter(self, key):
        self.selected_filter, self.page = key, 0
        self.viewport.yview_moveto(0)
        self._render()
        self.on_preference()

    def move_page(self, delta):
        self.page += delta
        self.viewport.yview_moveto(0)
        self._render()

    def set_snapshot(self, snapshot):
        anchor = self.display_rows[0]["id"] if self.display_rows else None
        self.snapshot = snapshot or {"rows": [], "counts": {}, "source": "unavailable"}
        # A background refresh should not move the page being read.
        rows = self._filtered()
        if anchor:
            for index, row in enumerate(rows):
                if row["id"] == anchor:
                    self.page = index // self.page_size
                    break
        self._render()

    def _filtered(self):
        key = self.selected_filter or self._preferred_filter()
        return [row for row in self.snapshot.get("rows", []) if row["bucket"] == key]

    @staticmethod
    def _short(text, size):
        return text if len(text) <= size else text[:size-1] + "…"

    @staticmethod
    def _when(stamp):
        if not stamp:
            return "时间未知"
        delta = max(0, time.time()-stamp)
        if delta < 60:
            return "刚刚更新"
        if delta < 3600:
            return f"{int(delta//60)} 分钟前"
        if delta < 86400:
            return f"{int(delta//3600)} 小时前"
        return datetime.fromtimestamp(stamp).strftime("%m/%d %H:%M")

    def _render(self):
        counts = self.snapshot.get("counts", {})
        available = self.snapshot.get("source") != "unavailable"
        total = sum(counts.values())
        parts = []
        if counts.get("attention"):
            parts.append(f"{counts['attention']} 待处理")
        if counts.get("active"):
            parts.append(f"{counts['active']} 进行中")
        self.summary.configure(text=" · ".join(parts) if parts else f"近期 {total} 项" if available else "状态暂不可用")
        self.summary.configure(fg="#efad83" if counts.get("attention") else self.colors["muted"])
        self.toggle_button.configure(text="查看" if self.collapsed else "收起")
        if self.collapsed:
            self.on_resize()
            return
        key = self.selected_filter or self._preferred_filter()
        for tab, button in self.tabs.items():
            text = dict(self.FILTERS)[tab]
            button.configure(text=f"{text} {counts.get(tab, 0)}",
                             fg=self.colors["accent"] if tab == key else self.colors["muted"],
                             bg=self.colors["card"] if tab == key else self.colors["bg"])
        rows = self._filtered()
        pages = max(1, math.ceil(len(rows)/self.page_size))
        self.page = max(0, min(self.page, pages-1))
        self.display_rows = rows[self.page*self.page_size:(self.page+1)*self.page_size]
        self.empty.pack_forget()
        self.pager.pack_forget()
        self.notice.pack_forget()
        for slot, (shell, title, state, detail, hint, open_button) in enumerate(self.cards):
            if slot >= len(self.display_rows):
                shell.pack_forget()
                continue
            row = self.display_rows[slot]
            shell.pack(fill="x", pady=(4, 0))
            title.configure(text=self._short(row["title"], 32))
            state.configure(text=row["status_text"], fg="#efad83" if row["bucket"] == "attention"
                            else self.colors["accent"] if row["bucket"] == "active" else self.colors["muted"])
            context = self._short(row["project"] or "无项目", 14)
            if row["model"]:
                context += " · " + self._short(row["model"], 24)
            detail.configure(text=context + "\n" + self._when(row["updated_at"]) + " · " + row["label_reason"])
            hint.configure(text="待确认" if row["status"] == "uncertain" else "")
            open_button.configure(text="查看结果" if row["status"] == "completed" else "打开会话",
                                  state="normal" if row["openable"] and hasattr(os, "startfile") else "disabled")
        if not rows:
            empty_text = ("任务状态读取暂不可用，稍后自动重试。" if not available else
                          {"attention": "没有需要处理的任务。", "active": "当前没有确认正在运行的任务。",
                           "recent": "暂无近期任务记录。"}[key])
            self.empty.configure(text=empty_text)
            self.empty.pack(fill="x", pady=(8, 4))
        if pages > 1:
            self.prev.configure(state="normal" if self.page else "disabled")
            self.next.configure(state="normal" if self.page < pages-1 else "disabled")
            self.page_label.configure(text=f"{self.page+1} / {pages}")
            self.pager.pack(fill="x", pady=(4, 0))
        if self.notice_timer is None:
            self.notice.configure(text="备用线程列表 · 未加载不代表完成" if self.snapshot.get("source") == "fallback"
                                  else "按最新一轮状态显示 · 点击查看完整会话" if available else "")
        self.notice.pack(fill="x", pady=(4, 0))
        self.on_resize()

    def _notify(self, text):
        if self.notice_timer is not None:
            self.after_cancel(self.notice_timer)
        self.notice.configure(text=text)
        self.notice_timer = self.after(4000, self._clear_notice)
        self.on_resize()

    def _clear_notice(self):
        self.notice_timer = None
        self._render()

    def open_record(self, slot):
        if slot >= len(self.display_rows):
            return
        url = task_url(self.display_rows[slot]["id"])
        if not url:
            self._notify("这条记录没有可跳转的会话 ID，可复制任务信息。")
            return
        try:
            os.startfile(url)
            self._notify("已请求 Codex 打开此任务。")
        except (OSError, AttributeError):
            if self._copy(url):
                self._notify("无法打开 Codex，已复制会话地址。")
            else:
                self._notify("无法打开 Codex，剪贴板暂不可用。")

    def _copy(self, text):
        try:
            self.clipboard_clear()
            self.clipboard_append(text)
            return True
        except tk.TclError:
            return False

    def copy_record(self, slot):
        if slot >= len(self.display_rows):
            return
        row = self.display_rows[slot]
        text = (f"任务：{row['title']}\n状态：{row['status_text']}\n说明：{row['label_reason']}\n"
                f"建议：{row['next_action']}\n项目：{row['cwd'] or '无项目'}\n模型：{row['model'] or '未知'}\n"
                f"会话 ID：{row['id']}\n")
        if row["updated_at"]:
            text += f"更新时间：{datetime.fromtimestamp(row['updated_at']).astimezone().isoformat(timespec='seconds')}\n"
        if task_url(row["id"]):
            text += f"会话地址：{task_url(row['id'])}\n"
        if self._copy(text):
            self._notify("已复制完整标题、状态、项目和会话地址。")
        else:
            self._notify("剪贴板暂不可用，请稍后再试。")

    def close(self):
        if self.notice_timer is not None:
            self.after_cancel(self.notice_timer)
            self.notice_timer = None
