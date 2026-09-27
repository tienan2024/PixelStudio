"""Real member identities and movement, independent of furniture state."""
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

    def set_agents(self, threads):
        self.characters.register(threads)
        ordered = sorted(threads, key=lambda t: ((t.get("status") or {}).get("type") != "active", member_key(t)))
        self.total = len(threads)
        self.active = sum((t.get("status") or {}).get("type") == "active" for t in threads)
        actors, occupied = {}, {key: [] for key in self.rooms}
        for thread in ordered[:12]:
            key = member_key(thread)
            state = (thread.get("status") or {}).get("type", "unknown")
            state = state if state in COLORS else "unknown"
            room = self.rooms[self.status_rooms[state]]
            candidates = sorted(range(40, room.width-30, 38), key=lambda x: abs(x-room.arrival))
            local_x = next((x for x in candidates if x not in occupied[room.id]), room.arrival)
            occupied[room.id].append(local_x)
            tx, ty = room.x+local_x, 190
            actor = self.actors.get(key, {"x": tx, "y": ty})
            actor.update(tx=tx, ty=ty, state=state, room=room.id,
                         name=" ".join(str(thread.get("agentNickname") or thread.get("name") or "AI 成员").split()),
                         role=str(thread.get("agentRole") or "子代理"))
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

    def hit(self, x, y):
        for key, actor in reversed(sorted(self.actors.items(), key=lambda item: item[1]["y"])):
            left, top, right, bottom = actor.get("bounds", (0, 0, 0, 0))
            if left <= x < right and top <= y < bottom:
                image = actor.get("image")
                if image is None or not image.transparency_get(int(x-left), int(y-top)):
                    return key
        return None
