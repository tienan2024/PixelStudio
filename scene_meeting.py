"""Meeting-room effects: whiteboard reveal, speech dots, listener nods, mug steam."""
from __future__ import annotations

import math


class MeetingEffects:
    TAG = "meeting_fx"
    _PHASES = ("gathering", "discussing", "leaving")

    def __init__(self, canvas, room):
        self.canvas = canvas
        self.room = room

    def draw(self, now, phase, started_at, participants):
        c = self.canvas
        c.delete(self.TAG)
        if phase not in self._PHASES:
            return
        elapsed = max(0.0, float(now) - float(started_at or now))
        origin = self.room.x

        def rect(x, y, w, h, color):
            c.create_rectangle(round(x), round(y), round(x + w), round(y + h),
                               fill=color, outline="", tags=self.TAG)

        def arc(x0, y0, x1, y1, color):
            c.create_arc(round(x0), round(y0), round(x1), round(y1), style="arc",
                         outline=color, tags=self.TAG)

        # Subtle steam wisps over the two table mugs while the meeting runs.
        if phase in ("gathering", "discussing"):
            for mug_x in (248, 365):
                for n in range(2):
                    t = (elapsed * 0.6 + n * 0.5) % 1.0
                    wx = origin + mug_x + math.sin(elapsed * 1.4 + n * 2.1 + mug_x) * 2
                    wy = 122 - t * 10
                    color = "#d9d0ba" if n == 0 else "#c8bfa8"
                    rect(wx, wy, 1, 2, color)
                    if t < 0.5:
                        rect(wx - 1, wy - 2, 1, 1, color)

        # Abstract whiteboard strokes appear one by one while discussing.
        if phase == "discussing":
            strokes = [
                (440, 114, 30, 2, "#7a6a52"),
                (440, 121, 2, 2, "#8a9b8e"), (444, 121, 26, 1, "#a89478"),
                (440, 127, 2, 2, "#8a9b8e"), (444, 127, 21, 1, "#a89478"),
                (440, 133, 2, 2, "#8a9b8e"), (444, 133, 28, 1, "#a89478"),
                (476, 118, 10, 2, "#8fa4b5"), (476, 128, 10, 2, "#8fa4b5"),
                (486, 124, 2, 2, "#b58f63"), (486, 132, 2, 2, "#b58f63"),
            ]
            revealed = min(len(strokes), int(elapsed / 1.1))
            for sx, sy, sw, sh, color in strokes[:revealed]:
                rect(origin + sx, sy, sw, sh, color)

        # Per-participant marks; empty or malformed entries are skipped.
        for p in participants or ():
            if not isinstance(p, dict) or not p.get("seated"):
                continue
            px, top = p.get("x"), p.get("top")
            if px is None or top is None:
                continue
            if p.get("speaking"):
                bx = min(px + 11, origin + self.room.width - 17)
                by = top - 4
                rect(bx, by, 14, 8, "#f2ead6")
                rect(bx, by, 14, 1, "#8a7458")
                rect(bx, by + 7, 14, 1, "#8a7458")
                rect(bx, by, 1, 8, "#8a7458")
                rect(bx + 13, by, 1, 8, "#8a7458")
                rect(px + 8, top + 2, 3, 2, "#f2ead6")
                step = int(elapsed * 1.6) % 3
                for n in range(3):
                    if n == step:
                        rect(bx + 2 + n * 4, by + 3, 2, 2, "#6b5a44")
                    else:
                        rect(bx + 2 + n * 4, by + 3, 1, 1, "#b3a68c")
            else:
                bob = 1 if math.sin(elapsed * 2.2 + px * 0.13) > 0.6 else 0
                ay = top + 3 + bob
                arc(px - 11, ay - 3, px - 5, ay + 3, "#b9a887")
                arc(px + 5, ay - 3, px + 11, ay + 3, "#b9a887")
