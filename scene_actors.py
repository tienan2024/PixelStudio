"""Persistent Codex/Kimi companions, independent from historical subagent rows."""
from character_sprites import character_bank, draw_fallback, member_key

COLORS = {"active": "#9bdbba", "idle": "#9bbad6", "systemError": "#eb9c80",
          "notLoaded": "#b4becb", "unknown": "#b4becb"}
LABELS = {"active": "运行中", "idle": "待命", "systemError": "异常",
          "notLoaded": "未加载 · 状态未知", "unknown": "状态未知"}


class ActorLayer:
    def __init__(self, canvas, rooms, status_rooms):
        self.canvas, self.rooms = canvas, {r.id: r for r in rooms}
        self.status_rooms = status_rooms
        self.characters = character_bank(canvas)
        self.actors, self.total, self.active = {}, 0, 0
        self.selected = None

    def set_companions(self, threads, *, meeting=False):
        self.characters.register(threads)
        ordered = sorted(threads, key=lambda t: member_key(t))
        self.total = len(threads)
        self.active = sum((t.get("status") or {}).get("type") == "active" for t in threads)
        actors = {}
        for index, thread in enumerate(ordered[:2]):
            key = member_key(thread)
            state = (thread.get("status") or {}).get("type", "unknown")
            state = state if state in COLORS else "unknown"
            room = self.rooms["meeting" if meeting else self.status_rooms[state]]
            local_x = (227, 337)[index] if meeting else (210, 366)[index]
            tx, ty = room.x+local_x, 158 if meeting else 190
            actor = self.actors.get(key, {"x": tx, "y": ty})
            if actor.get("room", room.id) != room.id:
                # Enter through a room transition; do not spend a minute crossing the map.
                actor.update(x=tx-28, y=ty)
            actor.update(tx=tx, ty=ty, state=state, room=room.id,
                         name=" ".join(str(thread.get("agentNickname") or thread.get("name") or "AI 成员").split()),
                         role="常驻伙伴", task=thread.get("task", "状态读取中"),
                         detail=thread.get("detail", ""), state_text=thread.get("statusText", LABELS[state]),
                         phase=thread.get("phase", "unknown"), meeting=meeting)
            actors[key] = actor
        self.actors = actors
        if self.selected not in actors:
            self.selected = None

    def advance(self):
        for actor in self.actors.values():
            actor["moving"] = actor["x"] != actor["tx"] or actor["y"] != actor["ty"]
            for axis in ("x", "y"):
                delta = actor["t"+axis]-actor[axis]
                actor[axis] += max(-4, min(4, delta))

    def draw(self):
        c = self.canvas
        c.delete("actors")
        for key, a in sorted(self.actors.items(), key=lambda item: item[1]["y"]):
            x, y = a["x"], a["y"]
            c.create_rectangle(x-11, y-1, x+11, y+2, fill="#544738", outline="", tags="actors")
            image, dx, dy = self.characters.sample(key, a["state"], moving=a.get("moving", False))
            if image:
                c.create_image(x+dx, y+dy, image=image, anchor="s", tags="actors")
                width, height = image.width(), image.height()
            else:
                draw_fallback(c, x+dx, y+dy, self.characters.assignments.get(key, 0), "actors")
                width, height = 20, 40
            a["image"] = image
            a["bounds"] = (x+dx-width//2, y+dy-height,
                           x+dx-width//2+width, y+dy)
            badge = "?" if a["state"] in {"unknown", "notLoaded"} else "!" if a["state"] == "systemError" else ""
            if badge:
                c.create_text(x+width//2+4, y-height-1, text=badge, fill=COLORS[a["state"]],
                              font=("Consolas", 10, "bold"), tags="actors")
            if key == self.selected:
                c.create_rectangle(x-9, y+3, x+9, y+5, fill="#f3ca7d", outline="", tags="actors")
            label = a["name"] + " · " + ("总结会" if a["meeting"] else a["state_text"])
            a["label_bounds"] = (x-54, y-height-24, x+54, y-height-9)
            c.create_rectangle(x-54, y-height-24, x+54, y-height-9,
                               fill="#1c2b36", outline="#475758", tags="actors")
            c.create_text(x, y-height-17, text=label[:18], fill=COLORS[a["state"]],
                          font=("Microsoft YaHei UI", 7), tags="actors")

    def hit(self, x, y):
        for key, actor in reversed(sorted(self.actors.items(), key=lambda item: item[1]["y"])):
            lx, ly, rx, ry = actor.get("label_bounds", (0, 0, 0, 0))
            if lx <= x < rx and ly <= y < ry:
                return key
            left, top, right, bottom = actor.get("bounds", (0, 0, 0, 0))
            if left <= x < right and top <= y < bottom:
                image = actor.get("image")
                if image is None or not image.transparency_get(int(x-left), int(y-top)):
                    return key
        return None
