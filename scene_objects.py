"""Independent room props: one sprite, hit region, state and effect per object."""
from __future__ import annotations

import math


class SceneObject:
    def __init__(self, canvas, room, spec, assets, state):
        self.canvas, self.room, self.spec, self.state = canvas, room, spec, state
        self.id = spec["id"]
        self.x, self.y = room.x + spec["x"], spec["y"]
        self.image = assets.furniture(spec["asset"], *spec["size"])
        self.tag = "object:" + self.id
        self.effects = "effect:" + self.id
        self.width = self.image.width() if self.image else min(40, spec["size"][0])
        self.height = self.image.height() if self.image else min(36, spec["size"][1])
        self.bounds = (self.x-self.width//2, self.y-self.height,
                       self.x-self.width//2+self.width, self.y)

    def draw(self):
        self.canvas.delete(self.tag)
        if self.image:
            self.canvas.create_image(self.x, self.y, image=self.image, anchor="s", tags=self.tag)
        else:
            self.canvas.create_rectangle(*self.bounds, fill="#6e5842", outline="#c3a16b", tags=self.tag)
            self.canvas.create_text(self.x, self.y-self.height//2, text=self.spec["label"],
                                    fill="#ebd9b1", font=("Microsoft YaHei UI", 7), tags=self.tag)

    def hit(self, x, y):
        left, top, right, bottom = self.bounds
        if not (left <= x < right and top <= y < bottom):
            return False
        if self.image:
            return not self.image.transparency_get(int(x-left), int(y-top))
        return True

    def animate(self, frame):
        c, tag = self.canvas, self.effects
        c.delete(tag)
        left, top, right, bottom = self.bounds
        w, h = self.width, self.height
        state = self.state.index(self.id)
        effect = self.spec.get("effect", "books" if self.spec["asset"] == "bookcase" else "")

        def rect(x, y, width, height, color, **kw):
            return c.create_rectangle(round(x), round(y), round(x+width), round(y+height),
                                      fill=color, outline="", tags=tag, **kw)

        def text(x, y, value, color="#e6c894", size=9):
            c.create_text(round(x), round(y), text=value, fill=color,
                          font=("Consolas", size, "bold"), tags=tag)

        if effect == "monitor":
            sx, sy, sw, sh = left+w*.065, top+h*.07, w*.87, h*.58
            rect(sx, sy, sw, sh, "#132331" if state else "#101719")
            if state == 1:
                for n in range(4):
                    rect(sx+4, sy+4+n*5, min(sw-8, 9+(frame+n*7)%24), 1,
                         ("#8ec4af", "#d7b77e", "#97bace")[n%3])
            elif state == 2:
                for n in range(8):
                    rect(sx+3+(n*17)%max(1,int(sw-6)), sy+3+(n*11)%max(1,int(sh-6)),
                         1, 1, "#d7c992" if (frame+n)%6 else "#8faec8")
        elif effect == "server":
            for n in range(5):
                rect(left+w*.27, top+8+n*h*.12, 2, 1,
                     "#9bdaa6" if state and (frame+n)%4 else "#31403c")
        elif effect == "coffee" and state:
            for n in range(3):
                rise = (frame*2+n*5)%18
                rect(self.x+math.sin((frame+n)/2)*2, top+10-rise, 2, 3, "#c6c3ad")
            rect(self.x, bottom-h*.27, 1, 4, "#b58954")
        elif effect == "water" and state:
            for n in range(5):
                drop = (frame*3+n*7)%max(1,h-10)
                rect(self.x-10+n*5, top+drop, 1, 3, "#92c9d0")
        elif effect == "aquarium":
            for n in range(3):
                rise = (frame+n*9)%max(1, int(h*.38))
                rect(left+w*.76+n*3, top+h*.44-rise, 1, 1, "#a7d6cf")
            if state:
                for n in range(7):
                    rect(left+8+(n*11)%max(1,w-16), top+8+(frame+n*3)%13,
                         1, 1, "#e9bd76")
        elif effect == "arcade":
            sx, sy, sw, sh = left+w*.19, top+h*.21, w*.64, h*.25
            rect(sx, sy, sw, sh, "#112534" if state else "#0e151c")
            if state:
                for n in range(6):
                    rect(sx+2+(n*7)%max(1,int(sw-4)), sy+2+(frame+n*5)%max(1,int(sh-4)),
                         1, 2, "#ceb474")
        elif effect == "lamp":
            if state:
                rect(self.x-2, top+h*.24, 4, 6, "#fff0bf")
                rect(self.x-6, bottom+1, 12, 1, "#d6b571")
            else:
                c.create_polygon(left+w*.24, top+h*.07, right-w*.24, top+h*.07,
                                 right-w*.07, top+h*.56, left+w*.07, top+h*.56,
                                 fill="#4f5359", outline="#777565", tags=tag)
        elif effect == "wardrobe" and state:
            rect(left+w*.15, top+h*.13, w*.7, h*.72, "#262733")
            rect(left+w*.17, top+h*.22, w*.65, 2, "#ad8e63")
            for n, color in enumerate(("#9aa9b2", "#b7b099", "#698b79", "#9d817d")):
                rect(left+w*.23+n*w*.135, top+h*.27, w*.1, h*.42, color)
            rect(left+w*.14, top+h*.13, 3, h*.72, "#b58e5c")
            rect(right-w*.17, top+h*.13, 3, h*.72, "#b58e5c")
        elif effect == "bed" and state:
            for n in range(3):
                text(left+w*.3+n*10, top+15-(frame+n*4)%19, "z", "#b6cce0", 8+n)
        elif effect == "cat":
            # Ambient breathing stays separate from an optional petting pulse.
            rect(self.x-3, bottom+1, 5+(frame//3)%2, 1, "#89673f")
            if state:
                text(self.x+12, top-4-(frame%5), "~", "#e8bd82")
        elif effect == "sofa" and state:
            text(self.x-13, top-2-(frame%4), "~", "#b0c7ac")
            text(self.x+13, top-5-(frame%4), "~", "#b0c7ac")
        elif effect == "books":
            rect(left+w*.13+(state%3)*w*.23, top+h*.26, w*.14, 2, "#e1bb77")

    def raise_layers(self):
        self.canvas.tag_raise(self.tag)
        self.canvas.tag_raise(self.effects)

    def highlight(self):
        left, top, right, bottom = self.bounds
        c, color = self.canvas, "#e6c58a"
        for x, direction in ((left-2, 1), (right+2, -1)):
            for y, vertical in ((top-2, 1), (bottom+2, -1)):
                c.create_line(x, y+vertical*4, x, y, x+direction*4, y,
                              fill=color, tags="hover")
