"""Scroll viewport composing independent rooms, furniture and live member layers."""
from __future__ import annotations

from pathlib import Path
import tkinter as tk

from scene_actors import ActorLayer, LABELS
from scene_assets import SceneAssets
from scene_layout import load_rooms
from scene_objects import SceneObject
from scene_state import SceneState


class PixelWorld(tk.Canvas):
    # External window geometry stays fixed; the room catalog determines map width.
    WIDTH, HEIGHT = 1120, 224

    def __init__(self, parent, *, bg, on_toggle=None, on_select=None):
        self.rooms, status_rooms = load_rooms()
        self.map_width = sum(room.width for room in self.rooms)
        super().__init__(parent, width=294, height=self.HEIGHT, bg=bg, takefocus=True,
                         highlightthickness=0, bd=0, cursor="hand2",
                         scrollregion=(0, 0, self.map_width, self.HEIGHT))
        self.on_toggle, self.on_select = on_toggle, on_select
        self.view_width, self.expanded = 294, False
        self.edge_colors = bg, bg
        self.room_id, self.camera = self.rooms[0].id, self.rooms[0].center
        self.frame, self.hovered, self.selected_object = 0, None, None
        self.tabs = []
        self.images = SceneAssets(self)
        specs = [obj for room in self.rooms for obj in room.objects]
        state_path = Path(__file__).resolve().parents[1] / ".runtime/widget-world-state.json"
        self.state = SceneState(state_path, specs)
        self.objects = [SceneObject(self, room, spec, self.images, self.state)
                        for room in self.rooms for spec in room.objects]
        self.objects.sort(key=lambda obj: obj.spec.get("layer", 20))
        self.by_id = {obj.id: obj for obj in self.objects}
        self.members = ActorLayer(self, self.rooms, status_rooms)
        self._background()
        for obj in self.objects:
            obj.draw()
        self.bind("<Configure>", lambda _e: self._center_view())
        self.bind("<Button-1>", self._click)
        self.bind("<Motion>", self._motion)
        self.bind("<Leave>", lambda _e: self._hover(None))
        self.bind("<Return>", self._enter)
        self.bind("<Left>", lambda _e: self._step_room(-1))
        self.bind("<Right>", lambda _e: self._step_room(1))
        self.bind("<MouseWheel>", self._wheel)
        for i, room in enumerate(self.rooms, 1):
            self.bind(str(i), lambda _e, key=room.id: self.select_room(key))
        self._live()

    def rect(self, x, y, width, height, color, tag="architecture"):
        return self.create_rectangle(int(x), int(y), int(x+width), int(y+height),
                                     fill=color, outline="", tags=tag)

    def _background(self):
        for i, room in enumerate(self.rooms, 1):
            self.rect(room.x, 24, room.width, 176, "#293542")
            self.rect(room.x, 153, room.width, 47, "#72513b")
            image = self.images.background(room.background, room.width, 176)
            if image:
                self.create_image(room.center, 24, image=image, anchor="n", tags="architecture")
            else:
                for y in range(157, 201, 10):
                    self.rect(room.x, y, room.width, 1, "#977453")
            for x in (room.x, room.x+room.width-8):
                self.rect(x, 24, 8, 176, "#382d29")
                self.rect(x+2, 24, 2, 176, "#947149")
            self.rect(room.x+20, 32, 88, 18, "#202d35")
            self.create_text(room.x+28, 41, anchor="w", text=f"0{i}  {room.label}",
                             fill="#d3bd94", font=("Microsoft YaHei UI", 8), tags="architecture")

    def set_agents(self, threads):
        self.members.set_agents(threads)
        self._live()

    def advance(self):
        self.frame += 1
        self.state.tick()
        self.members.advance()
        self._live()

    def _live(self):
        for obj in self.objects:
            obj.animate(self.frame)
            obj.raise_layers()
        self.members.draw()
        self._highlight()
        self._overlay()

    def set_viewport(self, width, *, expanded, left_bg, right_bg):
        self.view_width, self.expanded = width, expanded
        self.edge_colors = left_bg, right_bg
        self.configure(width=width)
        self._center_view()

    def _center_view(self):
        left = max(0, min(self.camera-self.view_width/2, self.map_width-self.view_width))
        self.xview_moveto(left/self.map_width)
        self._overlay()

    def select_room(self, key):
        room = next((room for room in self.rooms if room.id == key), None)
        if room:
            self.room_id, self.camera = key, room.center
            self.hovered = self.selected_object = None
            self.members.selected = None
            self._center_view()
            self._live()
        return "break"

    def _step_room(self, delta):
        index = next(i for i, room in enumerate(self.rooms) if room.id == self.room_id)
        return self.select_room(self.rooms[(index+delta)%len(self.rooms)].id)

    def _wheel(self, event):
        if self.expanded and event.delta:
            left = self.canvasx(0) + (-96 if event.delta > 0 else 96)
            left = max(0, min(left, self.map_width-self.view_width))
            self.camera = left+self.view_width/2
            self.room_id = min(self.rooms, key=lambda room: abs(room.center-self.camera)).id
            self._hover(None)
            self._center_view()
        return "break"

    def _enter(self, _event):
        if self.expanded and self.selected_object:
            self.state.activate(self.selected_object)
            self._live()
        elif self.on_toggle:
            self.on_toggle()
        return "break"

    def _overlay(self):
        self.delete("overlay")
        self.tabs = []
        left, width = int(self.canvasx(0)), self.view_width
        self.rect(left, 0, width, 24, "#1c2b36", "overlay")
        self.rect(left, 200, width, 24, "#1c2b36", "overlay")
        self.rect(left+10, 23, width-20, 1, "#3d505c", "overlay")
        start = 154 if width >= 600 else 20
        if width >= 600:
            self.create_text(left+20, 12, text="PIXEL STUDIO", anchor="w", fill="#e2c58b",
                             font=("Consolas", 9, "bold"), tags="overlay")
        for i, room in enumerate(self.rooms):
            x = left+start+i*66
            active = room.id == self.room_id
            if active:
                self.rect(x-6, 3, 60, 18, "#3b4b4d", "overlay")
            self.create_text(x+24, 12, text=room.label, fill="#edd3a3" if active else "#8eaaa9",
                             font=("Microsoft YaHei UI", 8), tags="overlay")
            self.tabs.append((x-6, x+54, room.id))
        if width >= 700:
            count = f"{self.members.active} 运行 · {self.members.total} 成员"
            if self.members.total > len(self.members.actors):
                count += f" · 展示 {len(self.members.actors)}"
            self.create_text(left+width-22, 12, text=count, anchor="e", fill="#a5c8bc",
                             font=("Microsoft YaHei UI", 8), tags="overlay")
        target = self.hovered or self.selected_object
        actor = self.members.actors.get(self.members.selected)
        if target:
            info = self.state.describe(target)
            if self.hovered:
                info += "  ·  点击互动"
        elif actor:
            info = f"{actor['name']} · {actor['role']} · {LABELS[actor['state']]}"
        else:
            info = ("点击家具互动  ·  滚轮平移 / 1–3 切换房间  ·  两侧卷轴收起" if self.expanded else
                    f"{self.members.total} 位成员 · 点击展开画卷")
        limit = max(12, (width-38)//11)
        info = info if len(info) <= limit else info[:limit-1]+"…"
        self.create_text(left+width/2, 212, text=info, fill="#bfc8c7",
                         font=("Microsoft YaHei UI", 8), tags="overlay")
        for x in (left+2, left+width-12):
            self.rect(x, 12, 10, 200, "#382f2c", "overlay")
            self.rect(x+2, 8, 6, 208, "#a88659", "overlay")
            self.rect(x+2, 14, 2, 196, "#e3c18a", "overlay")
            self.rect(x-2, 8, 14, 4, "#d4b078", "overlay")
            self.rect(x-2, 212, 14, 4, "#d4b078", "overlay")
        for y, cut in ((0, 8), (2, 4), (4, 2)):
            for yy in (y, self.HEIGHT-y-2):
                self.rect(left, yy, cut, 2, self.edge_colors[0], "overlay")
                self.rect(left+width-cut, yy, cut, 2, self.edge_colors[1], "overlay")

    def _object_at(self, x, y):
        if self.members.hit(x, y):
            return None
        return next((obj.id for obj in reversed(self.objects) if obj.hit(x, y)), None)

    def _motion(self, event):
        if not self.expanded or not (16 < event.x < self.view_width-16 and 24 < event.y < 200):
            self._hover(None)
            return
        self._hover(self._object_at(self.canvasx(event.x), event.y))

    def _hover(self, key):
        if key != self.hovered:
            self.hovered = key
            self._highlight()
            self._overlay()

    def _highlight(self):
        self.delete("hover")
        key = self.hovered or self.selected_object
        if key in self.by_id:
            self.by_id[key].highlight()

    def _click(self, event):
        self.focus_set()
        if not self.expanded or event.x <= 16 or event.x >= self.view_width-16:
            if self.on_toggle:
                self.on_toggle()
            return
        x, y = self.canvasx(event.x), event.y
        if y <= 24:
            for x1, x2, room_id in self.tabs:
                if x1 <= x <= x2:
                    self.select_room(room_id)
                    return
        if not 24 < y < 200:
            return
        member = self.members.hit(x, y)
        if member:
            self.members.selected, self.selected_object, self.hovered = member, None, None
            if self.on_select:
                self.on_select(self.members.actors[member])
        else:
            key = self._object_at(x, y)
            self.selected_object = key
            self.members.selected = None
            if key:
                self.state.activate(key)
        self._live()
