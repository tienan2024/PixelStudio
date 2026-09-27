"""Room-local scene definitions, independent from account data and rendering."""
from __future__ import annotations

from dataclasses import dataclass
import json

from scene_assets import ASSETS


@dataclass
class Room:
    id: str
    label: str
    x: int
    width: int
    background: str
    objects: list[dict]
    arrival: int = 280

    @property
    def center(self):
        return self.x + self.width//2


def load_rooms():
    catalog = json.loads((ASSETS / "world.json").read_text(encoding="utf-8"))
    rooms, object_ids, offset = [], set(), 0
    for filename in catalog["rooms"]:
        data = json.loads((ASSETS / filename).read_text(encoding="utf-8"))
        for obj in data["objects"]:
            if obj["id"] in object_ids:
                raise ValueError("Duplicate scene object: " + obj["id"])
            object_ids.add(obj["id"])
        rooms.append(Room(data["id"], data["label"], offset, data["width"],
                          data["background"], data["objects"], data.get("arrival", 280)))
        offset += data["width"]
    return rooms, catalog["status_rooms"]
