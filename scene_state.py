"""PixelStudio 场景物品独立状态管理（仅标准库）。"""

import json
import math
import os
import tempfile
import time
from pathlib import Path


class SceneState:
    """管理可交互物品的本地持久化状态。

    交互类型：toggle / cycle 会保存持久状态；pulse 只改内存中的瞬时
    状态，到期由 tick() 收回；inspect 只读。
    所有计时使用 time.monotonic。
    """

    def __init__(self, path: Path, objects: list[dict]):
        self._path = Path(path)
        self._objects = {obj["id"]: obj for obj in objects if "id" in obj}
        self._indexes = {}
        self._pulses = {}  # object_id -> monotonic 截止时间
        for object_id, obj in self._objects.items():
            self._indexes[object_id] = self._valid_default(obj)
        self._load()

    @staticmethod
    def _valid_default(obj):
        states = obj.get("states") or []
        default = obj.get("default", 0)
        if isinstance(default, bool) or not isinstance(default, int):
            return 0
        return default if 0 <= default < len(states) else 0

    def _load(self):
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
            if not isinstance(data, dict) or data.get("version") != 1:
                return
            saved = data.get("objects", {})
            if not isinstance(saved, dict):
                return
        except (OSError, ValueError):
            return
        for object_id, index in saved.items():
            obj = self._objects.get(object_id)
            if obj is None or obj.get("interaction") not in ("toggle", "cycle"):
                continue
            if isinstance(index, bool) or not isinstance(index, int):
                continue
            if 0 <= index < len(obj.get("states") or []):
                self._indexes[object_id] = index

    def _save(self):
        data = {
            "version": 1,
            "objects": {
                object_id: self._indexes[object_id]
                for object_id, obj in self._objects.items()
                if obj.get("interaction") in ("toggle", "cycle")
            },
        }
        tmp_name = None
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp_name = tempfile.mkstemp(
                dir=str(self._path.parent), prefix=self._path.name + ".", suffix=".tmp"
            )
            with os.fdopen(fd, "w", encoding="utf-8") as fp:
                json.dump(data, fp, ensure_ascii=False)
            os.replace(tmp_name, str(self._path))
            tmp_name = None
        except OSError:
            pass
        finally:
            if tmp_name is not None:
                try:
                    os.unlink(tmp_name)
                except OSError:
                    pass

    def index(self, object_id) -> int:
        return self._indexes.get(object_id, -1)

    def activate(self, object_id) -> str:
        obj = self._objects.get(object_id)
        if obj is None:
            return "未知物品"
        states = obj.get("states") or [""]
        kind = obj.get("interaction", "inspect")
        current = self._indexes[object_id]
        if kind == "toggle":
            default = self._valid_default(obj)
            self._indexes[object_id] = (
                (default + 1) % len(states) if current == default else default
            )
            self._save()
        elif kind == "cycle":
            self._indexes[object_id] = (current + 1) % len(states)
            self._save()
        elif kind == "pulse" and len(states) > 1:
            duration = obj.get("duration", 5)
            if (not isinstance(duration, (int, float)) or isinstance(duration, bool)
                    or not math.isfinite(duration)):
                duration = 5
            self._indexes[object_id] = 1
            self._pulses[object_id] = time.monotonic() + max(0.0, duration)
        elif kind == "inspect":
            pass
        return self.describe(object_id)

    def tick(self) -> bool:
        changed = False
        now = time.monotonic()
        for object_id in list(self._pulses):
            if now >= self._pulses[object_id]:
                del self._pulses[object_id]
                obj = self._objects[object_id]
                self._indexes[object_id] = self._valid_default(obj)
                changed = True
        return changed

    def describe(self, object_id) -> str:
        obj = self._objects.get(object_id)
        if obj is None:
            return "未知物品"
        label = obj.get("label", object_id)
        states = obj.get("states") or [""]
        index = self._indexes.get(object_id, 0)
        state = states[index] if 0 <= index < len(states) else ""
        if obj.get("interaction") == "inspect":
            return f"{label} · {obj.get('description', state)}"
        return f"{label} · {state}"
