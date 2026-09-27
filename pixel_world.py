"""A fixed-pixel panorama with live agents and a scroll-shaped viewport."""
from __future__ import annotations

from pathlib import Path
import random
import tkinter as tk
import zlib

from character_sprites import character_bank, draw_fallback, member_key


class PixelWorld(tk.Canvas):
    WIDTH, HEIGHT = 1120, 224
    INK = "#16232e"
    COLORS = {"active": "#9bdbba", "idle": "#9bbad6", "systemError": "#eb9c80",
              "notLoaded": "#b4becb", "unknown": "#b4becb"}
    LABELS = {"active": "运行中", "idle": "待命", "systemError": "异常",
              "notLoaded": "未加载 · 状态未知", "unknown": "状态未知"}

    # 功能分区（世界坐标 x，详见 docs/idle-events.md）
    ZONES = {"fireplace": (20, 70), "garden": (130, 255), "server": (246, 300),
             "window": (315, 375), "desk": (390, 700), "bookshelf": (620, 700),
             "couch": (730, 860), "aquarium": (860, 955), "arcade": (960, 1045)}
    DESK_SEATS = (410, 505, 600, 680)
    LANES = (180, 189, 198)   # 三条走道车道，按成员 key 固定（间距 >6px 才互不挡路）
    SPEED = 3                 # 水平速度（px/拍，一拍 280ms）
    # 待机事件池：(目标, 权重)；stroll=走廊踱步，visit=串门寒暄
    EVENTS = (("couch", 3), ("aquarium", 2), ("arcade", 1.5), ("bookshelf", 2),
              ("window", 2), ("garden", 1.5), ("fireplace", 1.5), ("server", 0.5),
              ("stroll", 2), ("visit", 1))
    ACTIVE_EVENTS = (("window", 2), ("stroll", 2), ("visit", 1))  # active 忙里偷闲

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
        self.rng = random.Random()
        self.characters = character_bank(self)
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

    def _home_x(self, state, n):
        """状态决定家：active 回工位，报错去机柜面壁，其余在休息区。"""
        if state == "active":
            return self.DESK_SEATS[n % 4] + (n // 4) * 26
        if state == "systemError":
            return 250 + (n % 3) * 24
        if state == "idle":
            return 745 + (n % 4) * 30
        if state == "notLoaded":
            return 865 + (n % 3) * 26
        return 320 + (n % 3) * 22

    def set_agents(self, threads):
        self.characters.register(threads)
        ordered = sorted(threads, key=lambda t: ((t.get("status") or {}).get("type") != "active",
                                                str(t.get("id") or t.get("name") or "")))
        self.total = len(threads)
        self.active = sum((t.get("status") or {}).get("type") == "active" for t in threads)
        actors, counts = {}, {}
        for thread in ordered[:12]:
            key = member_key(thread)
            state = (thread.get("status") or {}).get("type", "unknown")
            state = state if state in self.COLORS else "unknown"
            n = counts.get(state, 0)
            counts[state] = n + 1
            home = self._home_x(state, n)
            actor = self.actors.get(key)
            if actor is None:  # 新成员：固定车道、思考冷却随机打散
                actor = {"x": home, "y": 189,
                         "lane": self.LANES[zlib.crc32(key.encode("utf-8")) % 3],
                         "event": None,
                         "next_think": self.frame + self.rng.randint(20, 120)}
            actor.update(home=home, state=state,
                         name=" ".join(str(thread.get("agentNickname") or thread.get("name") or "AI 成员").split()),
                         role=str(thread.get("agentRole") or "子代理"))
            actors[key] = actor
        self.actors = actors
        if self.selected not in actors:
            self.selected = None
        self._live()

    # -- 待机随机事件（docs/idle-events.md） -------------------------------

    def _pick_event(self, key, a):
        """按状态权重抽一个事件，返回 {'x': 目标, 'dwell': 停留拍数} 或 None。"""
        state = a["state"]
        if state == "active":  # 干活是主行为，小概率短暂离席
            if self.rng.random() > 0.15:
                a["next_think"] = self.frame + self.rng.randint(60, 160)
                return None
            table = self.ACTIVE_EVENTS
        elif state == "systemError":  # 面壁为主，偶发短距踱步
            if self.rng.random() > 0.08:
                a["next_think"] = self.frame + self.rng.randint(80, 200)
                return None
            return {"x": self.rng.randint(220, 320), "dwell": self.rng.randint(6, 12)}
        else:
            table = self.EVENTS
        total = sum(w for _, w in table)
        roll = self.rng.uniform(0, total)
        for zone, w in table:
            roll -= w
            if roll <= 0:
                break
        if zone == "stroll":
            x, dwell = self.rng.randint(60, 1060), self.rng.randint(6, 12)
        elif zone == "visit":
            others = [o for k, o in self.actors.items() if k != key]
            if not others:
                x, dwell = self.rng.randint(60, 1060), self.rng.randint(6, 12)
            else:
                host = self.rng.choice(others)
                x = max(40, min(1080, int(host["x"]) + self.rng.choice((-1, 1)) * 20))
                dwell = self.rng.randint(10, 20)
        else:
            x1, x2 = self.ZONES[zone]
            x, dwell = self.rng.randint(x1, x2), self.rng.randint(15, 50)
        if state == "active":
            dwell = max(6, dwell // 2)  # 忙里偷闲，停留减半
        # 目标避占：与其他成员的目标/站位保持 ≥30px（串门除外）
        if zone != "visit":
            taken = [int(o["event"]["x"]) if o.get("event") else int(o["x"])
                     for k, o in self.actors.items() if k != key]
            for _ in range(6):
                if all(abs(x - t) >= 30 for t in taken):
                    break
                x += self.rng.choice((-1, 1)) * self.rng.randint(12, 36)
                x = max(40, min(1080, x))
        return {"x": x, "dwell": dwell}

    def _think(self, key, a):
        if a["event"] is None and not a.get("moving") and self.frame >= a["next_think"]:
            a["event"] = self._pick_event(key, a)

    def _blocked(self, a, direction):
        """同车道（|Δy|<6）行进方向 28px 内有成员则视为被挡。"""
        return any(o is not a and abs(o["y"] - a["y"]) < 6
                   and 0 < (o["x"] - a["x"]) * direction <= 28
                   for o in self.actors.values())

    def _drift_lane(self, a):
        dy = a["lane"] - a["y"]
        if dy:
            a["y"] += max(-1, min(1, dy))

    def _pass_lane(self, a):
        """借道：换到另一条没人挡道的车道绕过去（兽群式避让）。"""
        for ny in self.LANES:
            if ny == a["lane"]:
                continue
            if not any(o is not a and abs(o["y"] - ny) < 4 and abs(o["x"] - a["x"]) < 24
                       for o in self.actors.values()):
                dy = ny - a["y"]
                if dy:
                    a["y"] += max(-1, min(1, dy))
                    return True
        return False

    def _step(self, key, a):
        """走位：车道固定 + 分离避让 + 借道超车 + 堵死超时放弃。"""
        ev = a["event"]
        if ev and "until" in ev:  # 到位停留中
            a["moving"] = False
            self._drift_lane(a)
            if self.frame >= ev["until"]:
                a["event"] = None
                a["next_think"] = self.frame + self.rng.randint(40, 140)
            return
        tx = ev["x"] if ev else a["home"]
        dx = tx - a["x"]
        moved = False
        if dx:
            step = max(-self.SPEED, min(self.SPEED, dx))
            direction = 1 if step > 0 else -1
            if self._blocked(a, direction):
                a["blocked_for"] = a.get("blocked_for", 0) + 1
                moved = self._pass_lane(a)
                if a["blocked_for"] > 40:  # 实在过不去，放弃这次事件回家
                    a["event"] = None
                    a["blocked_for"] = 0
                    a["next_think"] = self.frame + self.rng.randint(40, 120)
            else:
                a["blocked_for"] = 0
                a["x"] += step
                moved = True
                self._drift_lane(a)
        else:
            self._drift_lane(a)
        a["moving"] = moved
        if ev and a["x"] == tx:  # 到达事件点，开始停留
            ev["until"] = self.frame + ev["dwell"]
            a["blocked_for"] = 0

    def advance(self):
        self.frame += 1
        for key, a in self.actors.items():
            self._think(key, a)
            self._step(key, a)
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
        for key, a in sorted(self.actors.items(), key=lambda item: item[1]["y"]):
            x, y = a["x"], a["y"]  # Position is the feet, not the sprite's top-left.
            self.rect(x-11, y-1, 22, 3, "#544738", "live")
            frame, dx, dy = self.characters.sample(key, a["state"], moving=a.get("moving", False))
            if frame is not None:
                self.create_image(x+dx, y+dy, image=frame, anchor="s", tags="live")
                width, height = frame.width(), frame.height()
            else:
                draw_fallback(self, x+dx, y+dy, self.characters.assignments.get(key, 0), "live")
                width, height = 20, 40
            a["bounds"] = (x-width//2-5, y-height-8, x+width//2+5, y+4)
            badge = "?" if a["state"] in {"unknown", "notLoaded"} else "!" if a["state"] == "systemError" else ""
            if badge:
                self.create_text(x+width//2+4, y-height-1, text=badge, fill=self.COLORS[a["state"]],
                                 font=("Consolas", 10, "bold"), tags="live")
            if key == self.selected:
                self.rect(x-9, y+3, 18, 2, "#f3ca7d", "live")
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
        for key, a in reversed(sorted(self.actors.items(), key=lambda item: item[1]["y"])):
            x1, y1, x2, y2 = a.get("bounds", (a["x"]-18, a["y"]-64, a["x"]+18, a["y"]+4))
            if x1 <= x <= x2 and y1 <= y <= y2:
                self.selected = key
                self._overlay()
                if self.on_select:
                    self.on_select(a)
                return
