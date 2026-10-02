"""Retain frequently redrawn Canvas items without changing the drawing API."""
from __future__ import annotations

from dataclasses import dataclass
import tkinter as tk


@dataclass
class _Item:
    item_id: int
    kind: str
    coordinates: tuple
    options: dict
    parked: bool = False


@dataclass
class _Layer:
    items: list
    cursor: int = 0


class SceneCanvas(tk.Canvas):
    """Cache redraw sequences started by ``delete(dynamic_tag)``.

    Call ``finish_layers()`` after a complete scene paint. It hides the unused
    tail of each redrawn layer; a later draw can reuse those items. Architecture
    and furniture sprites retain ordinary Tk creation and deletion behavior.
    Cached layers should be changed through their drawing sequence, rather than
    through direct item-coordinate or item-option mutations.
    """

    DYNAMIC_TAGS = frozenset({"actors", "pet", "overlay", "hover", "meeting_fx"})

    def __init__(self, master=None, cnf=None, **kw):
        self._scene_layers = {}
        self._scene_items = {}
        self._scene_dirty = set()
        self._scene_reorder = set()
        self._scene_defaults = {}
        self.structural_revision = 0
        self.order_revision = 0
        self.render_stats = {"created": 0, "updated": 0, "reused": 0, "hidden": 0}
        super().__init__(master, {} if cnf is None else cnf, **kw)

    @classmethod
    def _dynamic_tag(cls, tag):
        return isinstance(tag, str) and (tag in cls.DYNAMIC_TAGS or tag.startswith("effect:"))

    @staticmethod
    def _snapshot(value):
        """Keep comparisons independent of mutable option lists and Tk objects."""
        if isinstance(value, (tuple, list)):
            return tuple(SceneCanvas._snapshot(part) for part in value)
        if isinstance(value, (str, int, float, bool, bytes, type(None))):
            return value
        return str(value)

    def _options_dict(self, cnf, kw, kind=None):
        merged = tk._cnfmerge((cnf, kw))
        options = {}
        defaults = self._scene_defaults.get(kind, {})
        for key, value in merged.items():
            if value is None:
                continue
            key = key[:-1] if key.endswith("_") else key
            if key == "tag":
                key = "tags"
            elif key not in defaults and defaults:
                matches = [name for name in defaults if name.startswith(key)]
                if len(matches) == 1:
                    key = matches[0]
            options[key] = value
        return options

    def _layer_tag(self, options):
        tags = options.get("tags", ())
        if isinstance(tags, str):
            tags = self.tk.splitlist(tags)
        elif not isinstance(tags, (tuple, list)):
            tags = (str(tags),)
        return next((tag for tag in tags if self._dynamic_tag(tag)), None)

    def _new_item(self, kind, coordinates, options):
        item_id = super()._create(kind, coordinates, options)
        self.structural_revision += 1
        self.render_stats["created"] += 1
        return item_id

    def _create(self, itemType, args, kw):
        # Match Canvas._create's positional coordinates plus optional cnf dict.
        coordinates = tk._flatten(args)
        cnf = coordinates[-1]
        if isinstance(cnf, (dict, tuple)):
            coordinates, cnf = coordinates[:-1], cnf
        else:
            cnf = {}
        options = self._options_dict(cnf, kw, itemType)
        tag = self._layer_tag(options)
        if tag is None:
            return self._new_item(itemType, coordinates, options)

        layer = self._scene_layers.setdefault(tag, _Layer([]))
        self._scene_dirty.add(tag)
        index = layer.cursor
        layer.cursor += 1
        item = layer.items[index] if index < len(layer.items) else None

        if item is not None and item.kind != itemType:
            super().delete(item.item_id)
            self._scene_items.pop(item.item_id, None)
            self.structural_revision += 1
            item = None

        if item is None:
            item_id = self._new_item(itemType, coordinates, options)
            self._scene_reorder.add(tag)
            if itemType not in self._scene_defaults:
                configuration = super().itemconfigure(item_id)
                self._scene_defaults[itemType] = {
                    name: entry[3] for name, entry in configuration.items() if len(entry) == 5
                }
            # Resolve abbreviated keys against the now-known option defaults.
            options = self._options_dict(options, {}, itemType)
            item = _Item(item_id, itemType, self._snapshot(coordinates),
                         {name: self._snapshot(value) for name, value in options.items()})
            if index == len(layer.items):
                layer.items.append(item)
            else:
                layer.items[index] = item
            self._scene_items[item_id] = (tag, index)
            return item_id

        self.render_stats["reused"] += 1
        snapshot = {name: self._snapshot(value) for name, value in options.items()}
        changes = {
            name: value for name, value in options.items()
            if name not in item.options or snapshot[name] != item.options[name]
        }
        defaults = self._scene_defaults[itemType]
        for name in item.options.keys() - options.keys():
            changes[name] = defaults.get(name, "")
        if item.parked:
            state = options.get("state", defaults.get("state", ""))
            if state == "hidden":
                changes.pop("state", None)
            else:
                changes["state"] = state
                # The visible prefix may have been reordered while this tail
                # was parked; restore both its local and world layer order.
                self._scene_reorder.add(tag)

        coordinate_snapshot = self._snapshot(coordinates)
        moved = coordinate_snapshot != item.coordinates
        if moved:
            super().coords(item.item_id, *coordinates)
        if changes:
            super().itemconfigure(item.item_id, **changes)
        if moved or changes:
            self.render_stats["updated"] += 1
        item.coordinates, item.options = coordinate_snapshot, snapshot
        item.parked = False
        return item.item_id

    def delete(self, *args):
        """Restart dynamic draw sequences, or perform ordinary Tk deletion."""
        if "all" in args:
            had_items = bool(super().find_all())
            super().delete(*args)
            self._scene_layers.clear()
            self._scene_items.clear()
            self._scene_dirty.clear()
            self._scene_reorder.clear()
            self._scene_defaults.clear()
            if had_items:
                self.structural_revision += 1
            return

        ordinary = []
        for tag in args:
            if self._dynamic_tag(tag):
                self._scene_layers.setdefault(tag, _Layer([])).cursor = 0
                self._scene_dirty.add(tag)
            else:
                ordinary.append(tag)
        if not ordinary:
            return

        removed = set()
        for selector in ordinary:
            removed.update(super().find_withtag(selector))
        super().delete(*ordinary)
        for item_id in removed:
            location = self._scene_items.pop(item_id, None)
            if location is not None:
                tag, index = location
                self._scene_layers[tag].items[index] = None
        if removed:
            self.structural_revision += 1

    def finish_layers(self):
        """Park items omitted by the latest redraw of each touched layer."""
        for tag in self._scene_dirty:
            layer = self._scene_layers[tag]
            for item in layer.items[layer.cursor:]:
                if item is None or item.parked:
                    continue
                if item.options.get("state") != "hidden":
                    super().itemconfigure(item.item_id, state="hidden")
                    self.render_stats["updated"] += 1
                item.parked = True
                self.render_stats["hidden"] += 1
            if tag in self._scene_reorder:
                # Replacement items are appended by Tk. Restore draw order only
                # when the structure changed, before the caller orders layers.
                for item in layer.items[:layer.cursor]:
                    if item is not None:
                        super().tag_raise(item.item_id)
        self._scene_dirty.clear()
        if self._scene_reorder:
            self.order_revision += 1
        self._scene_reorder.clear()
