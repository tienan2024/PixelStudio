"""Scroll viewport composing independent rooms, furniture and live member layers."""
from __future__ import annotations

from pathlib import Path
import tkinter as tk
import time

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
        self.companions, self.last_codex = [], {}
        self.meeting_until, self.meeting_manual = 0.0, False
        self.dismissed_turn = None
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

    @property
    def meeting_active(self):
        return self.meeting_manual or time.monotonic() < self.meeting_until

    def set_companions(self, companions):
        self.companions = companions
        codex = next((item for item in companions if item["id"] == "companion:codex"), {})
        key = (codex.get("thread_id"), codex.get("turn_id"))
        previous_key = (self.last_codex.get("thread_id"), self.last_codex.get("turn_id"))
        fresh_completion = (key == previous_key and bool(key[1]) and
                            self.last_codex.get("status", {}).get("type") == "active" and
                            codex.get("status", {}).get("type") == "idle" and
                            0 <= time.time()-codex.get("ended_at", 0) < 30 and
                            codex.get("statusText") not in {"已中止", "已暂停"})
        if key != self.dismissed_turn and (codex.get("phase") == "summary" or fresh_completion):
            was_active = self.meeting_active
            self.meeting_until = time.monotonic() + 35
            if not was_active and self.expanded:
                self.select_room("meeting")
        elif (not self.meeting_manual and codex.get("phase") == "working" and
              key != previous_key and bool(key[1])):
            self.meeting_until = 0.0
        self.last_codex = codex
        self._sync_companions()
        self._live()

    def toggle_meeting(self):
        if self.meeting_active:
            self.meeting_until, self.meeting_manual = 0.0, False
            self.dismissed_turn = (self.last_codex.get("thread_id"), self.last_codex.get("turn_id"))
        else:
            self.meeting_manual = True
            self.select_room("meeting")
        self._sync_companions()
        self._live()

    def _sync_companions(self):
        was_seated = any(a.get("meeting") for a in self.members.actors.values())
        meeting = self.meeting_active
        self.members.set_companions(self.companions, meeting=meeting)
        if was_seated and not meeting and self.room_id == "meeting":
            self.select_room("studio")

    def advance(self):
        self.frame += 1
        self.state.tick()
        if not self.meeting_active and any(a.get("meeting") for a in self.members.actors.values()):
            self._sync_companions()
        self.members.advance()
        self._live()

    def _live(self):
        for obj in self.objects:
            obj.animate(self.frame)
            if obj.spec.get("layer", 20) < 40:
                obj.raise_layers()
        self.members.draw()
        for obj in self.objects:
            if obj.spec.get("layer", 20) >= 40:
                obj.raise_layers()
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
            self._activate_object(self.selected_object)
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
        tab_width = min(66, (width-start-18)//len(self.rooms))
        for i, room in enumerate(self.rooms):
            x = left+start+i*tab_width
            active = room.id == self.room_id
            if active:
                self.rect(x-6, 3, tab_width-6, 18, "#3b4b4d", "overlay")
            self.create_text(x+(tab_width-18)/2, 12, text=room.label, fill="#edd3a3" if active else "#8eaaa9",
                             font=("Microsoft YaHei UI", 8), tags="overlay")
            self.tabs.append((x-6, x+tab_width-12, room.id))
        if width >= 700:
            count = "总结会议 · Codex / Kimi" if self.meeting_active else f"{self.members.active} 忙碌 · Codex / Kimi"
            self.create_text(left+width-22, 12, text=count, anchor="e", fill="#a5c8bc",
                             font=("Microsoft YaHei UI", 8), tags="overlay")
        target = self.hovered or self.selected_object
        actor = self.members.actors.get(self.members.selected)
        if target:
            if self.by_id[target].spec.get("action") == "meeting":
                info = "点击结束总结会" if self.meeting_active else "点击召集 Codex 与 Kimi 开总结会"
            else:
                info = self.state.describe(target)
            if self.hovered:
                info += "  ·  点击互动"
        elif actor:
            info = f"{actor['name']} · {actor['state_text']} · {actor['task']}"
        else:
            info = (f"点击伙伴查看任务 · 滚轮平移 / 1–{len(self.rooms)} 切换房间 · 点击白板开会" if self.expanded else
                    "Codex / Kimi 伙伴 · 点击展开画卷")
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
        foreground = next((obj.id for obj in reversed(self.objects)
                           if obj.spec.get("layer", 20) >= 40 and obj.hit(x, y)), None)
        if foreground:
            return foreground
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
        foreground = next((obj.id for obj in reversed(self.objects)
                           if obj.spec.get("layer", 20) >= 40 and obj.hit(x, y)), None)
        member = None if foreground else self.members.hit(x, y)
        if member:
            self.members.selected, self.selected_object, self.hovered = member, None, None
            if self.on_select:
                self.on_select(self.members.actors[member])
        else:
            key = self._object_at(x, y)
            self.selected_object = key
            self.members.selected = None
            if key:
                self._activate_object(key)
        self._live()

    def _activate_object(self, key):
        if self.by_id[key].spec.get("action") == "meeting":
            self.toggle_meeting()
        else:
            self.state.activate(key)
