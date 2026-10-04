"""Scroll viewport composing independent rooms, furniture and live member layers."""
from __future__ import annotations

from pathlib import Path
import tkinter as tk
import time

from scene_canvas import SceneCanvas
from scene_actors import ActorLayer
from scene_assets import SceneAssets
from scene_layout import load_rooms
from scene_objects import SceneObject
from scene_state import SceneState
from scene_meeting import MeetingEffects
from scene_pet import PetLayer
from scene_soul import SoulLayer


class PixelWorld(SceneCanvas):
    # External window geometry stays fixed; the room catalog determines map width.
    WIDTH, HEIGHT = 1120, 224

    def __init__(self, parent, *, bg, on_toggle=None, on_select=None, pet_live=False):
        self.rooms, status_rooms = load_rooms()
        self.map_width = sum(room.width for room in self.rooms)
        super().__init__(parent, width=294, height=self.HEIGHT, bg=bg, takefocus=True,
                         highlightthickness=0, bd=0, cursor="hand2",
                         scrollregion=(-self.WIDTH, 0, self.map_width+self.WIDTH, self.HEIGHT))
        self.on_toggle, self.on_select = on_toggle, on_select
        self.view_width, self.expanded = 294, False
        self.view_offset, self.surface_width = 0, 294
        self._requested_view = (294, 0)
        self.panel_mask = None
        self.edge_colors = bg, bg
        self.room_id, self.camera = self.rooms[0].id, self.rooms[0].center
        self.frame, self.hovered, self.selected_object = -1, None, None
        self.animation_epoch = time.monotonic()
        self.camera_animation, self.return_camera_at = None, 0.0
        self.manual_camera_until, self.meeting_camera_owned = 0.0, False
        self.tabs = []
        self._render_timer, self._closed = None, False
        self._stack_signature = None
        self.images = SceneAssets(self)
        specs = [obj for room in self.rooms for obj in room.objects]
        state_path = Path(__file__).resolve().parents[1] / ".runtime/widget-world-state.json"
        self.state = SceneState(state_path, specs)
        self.objects = [SceneObject(self, room, spec, self.images, self.state)
                        for room in self.rooms for spec in room.objects]
        self.objects.sort(key=lambda obj: obj.spec.get("layer", 20))
        self.by_id = {obj.id: obj for obj in self.objects}
        self.members = ActorLayer(self, self.rooms, status_rooms)
        self.pet = PetLayer(self, self.rooms, self.images, self.members, live=pet_live)
        self.soul = SoulLayer(self, live=pet_live)
        self.pet_hover = None
        self.meeting_effects = MeetingEffects(self, next(r for r in self.rooms if r.id == "meeting"))
        self.companions, self.last_codex = [], {}
        self.meeting_until, self.meeting_started = 0.0, 0.0
        self._background()
        for obj in self.objects:
            obj.draw()
        self.bind("<Configure>", lambda _e: self._live())
        self.bind("<Button-1>", self._click)
        self.bind("<Motion>", self._motion)
        self.bind("<Leave>", self._leave)
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
        return time.monotonic() < self.meeting_until

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
        if codex.get("phase") == "summary" or fresh_completion:
            was_active = self.meeting_active
            self.meeting_until = time.monotonic() + 35
            if not was_active:
                self.meeting_started = time.monotonic()
                self.return_camera_at = 0.0
                if (self.expanded and not self.pet.selected and self.soul.selected is None
                        and time.monotonic() >= self.manual_camera_until):
                    self.select_room("meeting", animated=True, automatic=True)
                    self.meeting_camera_owned = True
        elif (codex.get("phase") == "working" and
              key != previous_key and bool(key[1])):
            self.meeting_until = 0.0
        self.last_codex = codex
        self._sync_companions()
        self._live()

    def _sync_companions(self):
        was_seated = any(a.get("meeting") for a in self.members.actors.values())
        meeting = self.meeting_active
        self.members.set_companions(self.companions, meeting=meeting)
        if was_seated and not meeting and self.room_id == "meeting" and self.meeting_camera_owned:
            self.return_camera_at = time.monotonic()+1.3

    def advance(self):
        now = time.monotonic()
        self.state.tick()
        if not self.meeting_active and any(a.get("meeting") for a in self.members.actors.values()):
            self._sync_companions()
        self.members.advance()
        self.pet.advance(self.companions, self.meeting_active)
        self.soul.advance(self.companions, self.meeting_active)
        if self.return_camera_at and now >= self.return_camera_at:
            self.return_camera_at = 0.0
            if (self.room_id == "meeting" and self.meeting_camera_owned and not self.pet.selected
                    and self.soul.selected is None):
                self.select_room("studio", animated=True, automatic=True)
            self.meeting_camera_owned = False
        if self.camera_animation:
            started, origin, target = self.camera_animation
            progress = min(1, (now-started)/.8)
            eased = progress*progress*(3-2*progress)
            self.camera = origin+(target-origin)*eased
            if progress >= 1:
                self.camera_animation = None
        self._live()

    def _live(self):
        """Coalesce geometry, input and simulation updates into one idle paint."""
        if not self._closed and self._render_timer is None:
            self._render_timer = self.after_idle(self._paint_frame)

    def _paint_frame(self):
        self._render_timer = None
        if self._closed:
            return
        # Configure has committed before painting. Do not combine new overlay
        # coordinates with a viewport that still has the previous physical width.
        actual_width = self.winfo_width() if self.winfo_ismapped() else int(self.cget("width"))
        if actual_width > 1:
            self.surface_width = actual_width
        desired_width, desired_offset = self._requested_view
        self.view_width = min(desired_width, self.surface_width)
        self.view_offset = max(0, min(desired_offset, self.surface_width-self.view_width))
        self._center_view()
        now = time.monotonic()
        prop_frame = int((now-self.animation_epoch)/.28)
        if prop_frame != self.frame:
            for obj in self.objects:
                obj.animate(prop_frame)
            self.frame = prop_frame
        self.members.draw()
        self.pet.draw()
        self.meeting_effects.draw(now, self.members.meeting_phase,
                                 self.members.discussion_started or self.meeting_started,
                                 self.members.participants())
        self._highlight()
        self._overlay()
        self.finish_layers()
        walkers = tuple(key for key, actor in sorted(self.members.actors.items(),
                        key=lambda item: item[1]["y"]) if actor.get("front"))
        signature = (self.structural_revision, self.order_revision, walkers)
        if signature != self._stack_signature:
            for obj in self.objects:
                if obj.spec.get("layer", 20) < 40:
                    obj.raise_layers()
            self.tag_raise("actors")
            for obj in self.objects:
                if obj.spec.get("layer", 20) >= 40:
                    obj.raise_layers()
            self.members.raise_walkers()
            for tag in ("pet", "meeting_fx", "hover", "overlay"):
                self.tag_raise(tag)
            self._stack_signature = signature

    def set_viewport(self, width, *, expanded, left_bg, right_bg, offset=0, surface_width=None, panel_mask=None):
        self.expanded = expanded
        self.edge_colors = left_bg, right_bg
        self._requested_view = (width, offset)
        self.panel_mask = panel_mask
        surface_width = width if surface_width is None else surface_width
        if int(self.cget("width")) != surface_width:
            self.configure(width=surface_width)
        self._live()

    def _center_view(self):
        left = max(0, min(self.camera-self.view_width/2, self.map_width-self.view_width))
        # Reveal a logical viewport inside a stable physical canvas. Padding in
        # the scroll region permits the masked area to extend beyond the map.
        origin = left-self.view_offset
        fraction = (origin+self.WIDTH)/(self.map_width+2*self.WIDTH)
        if fraction != getattr(self, "_view_fraction", None):
            self.xview_moveto(fraction)
            self._view_fraction = fraction

    def _hold_camera(self):
        self.manual_camera_until = time.monotonic()+45
        self.meeting_camera_owned = False
        self.return_camera_at = 0.0
        self.camera_animation = None

    def focus_pet(self):
        self.select_room(self.pet.room_id, animated=True)
        self.pet.selected, self.pet.show_journal = True, False
        self.focus_set()
        self._live()
        return "break"

    def dismiss_pet(self):
        if not self.pet.selected:
            return False
        self.pet.selected = False
        self.pet.pointer = self.pet_hover = None
        self._hold_camera()
        self._live()
        return True

    def dismiss_panel(self):
        if self.soul.dismiss():
            self.members.selected = None
            self._hold_camera()
            self._live()
            return True
        return self.dismiss_pet()

    def select_room(self, key, *, animated=True, automatic=False):
        room = next((room for room in self.rooms if room.id == key), None)
        if room:
            if not automatic:
                self._hold_camera()
            self.room_id = key
            if animated and self.expanded:
                origin = self.canvasx(self.view_offset)+self.view_width/2
                target = max(self.view_width/2, min(room.center, self.map_width-self.view_width/2))
                self.camera_animation = (time.monotonic(), origin, target)
            else:
                self.camera, self.camera_animation = room.center, None
            self.hovered = self.selected_object = None
            self.members.selected = None
            self.pet.selected = False
            self.soul.dismiss()
            self.pet_hover = None
            self._live()
        return "break"

    def _step_room(self, delta):
        index = next(i for i, room in enumerate(self.rooms) if room.id == self.room_id)
        return self.select_room(self.rooms[(index+delta)%len(self.rooms)].id)

    def _wheel(self, event):
        if self.expanded and event.delta:
            self._hold_camera()
            self.camera_animation = None
            left = self.canvasx(self.view_offset) + (-96 if event.delta > 0 else 96)
            left = max(0, min(left, self.map_width-self.view_width))
            self.camera = left+self.view_width/2
            self.room_id = min(self.rooms, key=lambda room: abs(room.center-self.camera)).id
            self._hover(None)
            self._live()
        return "break"

    def _enter(self, _event):
        if self.expanded and (self.pet.selected or self.soul.selected is not None):
            return "break"
        elif self.expanded and self.selected_object:
            self._activate_object(self.selected_object)
            self._live()
        elif self.on_toggle:
            self.on_toggle()
        return "break"

    def _overlay(self):
        self.delete("overlay")
        self.tabs = []
        left, width = int(self.canvasx(self.view_offset)), self.view_width
        surface_left = int(self.canvasx(0))
        for start, end in ((0, self.view_offset), (self.view_offset+width, self.surface_width)):
            self.rect(surface_left+start, 0, max(0,end-start), self.HEIGHT, self.cget("bg"), "overlay")
            if self.panel_mask:
                panel_left, panel_right, color = self.panel_mask
                overlap_left, overlap_right = max(start,panel_left), min(end,panel_right)
                self.rect(surface_left+overlap_left, 0, max(0,overlap_right-overlap_left),
                          self.HEIGHT, color, "overlay")
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
            hour = time.localtime().tm_hour
            period = "清晨" if 5 <= hour < 9 else "上午" if 9 <= hour < 12 else "午后" if 12 <= hour < 18 else "傍晚" if 18 <= hour < 21 else "夜晚"
            self.create_text(left+width-22, 12, text=f"{period} · {time.strftime('%H:%M')}",
                             anchor="e", fill="#a5c8bc", font=("Microsoft YaHei UI", 8), tags="overlay")
        target = self.hovered or self.selected_object
        actor = self.members.actors.get(self.members.selected)
        if self.pet_hover:
            info = self.pet.describe(self.pet_hover)
        elif self.pet.selected:
            info = self.pet.status_text()
        elif target:
            info = self.state.describe(target)
            if self.hovered:
                info += "  ·  点击互动"
        elif actor:
            info = f"{actor['name']} · {actor['state_text']} · {self.soul.status()}"
        else:
            info = (f"漫游工作室 · 点击物品互动 · 滚轮平移 / 1–{len(self.rooms)} 切换房间" if self.expanded else
                    "像素工作室 · 点击展开画卷")
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
        if self.expanded:
            self.pet.draw_panel(left, width)
            self.soul.draw_panel(left, width)

    def _object_at(self, x, y):
        if self.members.hit(x, y, foreground_only=True):
            return None
        foreground = next((obj.id for obj in reversed(self.objects)
                           if obj.spec.get("layer", 20) >= 40 and obj.hit(x, y)), None)
        if foreground:
            return foreground
        if self.members.hit(x, y):
            return None
        return next((obj.id for obj in reversed(self.objects) if obj.hit(x, y)), None)

    def _motion(self, event):
        self.pet.pointer = (self.canvasx(event.x), event.y) if self.expanded else None
        self.pet_hover = self.pet.hit(self.canvasx(event.x), event.y) if self.expanded else None
        local_x = event.x-self.view_offset
        if not self.expanded or not (16 < local_x < self.view_width-16 and 24 < event.y < 200):
            self._hover(None)
            return
        over_panel = self.pet.selected and self.pet._inside(self.canvasx(event.x), event.y,
                                                          getattr(self.pet, "panel_bounds", (0, 0, 0, 0)))
        over_panel = over_panel or self.soul.panel_contains(self.canvasx(event.x), event.y)
        if over_panel:
            self.pet_hover = None
        self._hover(None if self.pet_hover or over_panel else self._object_at(self.canvasx(event.x), event.y))
        self._live()

    def _hover(self, key):
        if key != self.hovered:
            self.hovered = key
            self._live()

    def _leave(self, _event):
        self.pet.pointer = self.pet_hover = None
        self._hover(None)
        self._live()

    def _highlight(self):
        self.delete("hover")
        key = self.hovered or self.selected_object
        if key in self.by_id:
            self.by_id[key].highlight()

    def _click(self, event):
        self.focus_set()
        local_x = event.x-self.view_offset
        if not 0 <= local_x <= self.view_width:
            return "break"
        if not self.expanded or local_x <= 16 or local_x >= self.view_width-16:
            if self.on_toggle:
                self.on_toggle()
            return
        x, y = self.canvasx(event.x), event.y
        self._hold_camera()
        if self.soul.panel_click(x, y):
            self._live()
            return
        if self.pet.panel_click(x, y):
            self._live()
            return
        if y <= 24:
            for x1, x2, room_id in self.tabs:
                if x1 <= x <= x2:
                    self.select_room(room_id)
                    return
        if not 24 < y < 200:
            return
        pet = self.pet.hit(x, y)
        if pet:
            self.members.selected = self.selected_object = self.hovered = None
            self.soul.dismiss()
            self.pet.activate(pet)
            self._live()
            return
        self.pet.selected = False
        foreground = next((obj.id for obj in reversed(self.objects)
                           if obj.spec.get("layer", 20) >= 40 and obj.hit(x, y)), None)
        member = self.members.hit(x, y, foreground_only=True) or (None if foreground else self.members.hit(x, y))
        if member:
            self.members.selected, self.selected_object, self.hovered = member, None, None
            self.soul.open(member)
            if self.on_select:
                self.on_select(self.members.actors[member])
        else:
            key = self._object_at(x, y)
            self.selected_object = key
            self.members.selected = None
            self.soul.dismiss()
            if key:
                self._activate_object(key)
        self._live()

    def _activate_object(self, key):
        self.state.activate(key)
        self.frame = -1

    def close(self):
        self._closed = True
        if self._render_timer is not None:
            self.after_cancel(self._render_timer)
            self._render_timer = None
        self.soul.close()
        self.pet.close()
