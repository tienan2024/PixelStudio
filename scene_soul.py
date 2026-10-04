"""Model-backed companion intentions, real scene experiences and personal panels."""
from pathlib import Path
import time

from soul_brain import SoulBrain
from soul_state import SoulState

MOODS = {"steady": "平静", "curious": "好奇", "warm": "温柔", "tired": "有点累",
         "focused": "专注", "cheerful": "开心"}
ACTIVITIES = {"stay": "安静待着", "read": "读几页书", "coffee": "喝杯咖啡",
              "water_plant": "照顾绿植", "rest": "歇一会儿", "chat": "聊聊天",
              "care_cat": "照顾橘子", "stretch": "伸个懒腰"}


class SoulLayer:
    def __init__(self, world, *, live=False):
        self.world, self.members, self.pet = world, world.members, world.pet
        runtime = Path(__file__).resolve().parents[1] / ".runtime"
        self.state = SoulState(runtime / "widget-soul-state.json" if live else None)
        self.brain = SoulBrain(runtime / "widget-soul-brain.json" if live else None)
        self.live, self.selected, self.page = live, None, "heart"
        self.memory_page = 0
        self.pending, self.jobs, self.hits = {}, {}, []
        self.greeting, self.message = None, ""
        self.next_decision, self.next_check = time.monotonic()+3, 0.
        self.last_tick, self.meeting_seen = time.monotonic(), False
        care = self.pet.state.snapshot()["last_care"]
        self.last_care_at = care["at"] if care else 0

    def open(self, key):
        self.selected, self.page = key.removeprefix("companion:"), "heart"
        self.memory_page = 0
        self.pet.selected = False

    def dismiss(self):
        if self.selected is None:
            return False
        self.selected = None
        return True

    def greet(self):
        if not self.live:
            self.message = "场景预览 · 未连接模型"
            return
        if not self.brain.snapshot()["enabled"]:
            self.message = "先开启伙伴思考，再打招呼。"
            return
        self.greeting = self.selected
        self.next_check = 0
        self.message = "收到招呼了，闲下来再想一想。"

    def toggle(self):
        if not self.live:
            self.message = "场景预览 · 未连接模型"
            return
        enabled = not self.brain.snapshot()["enabled"]
        self.brain.set_enabled(enabled)
        if not enabled:
            self._cancel()
            self.message = "已暂停生活决定和未完成的自主活动。"
        else:
            self.next_decision, self.next_check, self.message = time.monotonic(), 0, "伙伴日常已开启。"

    def _cancel(self):
        for key in ("codex", "kimi"):
            actor_id = "companion:"+key
            self.members.end_life(actor_id)
            if actor_id in self.members.actors:
                self.members.actors[actor_id]["dialogue_until"] = 0
        self.pending.clear()
        self.jobs.clear()
        self.greeting = None
        self.pet.cancel_companion_care()

    def _context(self, companions, meeting):
        hour = time.localtime().tm_hour
        period = "清晨" if 5 <= hour < 9 else "上午" if 9 <= hour < 12 else "午后" if 12 <= hour < 18 else "傍晚" if 18 <= hour < 21 else "夜晚"
        observed = {p["id"].removeprefix("companion:"): p.get("status", {}).get("type", "unknown")
                    for p in companions}
        characters = [dict(self.state.snapshot(key), id=key, observed_state=observed.get(key, "unknown"))
                      for key in ("codex", "kimi")]
        cat = self.pet.state.snapshot()
        cat = {key: cat[key] for key in ("fullness", "water", "energy", "affection")}
        cat.update(need=self.pet.state.need(), care_pending=bool(self.pet.requested_action or
                   self.pet.plan and self.pet.plan["action"] in {"feed", "water", "play", "pet"}),
                   paused=not self.pet.brain.snapshot()["enabled"])
        return dict(period=period, greeting=self.greeting, meeting=meeting, characters=characters, cat=cat)

    def _observe(self, meeting):
        care = self.pet.state.snapshot()["last_care"]
        if care and care["at"] > self.last_care_at:
            self.last_care_at = care["at"]
            for key in ("codex", "kimi"):
                actor = self.members.actors.get("companion:"+key, {})
                if care["actor"] == actor.get("name"):
                    self.state.complete(key, "care_cat", care["text"])
                    self.jobs.pop(key, None)
        seated = meeting and self.members.meeting_phase == "discussing"
        if seated and not self.meeting_seen:
            for key in ("codex", "kimi"):
                self.state.remember(key, "和伙伴一起坐在会议室，听取任务总结。")
            self.meeting_seen = True
        if not meeting:
            self.meeting_seen = False

    def _ready(self, key):
        actor_id = "companion:"+key
        actor = self.members.actors.get(actor_id)
        return bool(actor and actor["state"] == "idle" and not self.members.meeting
                    and not (self.members.care and self.members.care["key"] == actor_id))

    def _destination(self, key, action):
        props = {"read": "studio-books" if key == "codex" else "lounge-books",
                 "coffee": "coffee-machine", "water_plant": "studio-plant-left" if key == "codex" else "lounge-plant",
                 "rest": "lounge-sofa"}
        prop = self.world.by_id.get(props.get(action))
        if prop:
            offset = 31 if action in {"read", "coffee"} else -26 if action == "water_plant" else (-30 if key == "codex" else 30)
            return prop.x+offset, 174 if action == "rest" else 190, prop.room.id, prop.id
        if action == "stretch":
            actor = self.members.actors["companion:"+key]
            return actor["x"], 190, actor["room"], None
        return None

    def _start(self, key, entry):
        if not self._ready(key):
            return False
        action, actor_id = entry["action"], "companion:"+key
        if action == "stay":
            if entry["say"]:
                actor = self.members.actors[actor_id]
                if not self.members.begin_life(actor_id, actor["x"], actor["y"], actor["room"], "stay"):
                    return False
                self.jobs[key] = dict(entry=entry, kind="life", prop=None, elapsed=0., arrived=False)
            return True
        if action == "care_cat":
            accepted = self.pet.offer_care(key, entry["care"], model=entry["model"], decided_at=entry["decided_at"])
            if accepted:
                self.jobs[key] = dict(entry=entry, kind="care", elapsed=0.)
            return accepted
        destination = self._destination(key, action)
        if not destination:
            return True
        x, y, room, prop = destination
        if self.members.begin_life(actor_id, x, y, room, action):
            self.jobs[key] = dict(entry=entry, kind="life", prop=prop, elapsed=0., arrived=False)
            return True
        return False

    def _chat(self, dt):
        pair = [self.jobs.get(key) for key in ("codex", "kimi")]
        if not all(job and job["entry"]["action"] == "chat" for job in pair):
            return
        if not all(self.members.life_ready("companion:"+key) for key in ("codex", "kimi")):
            for job in pair:
                job["elapsed"] = 0.
            return
        elapsed = pair[0]["elapsed"]+dt
        for job in pair:
            job["elapsed"] = elapsed
        for index, key in enumerate(("codex", "kimi")):
            job = pair[index]
            if elapsed >= index*6 and not job.get("said"):
                self.members.say("companion:"+key, job["entry"]["say"], duration=5.5)
                job["said"] = True
        if elapsed >= 13:
            for key, job in zip(("codex", "kimi"), pair):
                other = "Kimi" if key == "codex" else "Codex"
                self.state.complete(key, "chat", "和"+other+"在休闲厅聊了聊："+job["entry"]["say"])
                self.members.end_life("companion:"+key)
                self.jobs.pop(key, None)

    def advance(self, companions, meeting):
        now = time.monotonic()
        dt, self.last_tick = min(.12, max(0, now-self.last_tick)), now
        observed = {p["id"].removeprefix("companion:"): p.get("status", {}).get("type", "unknown") for p in companions}
        self.state.tick(observed)
        self._observe(meeting)
        decision = self.brain.poll()
        enabled = self.brain.snapshot()["enabled"]
        if not enabled and (self.pending or self.jobs):
            self._cancel()
        if decision and enabled:
            self.message = ""
            for entry in decision["companions"]:
                key = entry["id"]
                self.state.apply_thought(key, entry, decision["decided_at"])
                if entry["action"] != "stay" or entry["say"]:
                    self.pending[key] = dict(entry, expires=now+180, model=decision["model"], decided_at=decision["decided_at"])
        for key, entry in list(self.pending.items()):
            if now >= entry["expires"]:
                self.pending.pop(key, None)
                self.message = "这次安排没赶上，等下次再想一想。"
        for key, job in list(self.jobs.items()):
            actor_id = "companion:"+key
            if job["kind"] == "care":
                if not self.pet.plan or self.pet.plan.get("caregiver") != key:
                    self.jobs.pop(key, None)
                continue
            if actor_id not in self.members.life:
                self.jobs.pop(key, None)
                # An interrupted conversation cannot leave the other person talking alone.
                if job["entry"]["action"] == "chat":
                    for other in ("codex", "kimi"):
                        self.members.end_life("companion:"+other)
                        actor = self.members.actors.get("companion:"+other)
                        if actor:
                            actor["dialogue_until"] = 0
                        self.jobs.pop(other, None)
                continue
            if job["entry"]["action"] == "chat":
                continue
            if not self.members.life_ready(actor_id):
                job["elapsed"] = 0.
                continue
            if not job["arrived"]:
                job["arrived"] = True
                if job["prop"]:
                    self.world._activate_object(job["prop"])
                if job["entry"]["say"]:
                    self.members.say(actor_id, job["entry"]["say"])
            job["elapsed"] += dt
            action = job["entry"]["action"]
            if job["elapsed"] >= {"stay": 6, "read": 12, "coffee": 7, "water_plant": 5, "rest": 20, "stretch": 6}[action]:
                if action == "stay":
                    self.state.remember(key, "说了一句话："+job["entry"]["say"])
                else:
                    event = {"read": "在书架旁翻了几页书。", "coffee": "在休闲厅喝了一杯咖啡。",
                             "water_plant": "给绿植浇了水。", "rest": "在沙发上歇了一会儿。",
                             "stretch": "站起来伸了个懒腰。"}[action]
                    self.state.complete(key, action, event)
                self.members.end_life(actor_id)
                self.jobs.pop(key, None)
        self._chat(dt)
        if not meeting:
            pair = [self.pending.get(key) for key in ("codex", "kimi")]
            if all(entry and entry["action"] == "chat" for entry in pair) and all(self._ready(k) for k in ("codex", "kimi")):
                room = self.members.rooms["lounge"]
                started = []
                for index, key in enumerate(("codex", "kimi")):
                    if self.members.begin_life("companion:"+key, room.center+(-42 if index == 0 else 42), 190, room.id, "chat"):
                        self.jobs[key] = dict(entry=self.pending[key], kind="life", prop=None, elapsed=0., arrived=False)
                        started.append(key)
                if len(started) == 2:
                    for key in started:
                        self.pending.pop(key, None)
                else:
                    for key in started:
                        self.members.end_life("companion:"+key)
                        self.jobs.pop(key, None)
            for key, entry in list(self.pending.items()):
                if entry["action"] != "chat" and key not in self.jobs and self._start(key, entry):
                    self.pending.pop(key, None)
        self.state.save()
        if now < self.next_check:
            return
        self.next_check = now+2
        ready = companions and any(value == "idle" for value in observed.values())
        greeting_ready = self.greeting is not None and self._ready(self.greeting)
        if (ready and not meeting and not self.pending and not self.jobs and not self.pet.brain.snapshot()["busy"]
                and (greeting_ready or now >= self.next_decision)):
            context = self._context(companions, meeting)
            if not greeting_ready:
                context["greeting"] = None
            if self.brain.request(context):
                if greeting_ready:
                    self.greeting, self.message = None, ""
                elif self.greeting is None:
                    self.message = ""
                self.next_decision = now+900

    def status(self):
        brain = self.brain.snapshot()
        if self.state.error:
            return self.state.error
        if brain["error"]:
            return brain["error"]
        if self.message:
            return self.message
        if self.selected in self.jobs:
            return "正在"+ACTIVITIES[self.jobs[self.selected]["entry"]["action"]]
        if self.selected in self.pending:
            return "想去"+ACTIVITIES[self.pending[self.selected]["action"]]+" · 等闲下来"
        if brain["busy"] or not brain["enabled"]:
            return brain["status"]
        if brain["retry_after_seconds"]:
            return "下次思考约 "+str(max(1, (brain["retry_after_seconds"]+59)//60))+" 分钟后"
        return "日常慢慢发生，空闲时再想一想。"

    @staticmethod
    def inside(x, y, box):
        return box[0] <= x <= box[2] and box[1] <= y <= box[3]

    def panel_contains(self, x, y):
        return self.selected is not None and self.inside(x, y, getattr(self, "panel_bounds", (0, 0, 0, 0)))

    def draw_panel(self, left, width):
        self.hits = []
        if self.selected not in {"codex", "kimi"}:
            return
        c, key = self.world, self.selected
        data, brain = self.state.snapshot(key), self.brain.snapshot()
        w, x = min(390, width-38), left+width-min(390, width-38)-20
        self.panel_bounds = (x, 30, x+w, 194)
        c.create_rectangle(x+3, 33, x+w+3, 197, fill="#152125", outline="", tags="overlay")
        c.create_rectangle(*self.panel_bounds, fill="#283632", outline="#86947a", tags="overlay")
        def text(px, py, value, color="#c7d0ba", **kw):
            c.create_text(px, py, text=value, fill=color, font=("Microsoft YaHei UI", 8), tags="overlay", **kw)
        def button(x1, y1, x2, y2, label, action, enabled=True):
            box = (x1, y1, x2, y2)
            c.create_rectangle(*box, fill="#3b5145" if enabled else "#2b3933", outline="#667959", tags="overlay")
            text((x1+x2)/2, (y1+y2)/2, label, "#e9d8af" if enabled else "#788679")
            if enabled:
                self.hits.append((box, action))
        name = "Codex" if key == "codex" else "Kimi"
        text(x+12, 44, name+"的小世界", "#eed9a6", anchor="w")
        button(x+w-143, 35, x+w-97, 53, "心情" if self.page == "memories" else "小记", "page")
        button(x+w-91, 35, x+w-30, 53, "暂停日常" if brain["enabled"] else "开启日常", "toggle")
        button(x+w-24, 35, x+w-7, 53, "×", "close")
        status = self.status()
        text(x+12, 65, status[:max(15, int((w-24)/11))], "#d4b48b" if brain["error"] else "#a9c7ae", anchor="w")
        if self.page == "memories":
            pages = max(1, (len(data["memories"])+1)//2)
            self.memory_page = min(self.memory_page, pages-1)
            records = data["memories"][self.memory_page*2:self.memory_page*2+2]
            for index, event in enumerate(records):
                when = time.strftime("%m/%d %H:%M", time.localtime(event["at"]))
                text(x+12, 81+index*36, when, "#8ea693", anchor="w")
                text(x+12, 90+index*36, event["event"][:2*max(17, int((w-24)/11))-4],
                     anchor="nw", width=w-24)
            if not records:
                text(x+w/2, 112, "一起生活之后，经历才会留在这里。")
            favorites = sorted(data["preferences"].items(), key=lambda item: item[1], reverse=True)
            favorite = next((ACTIVITIES[action] for action, count in favorites if count), "还在慢慢形成")
            text(x+12, 158, ("常做："+favorite)[:max(10, int((w-94)/11))], "#8ea693", anchor="w")
            text(x+w-88, 158, f"{self.memory_page+1}/{pages}", "#8ea693")
            button(x+w-68, 150, x+w-43, 164, "‹", "previous", self.memory_page > 0)
            button(x+w-37, 150, x+w-12, 164, "›", "next", self.memory_page < pages-1)
        else:
            text(x+12, 84, data["profile"]["temperament"]+" · "+MOODS[data["mood"]], anchor="w")
            for index, (need, label, color) in enumerate((("energy", "精力", "#abc18d"), ("social", "陪伴", "#d3ac9b"), ("curiosity", "好奇", "#8ebbc5"))):
                bx, by, cell = x+12+index*(w-24)/3, 103, (w-24)/3
                text(bx, by, label, anchor="w")
                bar = max(12, cell-51)
                c.create_rectangle(bx+29, by-3, bx+29+bar, by+3, fill="#15251f", outline="", tags="overlay")
                c.create_rectangle(bx+29, by-3, bx+29+bar*data[need]/100, by+3, fill=color, outline="", tags="overlay")
                text(bx+32+bar, by, str(round(data[need])), color, anchor="w")
            thought = data["thought"] or "还没有模型生成的心思，等闲下来想一想。"
            thought = thought[:2*max(17, int((w-24)/11))-2]
            ago = " · "+str(max(0, int((time.time()-data["last_thought_at"])/60)))+" 分钟前" if data["last_thought_at"] else ""
            text(x+12, 121, "上次的小心思"+ago, "#8da493", anchor="w")
            text(x+12, 130, "「"+thought+"」", "#efdfb9", anchor="nw", width=w-24, justify="left")
        text(x+12, 181, f"Kimi 生活决策 {brain['used_last_hour']}/4", "#90a998", anchor="w")
        button(x+w-84, 169, x+w-12, 190, "打个招呼", "greet", self.live and brain["enabled"] and not brain["busy"])

    def panel_click(self, x, y):
        if not self.panel_contains(x, y):
            return False
        for box, action in self.hits:
            if self.inside(x, y, box):
                if action == "close":
                    self.dismiss()
                    self.members.selected = None
                elif action == "page":
                    self.page = "heart" if self.page == "memories" else "memories"
                    self.memory_page = 0
                elif action == "previous":
                    self.memory_page = max(0, self.memory_page-1)
                elif action == "next":
                    self.memory_page += 1
                elif action == "toggle":
                    self.toggle()
                elif action == "greet":
                    self.greet()
                break
        return True

    def close(self):
        self._cancel()
        self.brain.close()
        self.state.close()
