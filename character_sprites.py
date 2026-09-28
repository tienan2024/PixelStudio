"""Shared generated character frames for the world and member cards."""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
import time
import tkinter as tk


def member_key(thread):
    """One identity function for the scene and the independently sorted cards."""
    if thread.get("id"):
        return str(thread["id"])
    return "|".join(str(thread.get(k) or "") for k in
                    ("parentThreadId", "agentNickname", "name", "agentRole")) or "unnamed"


def character_bank(widget):
    root = widget._root()
    if not hasattr(root, "_character_sprites"):
        root._character_sprites = CharacterSprites(root)
    return root._character_sprites


class CharacterSprites:
    def __init__(self, root):
        self.root = root
        self.assignments = {"companion:codex": 0, "companion:kimi": 1}
        self.started = time.monotonic()
        self.frames = {}
        base = Path(__file__).resolve().parent / "assets"
        try:
            spec = json.loads((base / "characters-idle.json").read_text(encoding="utf-8"))
            sheet = tk.PhotoImage(master=root, file=str(base / "characters-idle.png"))
            for size, height in (("world", 64), ("card", 38)):
                variants = []
                for row in spec["frames"]:
                    factor = max(1, math.ceil(max(rect[3]-rect[1] for rect in row) / height))
                    frames = []
                    for x1, y1, x2, y2 in row:
                        crop = tk.PhotoImage(master=root, width=x2-x1, height=y2-y1)
                        root.tk.call(crop, "copy", sheet, "-from", x1, y1, x2, y2, "-to", 0, 0)
                        frames.append(crop.subsample(factor))
                    variants.append(frames)
                self.frames[size] = variants
        except (OSError, ValueError, KeyError, TypeError, tk.TclError):
            self.frames.clear()

    def register(self, threads):
        # Assign before either view sorts by recency or state. Retain assignments
        # when a member disappears briefly from a later snapshot.
        for key in sorted({member_key(t) for t in threads}):
            if key not in self.assignments:
                counts = [sum(v == n for v in self.assignments.values()) for n in (0, 1)]
                self.assignments[key] = 0 if counts[0] <= counts[1] else 1

    def sample(self, key, state, size="world", moving=False):
        if not self.frames:
            return None, 0, 0
        if key not in self.assignments:
            self.assignments[key] = len(self.assignments) % 2
        seed = int.from_bytes(hashlib.sha256(key.encode("utf-8")).digest()[:4], "big")
        elapsed = time.monotonic()-self.started + (seed % 5000)/1000
        blink_period = 4.2 + (seed % 1800)/1000
        # One short blink per 4.2–6 seconds, not one blink per animation tick.
        if elapsed % blink_period < 0.36:
            frame = 2
        elif moving:
            frame = (0, 3)[int(elapsed/0.28) % 2]
        else:
            frame = (0, 1, 0, 3)[int(elapsed/1.12) % 4]
        breath_period = 2.8 if state == "active" else 3.6
        breathe = -1 if math.sin(elapsed * math.tau / breath_period) > 0.5 else 0
        sway = 1 if int(elapsed/2.8) % 4 == 3 else 0
        if moving:
            breathe = -(int(elapsed/0.28) % 2)
        return self.frames[size][self.assignments[key]][frame], sway, breathe


def draw_fallback(canvas, x, feet, variant, tag):
    """Small human silhouette if the optional PNG cannot be read."""
    hair = "#b7accb" if variant == 0 else "#855b47"
    coat = "#8dad99" if variant == 0 else "#d3a37c"
    for dx, dy, w, h, color in (
        (-8, -38, 16, 16, hair), (-5, -33, 10, 10, "#e9c6a7"),
        (-7, -22, 14, 14, coat), (-4, -19, 8, 8, "#ded8c2"),
        (-6, -8, 5, 8, "#293849"), (1, -8, 5, 8, "#293849"),
        (-3, -29, 2, 2, "#263443"), (2, -29, 2, 2, "#263443"),
    ):
        canvas.create_rectangle(x+dx, feet+dy, x+dx+w, feet+dy+h,
                                fill=color, outline="", tags=tag)
