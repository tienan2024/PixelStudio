"""Persistent companions with continuous travel, seating and conversation poses."""
import math
import time

from character_sprites import character_bank, draw_fallback, member_key

COLORS = {"active": "#9bdbba", "idle": "#9bbad6", "systemError": "#eb9c80",
          "notLoaded": "#b4becb", "unknown": "#b4becb"}
LABELS = {"active": "运行中", "idle": "待命", "systemError": "异常",
          "notLoaded": "未加载 · 状态未知", "unknown": "状态未知"}
CARE_LABELS = {"feed": "前往喂猫", "water": "前往添水", "play": "前往陪玩", "pet": "前往摸摸"}


class ActorLayer:
    def __init__(self, canvas, rooms, status_rooms):
        self.canvas, self.rooms = canvas, {r.id: r for r in rooms}
        self.status_rooms = status_rooms
        self.characters = character_bank(canvas)
        self.actors, self.total, self.active = {}, 0, 0
        self.selected = None
        self.meeting, self.discussion_started = False, 0.0
        self.care = None
        self.last_tick = time.monotonic()

    def set_companions(self, threads, *, meeting=False):
        self.characters.register(threads)
        now = time.monotonic()
        if meeting != self.meeting:
            self.discussion_started = 0.0
        if meeting:
            self.care = None
        self.meeting = meeting
        ordered = sorted(threads, key=member_key)
        self.total = len(threads)
        self.active = sum((t.get("status") or {}).get("type") == "active" for t in threads)
        actors = {}
        for index, thread in enumerate(ordered[:2]):
            key = member_key(thread)
            state = (thread.get("status") or {}).get("type", "unknown")
            state = state if state in COLORS else "unknown"
            home = self.rooms[self.status_rooms[state]]
            home_destination = (home.x+(210, 366)[index], 190)
            room = self.rooms["meeting"] if meeting else home
            caring = self.care is not None and self.care["key"] == key
            tx, ty = ((room.x+(227, 337)[index], 140) if meeting else
                      self.care["destination"] if caring else home_destination)
            actor = self.actors.get(key)
            if actor is None:
                actor = dict(x=home.x+(210, 366)[index], y=190, path=[], facing=1,
                             next_wander=now+9+index*4, wander_step=0, react_until=0.0,
                             rise_until=0.0, settle_started=0.0, care_started=0.0)
            if actor.get("destination") != (tx, ty):
                kind = ("arriving" if meeting else "pet-care" if caring else
                        "returning" if actor.get("meeting") else "relocating")
                self._route(actor, tx, ty, kind)
                actor["next_wander"] = now+10+index*4
            if not caring:
                actor["care_started"] = 0.0
            actor.update(destination=(tx, ty), home_destination=home_destination,
                         state=state, room=room.id, index=index, id=key,
                         name=thread.get("name") or "伙伴", task=thread.get("task", "状态读取中"),
                         detail=thread.get("detail", ""), state_text=thread.get("statusText", LABELS[state]),
                         phase=thread.get("phase", "unknown"), meeting=meeting)
            actors[key] = actor
        self.actors = actors
        if self.care is not None and self.care["key"] not in actors:
            self.care = None
        if self.selected not in actors:
            self.selected = None

    def begin_care(self, key, x, y, action) -> bool:
        """Overlay one companion's route; the source task/state remains authoritative."""
        if self.meeting or key not in self.actors or action not in CARE_LABELS:
            return False
        if self.care is not None:
            self.end_care()
        actor = self.actors[key]
        self.care = dict(key=key, destination=(x, y), action=action)
        actor.update(destination=(x, y), care_started=0.0)
        self._route(actor, x, y, "pet-care")
        return True

    def end_care(self) -> None:
        """Return to the latest real home, which may have changed during care."""
        care, self.care = self.care, None
        if care is None:
            return
        actor = self.actors.get(care["key"])
        if actor is not None:
            actor["care_started"] = 0.0
            if not self.meeting:
                tx, ty = actor["home_destination"]
                actor["destination"] = (tx, ty)
                self._route(actor, tx, ty, "relocating")
                actor["next_wander"] = time.monotonic()+10+actor["index"]*3

    def care_ready(self) -> bool:
        """Report arrival only; the caller owns duration and care effects."""
        if self.meeting or self.care is None:
            return False
        actor = self.actors.get(self.care["key"])
        if actor is None or actor["path"]:
            return False
        tx, ty = self.care["destination"]
        return abs(actor["x"]-tx) < .5 and abs(actor["y"]-ty) < .5

    @staticmethod
    def _route(actor, tx, ty, kind):
        actor["rise_until"] = time.monotonic()+.4 if actor.get("seated") else 0.0
        points = []
        if actor["y"] != 190:
            points.append((actor["x"], 190))
        if actor["x"] != tx:
            points.append((tx, 190))
        if ty != 190:
            points.append((tx, ty))
        actor.update(path=points, route_kind=kind, moving=bool(points), seated=False)

    @property
    def meeting_phase(self):
        if self.meeting:
            return "discussing" if self.discussion_started else "gathering"
        if any(a.get("path") and a.get("route_kind") == "returning" for a in self.actors.values()):
            return "leaving"
        return "inactive"

    def advance(self):
        now = time.monotonic()
        dt, self.last_tick = min(0.12, max(0, now-self.last_tick)), now
        for actor in self.actors.values():
            was_seated = actor.get("seated", False)
            path = actor["path"]
            caring = self.care is not None and self.care["key"] == actor["id"]
            if (not path and actor["state"] == "idle" and not actor["meeting"]
                    and not caring and now >= actor["next_wander"]):
                actor["wander_step"] += 1
                home_x, home_y = actor["destination"]
                offset = (0, 22, 0, -18)[actor["wander_step"] % 4]
                self._route(actor, home_x+offset, home_y, "roaming")
                actor["next_wander"] = now+10+actor["index"]*3
                path = actor["path"]
            if path and now >= actor["rise_until"]:
                x, y = path[0]
                dx, dy = x-actor["x"], y-actor["y"]
                distance = math.hypot(dx, dy)
                speed = 165 if abs(dx) > 1 else 55
                step = min(distance, speed*dt)
                if distance:
                    actor["x"] += dx/distance*step
                    actor["y"] += dy/distance*step
                if abs(dx) > 1:
                    actor["facing"] = 1 if dx > 0 else -1
                if distance <= step:
                    actor["x"], actor["y"] = x, y
                    path.pop(0)
            actor["moving"] = bool(path)
            actor["seated"] = actor["meeting"] and not path
            if actor["seated"] and not was_seated:
                actor["settle_started"] = now
            if actor["seated"]:
                actor["facing"] = 1 if actor["index"] == 0 else -1
            elif caring and not path:
                actor["facing"] = 1
                if not actor["care_started"]:
                    actor["care_started"] = now
        if self.meeting and self.actors and all(a["seated"] and now-a["settle_started"] >= .4
                                                for a in self.actors.values()):
            if not self.discussion_started:
                self.discussion_started = now
        beat = max(0, now-self.discussion_started)
        for actor in self.actors.values():
            actor["speaking"] = bool(self.discussion_started and actor["seated"] and
                                      int(beat/5) % 2 == actor["index"] and beat % 5 < 3.9)

    def respond(self, key):
        if key in self.actors:
            self.actors[key]["react_until"] = time.monotonic()+1.4

    def draw(self):
        c, now = self.canvas, time.monotonic()
        c.delete("actors")
        for key, a in sorted(self.actors.items(), key=lambda item: item[1]["y"]):
            x, y = round(a["x"]), round(a["y"])
            moving, seated = a.get("moving", False), a.get("seated", False)
            speaking = a.get("speaking", False)
            rising = now < a["rise_until"]
            caring = self.care is not None and self.care["key"] == key and not a["meeting"]
            caring_here = caring and not moving
            pose = "speaking" if speaking else "listening" if seated or rising else "idle"
            if seated:
                y -= round(8*max(0, 1-(now-a["settle_started"])/.4))
            elif rising:
                y -= round(8*(1-(a["rise_until"]-now)/.4))
            elif caring_here:
                y += 2
            tag = ("actors", "actor:"+key)
            a["front"] = y >= 180
            c.create_rectangle(x-9, y-1, x+9, y+2, fill="#544738", outline="", tags=tag)
            image, dx, dy = self.characters.sample(key, a["state"], moving=moving and not rising,
                                                   pose=pose, facing=a["facing"])
            if image:
                width, height = image.width(), image.height()
                c.create_image(x+dx, y+dy, image=image, anchor="s", tags=tag)
            else:
                draw_fallback(c, x+dx, y+dy, self.characters.assignments.get(key, 0), tag)
                width, height = 20, 40
            a["image"] = image
            a["bounds"] = (x+dx-width//2, y+dy-height, x+dx-width//2+width, y+dy)
            if caring_here:
                self._draw_care(x+dx, y+dy, height, a, now, tag)
            if now < a["react_until"]:
                lift = int((now*6) % 2)
                c.create_line(x+width//2+1, y-height+12, x+width//2+4, y-height+8-lift,
                              fill="#e6c58a", width=2, tags=tag)
                c.create_rectangle(x+width//2+6, y-height+4-lift, x+width//2+8, y-height+6-lift,
                                   fill="#f0d4a0", outline="", tags=tag)
            badge = "?" if a["state"] in {"unknown", "notLoaded"} else "!" if a["state"] == "systemError" else ""
            if badge and key == self.selected:
                c.create_text(x-width//2-5, y-height+5, text=badge, fill=COLORS[a["state"]],
                              font=("Consolas", 10, "bold"), tags=tag)
            if key == self.selected:
                c.create_rectangle(x-9, y+3, x+9, y+5, fill="#f3ca7d", outline="", tags=tag)
            if a["meeting"]:
                activity = ("入座中" if moving and y < 185 else "前往会议室") if moving else ("交流中" if speaking else "听取总结")
            elif caring:
                activity = CARE_LABELS[self.care["action"]] if moving else "正在照顾橘子"
            elif moving:
                activity = "散会返回" if a.get("route_kind") == "returning" else "散步" if a.get("route_kind") == "roaming" else "前往工位" if a["state"] == "active" else "移动中"
            else:
                activity = a["state_text"]
            label = a["name"] + " · " + activity
            a["label_bounds"] = (0, 0, 0, 0)
            if key == self.selected or now < a["react_until"]:
                a["label_bounds"] = (x-54, y-height-24, x+54, y-height-9)
                c.create_rectangle(*a["label_bounds"], fill="#1c2b36", outline="#475758", tags=tag)
                c.create_text(x, y-height-17, text=label[:18], fill=COLORS[a["state"]],
                              font=("Microsoft YaHei UI", 7), tags=tag)

    def _draw_care(self, x, y, height, actor, now, tag):
        """Small pixel reach and props share the actor's existing draw layer."""
        c = self.canvas
        elapsed = max(0, now-(actor["care_started"] or now))
        beat = int(elapsed/.38) % 4
        bob = (0, 1, 0, -1)[beat]
        hand_x, hand_y = x+16, y-max(15, height//2)+8+bob
        sleeve = "#8dad99" if self.characters.assignments.get(actor["id"], 0) == 0 else "#d3a37c"
        c.create_rectangle(x+5, hand_y-10, x+10, hand_y-3, fill=sleeve, outline="", tags=tag)
        c.create_rectangle(x+8, hand_y-5, hand_x, hand_y, fill=sleeve, outline="", tags=tag)
        c.create_rectangle(hand_x-1, hand_y-3, hand_x+4, hand_y+1, fill="#e9c6a7", outline="", tags=tag)
        action = self.care["action"]
        if action in {"feed", "water"}:
            color = "#dfb579" if action == "feed" else "#8fcad4"
            for index in range(3):
                drop = (beat+index*2) % 6
                px, py = hand_x+4+index*3, hand_y+3+drop*3
                c.create_rectangle(px, py, px+2, py+(2 if action == "feed" else 3),
                                   fill=color, outline="", tags=tag)
        elif action == "play":
            c.create_line(hand_x+2, hand_y-1, hand_x+13, hand_y-7,
                          fill="#d9bd8c", width=2, tags=tag)
            c.create_line(hand_x+13, hand_y-7, hand_x+15+bob, hand_y+7,
                          fill="#ead9b8", width=1, tags=tag)
            c.create_rectangle(hand_x+12+bob, hand_y+7, hand_x+18+bob, hand_y+11,
                               fill="#d89179", outline="", tags=tag)
        else:
            hx, hy = hand_x+8, hand_y-12-bob
            for ox, oy, width, height in ((0, 0, 3, 3), (5, 0, 3, 3),
                                          (0, 2, 8, 3), (2, 5, 4, 2), (3, 7, 2, 1)):
                c.create_rectangle(hx+ox, hy+oy, hx+ox+width, hy+oy+height,
                                   fill="#dca395", outline="", tags=tag)

    def raise_walkers(self):
        for key, actor in sorted(self.actors.items(), key=lambda item: item[1]["y"]):
            if actor.get("front"):
                self.canvas.tag_raise("actor:"+key)

    def participants(self):
        return [dict(x=a["x"], y=a["y"], top=a.get("bounds", (0, a["y"]-64))[1],
                     name=a["name"], seated=a.get("seated", False), speaking=a.get("speaking", False))
                for a in self.actors.values()]

    def hit(self, x, y, *, foreground_only=False):
        for key, actor in reversed(sorted(self.actors.items(), key=lambda item: item[1]["y"])):
            if foreground_only and not actor.get("front"):
                continue
            lx, ly, rx, ry = actor.get("label_bounds", (0, 0, 0, 0))
            if lx <= x < rx and ly <= y < ry:
                return key
            left, top, right, bottom = actor.get("bounds", (0, 0, 0, 0))
            if left <= x < right and top <= y < bottom:
                image = actor.get("image")
                if image is None or not image.transparency_get(int(x-left), int(y-top)):
                    return key
        return None
