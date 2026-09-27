"""A fixed-pixel panorama with live agents and a scroll-shaped viewport."""
from __future__ import annotations

from pathlib import Path
import tkinter as tk


class PixelWorld(tk.Canvas):
    WIDTH, HEIGHT = 1120, 224
    INK = "#16232e"
    COLORS = {"active": "#9bdbba", "idle": "#9bbad6", "systemError": "#eb9c80",
              "notLoaded": "#b4becb", "unknown": "#b4becb"}
    LABELS = {"active": "运行中", "idle": "待命", "systemError": "异常",
              "notLoaded": "未加载 · 状态未知", "unknown": "状态未知"}
    BODY = ("............", "...KKKKKK...", "..KCCCCCCK..", ".KCCCCCCCCK.",
            ".KCCWCCWCCK.", ".KCCKCCKCCK.", ".KCCCCCCCCK.", "KCCCCCCCCCCK",
            "KCCCCCCCCCCK", ".KCCCCCCCCK.", "..KKKKKKKK..", "...KK..KK...")

    def __init__(self, parent, *, bg, on_toggle=None, on_select=None):
        super().__init__(parent, width=294, height=self.HEIGHT, bg=bg,
                         highlightthickness=0, bd=0, cursor="hand2",
                         scrollregion=(0, 0, self.WIDTH, self.HEIGHT))
        self.on_toggle, self.on_select = on_toggle, on_select
        self.actors, self.total, self.active = {}, 0, 0
        self.selected, self.frame = None, 0
        self.view_width, self.expanded = 294, False
        self.edge_colors = (bg, bg)
        self.background = None
        self._background()
        self.bind("<Configure>", lambda _e: self._center_view())
        self.bind("<Button-1>", self._click)
        self.bind("<Return>", lambda _e: self.on_toggle() if self.on_toggle else None)
        self._overlay()

    def rect(self, x, y, w, h, color, tag="art"):
        return self.create_rectangle(int(x), int(y), int(x+w), int(y+h),
                                     fill=color, outline="", tags=tag)

    def _background(self):
        self.rect(0, 24, self.WIDTH, 176, "#243341")
        path = Path(__file__).resolve().parent / "assets" / "pixel-world.png"
        if path.is_file():
            try:
                source = tk.PhotoImage(master=self, file=str(path))
                # Nearest-neighbour integer sampling keeps the asset's pixel grid.
                factor = max(1, round(source.width() / self.WIDTH))
                self.background = source.subsample(factor)
                self.create_image(self.WIDTH//2, 112, image=self.background, tags="art")
                return
            except tk.TclError:
                pass
        # Offline fallback: remain usable while a custom panorama is unavailable.
        self.rect(0, 150, self.WIDTH, 50, "#987049")
        self.rect(0, 148, self.WIDTH, 4, "#463d37")
        for y in (164, 182, 198):
            self.rect(0, y, self.WIDTH, 2, "#715438")
            for x in range((y % 3)*24, self.WIDTH, 72):
                self.rect(x, y-14, 2, 14, "#7d5b3e")
        for x in (30, 406, 750, 964):
            self.rect(x-4, 42, 142, 76, self.INK)
            self.rect(x, 46, 134, 68, "#263e59")
            for k, h in enumerate((20, 36, 26, 40, 22, 30)):
                self.rect(x+6+k*20, 110-h, 16, h, "#375571")
                self.rect(x+10+k*20, 114-h, 4, 4, "#d4b878")
            self.rect(x+64, 46, 4, 68, self.INK)
            self.rect(x-6, 114, 146, 6, "#bf9d73")
        for x in (206, 350, 710, 924):
            self.rect(x, 24, 8, 128, "#344757")
            self.rect(x, 24, 2, 126, "#82909a")
        for x in (388, 476, 564, 652):
            self.rect(x-36, 138, 76, 8, "#d1a476")
            self.rect(x-32, 146, 6, 24, "#4d4034")
            self.rect(x+28, 146, 6, 24, "#4d4034")
            self.rect(x-28, 108, 46, 28, self.INK)
            self.rect(x-24, 112, 38, 20, "#344c5f")
            self.rect(x-10, 136, 14, 4, self.INK)
            self.rect(x+24, 128, 8, 10, "#e9d6b0")
            self.rect(x-20, 164, 36, 8, "#3d555a")
        for x in (48, 92, 136, 186, 694, 1090):
            self.rect(x-8, 138, 18, 18, "#b47650")
            self.rect(x-10, 136, 22, 4, "#d59c69")
            self.rect(x, 108, 2, 30, "#415e48")
            for dx, dy, color in ((-12, 112, "#55906c"), (4, 104, "#78ad7b"),
                                   (-6, 92, "#8fbd84"), (6, 124, "#6d9c6b")):
                self.rect(x+dx, dy, 12, 8, color)
        self.rect(232, 76, 48, 74, self.INK)
        for y in range(82, 144, 14):
            self.rect(238, y, 36, 10, "#405567")
            self.rect(262, y+4, 4, 2, "#96d3b2")
        self.rect(294, 76, 40, 44, "#c5b790")
        for x, y in ((300, 82), (314, 86), (304, 102)):
            self.rect(x, y, 12, 10, "#e7c985")
        self.rect(754, 128, 124, 24, "#427462")
        self.rect(748, 148, 136, 16, "#60957a")
        self.rect(756, 164, 6, 8, "#3e3b34")
        self.rect(870, 164, 6, 8, "#3e3b34")
        self.rect(772, 172, 66, 8, "#c79c6b")
        self.rect(944, 126, 70, 38, "#76604a")
        self.rect(950, 92, 58, 34, "#336d7a")
        self.rect(950, 94, 58, 2, "#8dc4bf")
        self.rect(1028, 98, 44, 66, "#293d54")
        self.rect(1034, 106, 32, 30, "#6d867c")
        self.rect(1036, 142, 28, 6, "#d5bb84")

    def set_agents(self, threads):
        ordered = sorted(threads, key=lambda t: ((t.get("status") or {}).get("type") != "active",
                                                str(t.get("id") or t.get("name") or "")))
        self.total = len(threads)
        self.active = sum((t.get("status") or {}).get("type") == "active" for t in threads)
        actors, zone_counts = {}, {}
        for i, thread in enumerate(ordered[:12]):
            key = str(thread.get("id") or thread.get("agentNickname") or f"agent{i}")
            state = (thread.get("status") or {}).get("type", "unknown")
            state = state if state in self.COLORS else "unknown"
            n = zone_counts.get(state, 0)
            zone_counts[state] = n+1
            starts = {"active": 388, "idle": 842, "systemError": 242,
                      "notLoaded": 440, "unknown": 586}
            tx = starts[state] + (n % 4) * (100 if state == "active" else 32)
            ty = 162 + (n // 4) * 4
            actor = self.actors.get(key, {"x": tx, "y": ty})
            actor.update(tx=tx, ty=ty, state=state,
                         name=" ".join(str(thread.get("agentNickname") or thread.get("name") or "AI 成员").split()),
                         role=str(thread.get("agentRole") or "子代理"))
            actors[key] = actor
        self.actors = actors
        if self.selected not in actors:
            self.selected = None
        self._live()

    def advance(self):
        self.frame += 1
        for a in self.actors.values():
            for axis in ("x", "y"):
                delta = a["t"+axis]-a[axis]
                a[axis] += max(-2, min(2, delta))
        self._live()

    def _live(self):
        self.delete("live")
        if self.background is not None:
            # Animated overlays are independent of the generated background.
            for n, cx in enumerate((403, 503, 603, 704)):
                if n < self.active:
                    for row in range(3):
                        self.rect(cx-16, 94+row*5, 14+(self.frame+row)%5*2, 2,
                                  ("#8cc9ad", "#c9b97b", "#8babbc")[row], "live")
            for n in range(3):
                rise = (self.frame+n*4)%12
                self.rect(932+(n%2)*2, 151-rise, 2, 2, "#b6b8a1", "live")
            if self.frame%5 < 2:
                for x, y in ((445, 51), (622, 66), (818, 53)):
                    self.rect(x, y, 2, 2, "#d9be81", "live")
        for key, a in self.actors.items():
            x, y = a["x"], a["y"]
            self.rect(x+2, y+22, 22, 4, "#544738", "live")
            colors = {"K": self.INK, "C": self.COLORS[a["state"]], "W": "#fff1cc"}
            bob = 2 if a["state"] == "active" and self.frame % 2 else 0
            for row, pixels in enumerate(self.BODY):
                for col, p in enumerate(pixels):
                    if p in colors:
                        self.rect(x+col*2, y+row*2-bob, 2, 2, colors[p], "live")
            badge = "?" if a["state"] in {"unknown", "notLoaded"} else "!" if a["state"] == "systemError" else ""
            if badge:
                self.create_text(x+26, y-4, text=badge, fill=colors["C"],
                                 font=("Consolas", 10, "bold"), tags="live")
            if key == self.selected:
                self.rect(x+6, y-8, 12, 2, "#f3ca7d", "live")
        self._overlay()

    def set_viewport(self, width, *, expanded, left_bg, right_bg):
        self.view_width = width
        self.expanded = expanded
        self.edge_colors = left_bg, right_bg
        self.configure(width=width)
        self._center_view()

    def _center_view(self):
        self.xview_moveto(max(0, (self.WIDTH-self.view_width)/2) / self.WIDTH)
        self._overlay()

    def _overlay(self):
        self.delete("overlay")
        left, w = int(self.canvasx(0)), self.view_width
        self.rect(left, 0, w, 24, "#1c2b36", "overlay")
        self.rect(left, 200, w, 24, "#1c2b36", "overlay")
        self.rect(left+10, 23, w-20, 1, "#3d505c", "overlay")
        title = "PIXEL STUDIO" if w < 420 else "PIXEL STUDIO  /  一间会呼吸的工作室"
        self.create_text(left+20, 12, text=title, anchor="w", fill="#e2c58b",
                         font=("Microsoft YaHei UI", 8, "bold"), tags="overlay")
        count = f"{self.active} 运行 · {self.total} 成员"
        if self.total > len(self.actors):
            count += f" · 展示 {len(self.actors)}"
        if w >= 420:
            self.create_text(left+w-22, 12, text=count, anchor="e", fill="#a5c8bc",
                             font=("Microsoft YaHei UI", 8), tags="overlay")
        a = self.actors.get(self.selected)
        info = (f"{a['name'][:20]} · {a['role'][:18]} · {self.LABELS[a['state']]}" if a else
                "点击成员查看身份  ·  点击卷轴收起" if self.expanded else f"{count}  ·  点击展开画卷")
        if w < 420 and a:
            info = f"{a['name'][:12]} · {self.LABELS[a['state']]}"
        self.create_text(left+w/2, 212, text=info, fill="#aebdc5",
                         font=("Microsoft YaHei UI", 8), tags="overlay")
        for x in (left+2, left+w-12):
            self.rect(x, 12, 10, 200, "#382f2c", "overlay")
            self.rect(x+2, 8, 6, 208, "#a88659", "overlay")
            self.rect(x+2, 14, 2, 196, "#e3c18a", "overlay")
            self.rect(x-2, 8, 14, 4, "#d4b078", "overlay")
            self.rect(x-2, 212, 14, 4, "#d4b078", "overlay")
        # Stepped corner masks retain the pixel vocabulary and desktop transparency.
        for y, cut in ((0, 8), (2, 4), (4, 2)):
            for yy in (y, self.HEIGHT-y-2):
                self.rect(left, yy, cut, 2, self.edge_colors[0], "overlay")
                self.rect(left+w-cut, yy, cut, 2, self.edge_colors[1], "overlay")

    def _click(self, event):
        if not self.expanded or event.x <= 16 or event.x >= self.view_width-16:
            if self.on_toggle:
                self.on_toggle()
            return
        x, y = self.canvasx(event.x), event.y
        for key, a in reversed(list(self.actors.items())):
            if a["x"]-4 <= x <= a["x"]+30 and a["y"]-8 <= y <= a["y"]+28:
                self.selected = key
                self._overlay()
                if self.on_select:
                    self.on_select(a)
                return
