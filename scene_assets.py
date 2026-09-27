"""Tk-only image loading. Artwork and hit masks share the same cached frames."""
from __future__ import annotations

import json
import math
from pathlib import Path
import tkinter as tk

ASSETS = Path(__file__).resolve().parent / "assets" / "rooms"


class SceneAssets:
    def __init__(self, master):
        self.master, self.cache = master, {}
        self.sheet, self.rectangles = None, {}
        try:
            self.rectangles = json.loads((ASSETS / "furniture-atlas.json").read_text(encoding="utf-8"))
            self.sheet = tk.PhotoImage(master=master, file=str(ASSETS / "furniture-atlas.png"))
        except (OSError, ValueError, tk.TclError):
            self.rectangles = {}

    def furniture(self, name, width, height):
        key = (name, width, height)
        if key not in self.cache:
            try:
                x1, y1, x2, y2 = self.rectangles[name]
                source = tk.PhotoImage(master=self.master, width=x2-x1, height=y2-y1)
                self.master.tk.call(source, "copy", self.sheet, "-from", x1, y1, x2, y2)
                factor = max(1, math.ceil(max(source.width()/width, source.height()/height)))
                self.cache[key] = source.subsample(factor)
            except (KeyError, TypeError, ValueError, tk.TclError):
                self.cache[key] = None
        return self.cache[key]

    def background(self, filename, width, height):
        key = (filename, width, height)
        if key not in self.cache:
            try:
                source = tk.PhotoImage(master=self.master, file=str(ASSETS / filename))
                # Architectural posts cover the narrow side margins. Never stretch art.
                factor = max(1, round(source.width()/width))
                sampled = source.subsample(factor)
                out = tk.PhotoImage(master=self.master, width=min(width, sampled.width()),
                                    height=min(height, sampled.height()))
                top = max(0, (sampled.height()-height)//2)
                self.master.tk.call(out, "copy", sampled, "-from", 0, top,
                                    out.width(), top+out.height())
                self.cache[key] = out
            except (OSError, tk.TclError):
                self.cache[key] = None
        return self.cache[key]
