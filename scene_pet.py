"""An independent cat: model plans, local movement and completed care only."""
import json
import math
from pathlib import Path
import time
import tkinter as tk

from pet_brain import PetBrain
from pet_state import PetState

ASSETS = Path(__file__).resolve().parent / "assets"
CARE = {"feed": "喂粮", "water": "添水", "play": "陪玩", "pet": "摸摸"}
ACTIVITY = {"idle": "看着大家", "walk": "小步巡游", "eat": "认真吃饭",
            "drink": "喝水中", "sleep": "窝里打盹", "play": "追毛线球", "pet": "呼噜呼噜"}


class CatSprites:
    def __init__(self, master):
        self.frames, self.mirrors = {}, {}
        try:
            spec = json.loads((ASSETS / "cat-actions.json").read_text(encoding="utf-8"))
            sheet = tk.PhotoImage(master=master, file=str(ASSETS / spec["image"]))
            for pose, rectangles in spec["frames"].items():
                frames = []
                for x1, y1, x2, y2 in rectangles:
                    frame = tk.PhotoImage(master=master, width=x2-x1, height=y2-y1)
                    master.tk.call(frame, "copy", sheet, "-from", x1, y1, x2, y2)
                    frames.append(frame.subsample(spec["subsample"]))
                self.frames[pose] = frames
        except (OSError, ValueError, KeyError, TypeError, tk.TclError):
            self.frames.clear()

    def sample(self, pose, facing, now):
        frames = self.frames.get(pose, self.frames.get("idle", []))
        if not frames:
            return None
        if pose == "idle":
            index = 1 if now % 4.7 < .22 else 0
        else:
            index = int(now / {"walk": .15, "eat": .3, "sleep": 1.5, "play": .32}.get(pose, .5))
        frame = frames[index % len(frames)]
        if facing < 0:
            if str(frame) not in self.mirrors:
                self.mirrors[str(frame)] = frame.subsample(-1, 1)
            frame = self.mirrors[str(frame)]
        return frame


class PetLayer:
    def __init__(self, canvas, rooms, assets, members, *, live=False):
        self.canvas, self.members, self.assets = canvas, members, assets
        self.rooms = {room.id: room for room in rooms}
        runtime = Path(__file__).resolve().parents[1] / ".runtime"
        self.state = PetState(runtime / "widget-pet-state.json" if live else None)
        self.brain = PetBrain(runtime / "widget-pet-brain.json" if live else None)
        self.live = live
        self.sprites = CatSprites(canvas)
        self.props = json.loads((ASSETS / "cat-corner.json").read_text(encoding="utf-8"))["objects"]
        self.props = {p["id"]: dict(p, x=self.rooms[p["room"]].x+p["x"]) for p in self.props}
        self.x, self.y = self.rooms["lounge"].x+263., 190.
        self.target, self.facing, self.pose = None, 1, "idle"
        self.plan, self.stage, self.care_started = None, "idle", 0.
        self.requested_action, self.message = None, ""
        self.next_decision, self.next_check = time.monotonic()+2, 0.
        self.last_need, self.last_tick = self.state.need(), time.monotonic()
        self.approved_at, self.next_roam, self.bubble_until = 0., 0., 0.
        self.selected, self.panel_hits, self.hits = False, [], {}
        self.journal_page, self.show_journal = 0, False
        self.pointer = None
        self.bounds, self.label_bounds, self.photo = (0, 0, 0, 0), (0, 0, 0, 0), None

    @property
    def room_id(self):
        return min(self.rooms.values(), key=lambda r: abs(r.center-self.x)).id

    def request_care(self, action):
        self.selected = True
        self.show_journal = False
        if not self.live:
            self.message = "场景预览 · 未连接模型"
        elif not self.brain.snapshot()["enabled"]:
            self.message = "先开启右上方的模型决策"
        elif self.brain.snapshot()["busy"]:
            self.message = "正在等待橘子的决定，请稍候"
        elif self.plan and self.plan["action"] in CARE:
            self.message = "正在照顾橘子，请稍等一会儿"
        else:
            self.requested_action = action
            self.message = f"已排队：{CARE[action]}，可以取消"
            self.next_check = 0

    @staticmethod
    def _time_ago(at):
        seconds = max(0, time.time()-at)
        if seconds < 60:
            return "刚刚"
        if seconds < 3600:
            return f"{int(seconds//60)} 分钟前"
        if seconds < 86400:
            return f"{int(seconds//3600)} 小时前"
        return f"{int(seconds//86400)} 天前"

    @staticmethod
    def _wait_text(seconds):
        seconds = max(1, math.ceil(seconds))
        return f"{seconds} 秒" if seconds < 60 else f"{math.ceil(seconds/60)} 分钟"

    def status_text(self, brain=None):
        brain = brain or self.brain.snapshot()
        if not self.live:
            return "场景预览 · 未连接模型"
        if not brain["enabled"]:
            return brain["error"] or "自动照护已暂停"
        if self.plan and self.plan["action"] in CARE:
            if self.members.meeting:
                return "伙伴在开会 · 散会后继续照顾"
            name = "Codex" if self.plan["caregiver"] == "codex" else "Kimi"
            verb = CARE[self.plan["action"]]
            return f"{name} 正在{verb}" if self.stage == "caring" else f"{name} 正在赶来{verb}"
        if brain["busy"]:
            return "橘子正在想一想…"
        wait = brain.get("retry_after_seconds", 0)
        if self.requested_action:
            suffix = "等散会" if self.members.meeting else self._wait_text(wait)+"后可安排" if wait else "等待安排"
            return f"已排队：{CARE[self.requested_action]} · {suffix}"
        if brain["error"]:
            return brain["error"]
        if not brain["calls_remaining"]:
            return "自动思考休息中 · "+self._wait_text(wait)+"后可用"
        if self.plan and self.plan["action"] in {"sleep", "explore"}:
            return f"橘子 · {ACTIVITY[self.pose]}"
        return self.message or f"橘子在{self.rooms[self.room_id].label} · {ACTIVITY[self.pose]}"

    def toggle(self):
        if not self.live:
            self.message = "场景预览 · 未连接模型"
            return
        enabled = not self.brain.snapshot()["enabled"]
        self.brain.set_enabled(enabled)
        if not enabled:
            self.members.end_care()
            self.plan, self.target, self.requested_action = None, None, None
            self.pose, self.stage = "idle", "idle"
            self.message = "已暂停新决策和未完成照护"
        else:
            self.next_decision, self.next_check = time.monotonic(), 0
            self.message = "模型决策已开启"

    def _context(self, companions, meeting):
        state = self.state.snapshot()
        return dict(name=state["name"], needs={k: state[k] for k in ("fullness", "water", "energy", "affection")},
                    need=self.state.need(), requested_action=self.requested_action, meeting=meeting,
                    companions=[dict(id=p["id"].removeprefix("companion:"),
                                     state=p.get("status", {}).get("type", "unknown")) for p in companions],
                    last_action=(self.brain.snapshot()["last_decision"] or {}).get("action", ""))

    def _accept(self, decision, now):
        self.members.end_care()
        self.plan, self.approved_at = decision, now
        self.stage, self.care_started, self.message = "queued", 0., ""
        self.pose, self.target = "idle", None
        self.bubble_until, self.next_decision = now+24, now+600
        action = decision["action"]
        if action in ("feed", "water", "play"):
            prop = self.props[{"feed": "food", "water": "water", "play": "toy"}[action]]
            self.target = (prop["x"]-24, 190.)
        elif action == "sleep":
            prop = self.props["bed"]
            self.target = (prop["x"], 190.)
        elif action == "explore":
            self.target = (self.rooms[decision["location"]].center+52, 190.)
            self.next_roam = now+20

    def offer_care(self, caregiver, action, *, model, decided_at):
        """Accept a companion-model intention, using the normal care executor."""
        if (not isinstance(caregiver, str) or not isinstance(action, str)
                or caregiver not in ("codex", "kimi") or action not in CARE):
            return False
        brain = self.brain.snapshot()
        actor = self.members.actors.get("companion:"+caregiver)
        if (not self.live
                or self.members.meeting or not actor or actor["state"] != "idle"
                or not brain["enabled"] or brain["busy"] or self.requested_action
                or (self.plan and self.plan["action"] in CARE)):
            return False
        self._accept(dict(action=action, caregiver=caregiver, location="lounge",
                          source="companion-model", model=model, decided_at=decided_at), time.monotonic())
        # This intention is not a new thought from the cat's own model.
        self.bubble_until = 0
        self.message = f"{actor['name']} 想照顾橘子"
        return True

    def cancel_companion_care(self):
        if self.plan and self.plan.get("source") == "companion-model":
            self.members.end_care()
            self.plan, self.target, self.stage, self.pose = None, None, "idle", "idle"
            self.message = "伙伴已暂停这次自主照护"

    def _move(self, dt):
        if not self.target:
            return False
        dx, dy = self.target[0]-self.x, self.target[1]-self.y
        distance = math.hypot(dx, dy)
        step = min(distance, dt*78)
        if abs(dx) > 1:
            self.facing = 1 if dx > 0 else -1
        if distance:
            self.x += dx/distance*step
            self.y += dy/distance*step
        if distance <= step:
            self.x, self.y = self.target
            self.target = None
        return self.target is not None

    def advance(self, companions, meeting):
        now = time.monotonic()
        elapsed = max(0., now-self.last_tick)
        dt, self.last_tick = min(.12, elapsed), now
        if self.care_started and elapsed > .5:
            self.care_started += elapsed-dt
        self.state.tick(sleeping=self.pose == "sleep", playing=self.pose == "play")
        decision = self.brain.poll()
        if decision:
            self._accept(decision, now)
        action = self.plan["action"] if self.plan else None
        if action in CARE and now-self.approved_at > 180:
            self.members.end_care()
            self.plan, self.target, self.stage, action = None, None, "idle", None
            self.message = "这次照护已过期，等下一次决定"
        care = self.members.care
        if action in CARE and (care is None or care["key"] != "companion:"+self.plan["caregiver"]
                               or care["action"] != action):
            self.stage, self.care_started = "queued", 0.
        if action in CARE and meeting:
            self.members.end_care()
            self.stage, self.care_started, self.pose = "queued", 0., "idle"
            self.message = "伙伴在开总结会，散会后继续照顾"
        else:
            moving = self._move(dt)
            self.pose = "walk" if moving else "idle"
            if action in CARE:
                key = "companion:"+self.plan["caregiver"]
                if self.stage == "queued":
                    tx, ty = self.target or (self.x, self.y)
                    if self.members.begin_care(key, tx-34, ty, action):
                        self.stage, self.message = "arriving", ""
                if self.stage == "arriving" and not moving and self.members.care_ready():
                    self.stage, self.care_started, self.facing = "caring", now, 1
                if self.stage == "caring" and not self.members.care_ready():
                    self.stage, self.care_started = "arriving", 0.
                if self.stage == "caring":
                    self.pose = {"feed": "eat", "water": "drink", "play": "play", "pet": "pet"}[action]
                    if now-self.care_started >= (5 if action == "play" else 3.5):
                        actor = self.members.actors.get(key, {})
                        cared = self.state.apply_care(action, actor.get("name", self.plan["caregiver"]))
                        self.message = "照顾好啦" if cared else "现在不需要重复照料，先歇一会儿"
                        self.state.save(force=True)
                        self.members.end_care()
                        self.plan, self.stage, self.pose = None, "idle", "idle"
            elif action == "sleep" and not moving:
                self.pose = "sleep"
            elif action == "explore" and not moving and now >= self.next_roam:
                room = self.rooms[self.plan["location"]]
                self.target = (room.center+(48 if self.x < room.center else -48), 190.)
                self.next_roam = now+22
        self.state.save()
        if now < self.next_check:
            return
        self.next_check = now+2
        need = self.state.need()
        changed = need != self.last_need and need is not None
        ready = not self.plan or self.plan["action"] not in CARE
        if companions and not meeting and ready and (self.requested_action or now >= self.next_decision or
                                                      (changed and now-self.approved_at >= 120)):
            if self.brain.request(self._context(companions, meeting)):
                self.requested_action = None
                self.message = ""
                self.last_need = need
                self.next_decision = now+600

    def draw(self):
        c, now = self.canvas, time.monotonic()
        c.delete("pet")
        state = self.state.snapshot()
        self.hits = {}
        for key, prop in self.props.items():
            empty = (key == "food" and state["fullness"] < 55) or (key == "water" and state["water"] < 50)
            image = self.assets.furniture(prop.get("empty", prop["asset"]) if empty else prop["asset"], *prop["size"])
            x, y = prop["x"], prop["y"]
            if key == "toy" and self.pose == "play":
                x += int(math.sin(now*6)*5)
            if image:
                c.create_image(x, y, image=image, anchor="s", tags="pet")
                self.hits[key] = (x-image.width()/2, y-image.height(), x+image.width()/2, y, image)
            else:
                c.create_rectangle(x-10, y-7, x+10, y, fill="#c0996a", tags="pet")
                self.hits[key] = (x-10, y-7, x+10, y, None)
        x, y = round(self.x), round(self.y)
        c.create_rectangle(x-13, y-1, x+13, y+2, fill="#65513c", outline="", tags="pet")
        pose = "eat" if self.pose == "drink" else "idle" if self.pose == "pet" else self.pose
        self.photo = self.sprites.sample(pose, self.facing, now)
        dy = -1 if self.pose in ("idle", "pet") and now % 3.6 < 1.2 else 0
        if self.photo:
            w, h = self.photo.width(), self.photo.height()
            c.create_image(x, y+dy, image=self.photo, anchor="s", tags="pet")
        else:
            w, h = 28, 25
            c.create_rectangle(x-14, y-24, x+14, y, fill="#d6a164", outline="#664938", tags="pet")
            for dx in (-10, 7):
                c.create_rectangle(x+dx, y-28, x+dx+4, y-18, fill="#d6a164", outline="", tags="pet")
        self.bounds = (x-w//2, y+dy-h, x-w//2+w, y+dy)
        if self.pose == "sleep":
            c.create_text(x+22, y-h-4-int(now*2)%3, text="z z", fill="#d6dfcd", font=("Consolas", 8), tags="pet")
        if self.pose == "pet":
            c.create_text(x+15, y-h-5-int(now*2)%3, text="♥", fill="#e3ad9b", font=("Segoe UI", 8), tags="pet")
        label = "橘子 · "+ACTIVITY[self.pose]
        self.label_bounds = (0, 0, 0, 0)
        if self.selected:
            self.label_bounds = (x-47, y-h-19, x+47, y-h-5)
            c.create_rectangle(*self.label_bounds, fill="#243936", outline="#6c8264", tags="pet")
            c.create_text(x, y-h-12, text=label, fill="#e3d2a9", font=("Microsoft YaHei UI", 7), tags="pet")
        brain = self.brain.snapshot()
        if now < self.bubble_until and brain["last_thought"]:
            thought = brain["last_thought"]
            thought = thought if len(thought) <= 18 else thought[:17]+"…"
            c.create_rectangle(x-72, y-h-42, x+72, y-h-24, fill="#efe4c8", outline="#bba781", tags="pet")
            c.create_text(x, y-h-33, text=thought, fill="#4e4b3c", font=("Microsoft YaHei UI", 8), tags="pet")

    @staticmethod
    def _inside(x, y, box):
        return box[0] <= x < box[2] and box[1] <= y < box[3]

    def hit(self, x, y):
        if self._inside(x, y, self.label_bounds):
            return "cat"
        if self._inside(x, y, self.bounds):
            if self.photo is None or not self.photo.transparency_get(int(x-self.bounds[0]), int(y-self.bounds[1])):
                return "cat"
        for key, (x1, y1, x2, y2, photo) in reversed(list(self.hits.items())):
            if self._inside(x, y, (x1, y1, x2, y2)):
                if photo is None or not photo.transparency_get(int(x-x1), int(y-y1)):
                    return key
        return None

    def activate(self, key):
        self.selected = True
        if key in self.props and self.props[key]["action"]:
            self.request_care(self.props[key]["action"])

    def describe(self, key):
        if key == "cat":
            return "橘子 · 点击查看小心思、身体状态与照料记录"
        prop = self.props.get(key, {})
        return prop.get("label", "") + (" · 点击请模型安排照顾" if prop.get("action") else " · 安静的小窝")

    def draw_panel(self, left, width):
        self.panel_hits = []
        if not self.selected:
            return
        c = self.canvas
        w = min(344, width-38)
        x, y = left+width-w-20, 30
        tag = "overlay"
        c.create_rectangle(x+3, y+3, x+w+3, 196, fill="#172125", outline="", tags=tag)
        c.create_rectangle(x, y, x+w, 193, fill="#233532", outline="#8c9a77", tags=tag)
        self.panel_bounds = (x, y, x+w, 193)
        def label(px, py, text, color="#c6cfbc", **kw):
            c.create_text(px, py, text=text, fill=color, font=("Microsoft YaHei UI", 8), tags=tag, **kw)
        def button(x1, y1, x2, y2, text, action, *, enabled=True, accent=False):
            box = (x1, y1, x2, y2)
            hovered = self.pointer and self._inside(*self.pointer, box)
            fill = "#253a33" if not enabled else "#4b6351" if accent or hovered else "#304840"
            c.create_rectangle(*box, fill=fill, outline="#637b64" if enabled else "#3d5145", tags=tag)
            label((x1+x2)/2, (y1+y2)/2, text, "#efdcac" if enabled else "#798d7e")
            if enabled:
                self.panel_hits.append((box, action))
        label(x+12, 43, "橘子的小日子" if w >= 320 else "橘子", "#efdcac", anchor="w")
        brain, state = self.brain.snapshot(), self.state.snapshot()
        button(x+w-159, 34, x+w-113, 53, "近况" if self.show_journal else "小记", "journal")
        button(x+w-108, 35, x+w-33, 52, "暂停思考" if brain["enabled"] else "开启思考", "toggle")
        button(x+w-26, 35, x+w-7, 52, "×", "close")
        status = self.status_text(brain)
        status_width = w-74 if self.requested_action else w-24
        status_limit = max(12, int(status_width/11))
        status = status if len(status) <= status_limit else status[:status_limit-1]+"…"
        label(x+12, 63, status, "#dfb98d" if brain["error"] else "#a7c4a8", anchor="w")
        if self.requested_action:
            button(x+w-52, 56, x+w-9, 71, "取消", "cancel")
        if self.show_journal:
            journal = state["journal"]
            pages = max(1, math.ceil(len(journal)/3))
            self.journal_page = min(self.journal_page, pages-1)
            for i, entry in enumerate(journal[self.journal_page*3:self.journal_page*3+3]):
                by = 78+i*26
                label(x+12, by, self._time_ago(entry["at"]), "#8aa997", anchor="w")
                label(x+12, by+13, entry["text"][:max(16, int((w-24)/11))], "#e3d8b9", anchor="w")
            if not journal:
                label(x+w/2, 109, "还没有照料小记", "#e3d8b9")
                label(x+w/2, 129, "伙伴完成照顾后，会记在这里。")
            label(x+12, 157, f"最近 {len(journal)} 次照料 · {self.journal_page+1}/{pages}", "#87a599", anchor="w")
            button(x+w-62, 149, x+w-39, 164, "‹", "previous", enabled=self.journal_page > 0)
            button(x+w-34, 149, x+w-11, 164, "›", "next", enabled=self.journal_page < pages-1)
        else:
            for i, (key, title, color) in enumerate((("fullness", "饱腹", "#d9b980"), ("water", "饮水", "#8ebecb"),
                                                   ("energy", "精力", "#aec38b"), ("affection", "亲近", "#d9a99a"))):
                bx, by = x+12+(i%2)*(w-24)/2, 80+(i//2)*19
                bar = max(28, (w-24)/2-79)
                label(bx, by, title, anchor="w")
                c.create_rectangle(bx+33, by-3, bx+33+bar, by+3, fill="#152822", outline="", tags=tag)
                c.create_rectangle(bx+33, by-3, bx+33+bar*state[key]/100, by+3, fill=color, outline="", tags=tag)
                label(bx+39+bar, by, str(round(state[key])), color, anchor="w")
            thought = brain["last_thought"] or "还没有新的心愿，等橘子想一想。"
            thought_limit = 2*max(16, int((w-24)/11))-4
            thought = thought if len(thought) <= thought_limit else thought[:thought_limit-1]+"…"
            last = brain["last_decision"]
            current_thought = self.plan and self.plan.get("source") != "companion-model"
            caption = ("此刻的小心思" if current_thought else "上次的小心思") if last else "小心思"
            if last:
                caption += " · "+self._time_ago(last["decided_at"])
            label(x+12, 115, caption, "#87a599", anchor="w")
            label(x+12, 124, "「"+thought+"」", "#eee1bb", anchor="nw", width=w-24, justify="left")
            label(x+12, 158, f"Kimi 决策 · 本小时已用 {brain.get('used_last_hour', 6-brain['calls_remaining'])}/6 次", "#87a599", anchor="w")
        bw = (w-24)/4
        available = self.live and brain["enabled"] and not brain["busy"] and not (self.plan and self.plan["action"] in CARE)
        for i, (action, title) in enumerate(CARE.items()):
            bx = x+12+i*bw
            button(bx, 169, bx+bw-5, 191, title, action, enabled=available,
                   accent=self.requested_action == action)

    def panel_click(self, x, y):
        if not self.selected or not self._inside(x, y, getattr(self, "panel_bounds", (0, 0, 0, 0))):
            return False
        for box, action in self.panel_hits:
            if self._inside(x, y, box):
                if action == "close":
                    self.selected = False
                elif action == "toggle":
                    self.toggle()
                elif action == "journal":
                    self.show_journal = not self.show_journal
                    self.journal_page = 0
                elif action == "previous":
                    self.journal_page = max(0, self.journal_page-1)
                elif action == "next":
                    self.journal_page += 1
                elif action == "cancel":
                    self.requested_action = None
                    self.message = "已取消排队的照护"
                else:
                    self.request_care(action)
                break
        return True

    def close(self):
        self.state.save(force=True)
        self.brain.close()
