"""Local companion needs, model thoughts and completed-life memories.

All public methods are called on the UI thread. No network or third-party
dependencies are used; a missing path is an in-memory preview.
"""

import json
import math
import os
from pathlib import Path
import re
import tempfile
import time
import unicodedata

PROFILES = {
    "codex": {"temperament": "安静细致", "likes": ["书", "绿植"]},
    "kimi": {"temperament": "外向好奇", "likes": ["咖啡", "聊天", "橘子"]},
}
_IDENTITIES = tuple(PROFILES)
_MOODS = ("steady", "curious", "warm", "tired", "focused", "cheerful")
_ACTIONS = ("stay", "read", "coffee", "water_plant", "rest", "chat", "care_cat", "stretch")
_NEEDS = ("energy", "social", "curiosity")
_DEFAULTS = {
    "codex": {"energy": 78.0, "social": 58.0, "curiosity": 70.0},
    "kimi": {"energy": 76.0, "social": 72.0, "curiosity": 80.0},
}
_EFFECTS = {
    "stay": {},
    "read": {"energy": -2.0, "curiosity": 22.0},
    "coffee": {"energy": 16.0, "social": 3.0},
    "water_plant": {"energy": -1.0, "curiosity": 10.0},
    "rest": {"energy": 25.0},
    "chat": {"energy": -1.0, "social": 24.0},
    "care_cat": {"energy": -2.0, "social": 16.0, "curiosity": 6.0},
    "stretch": {"energy": 12.0, "curiosity": 2.0},
}
_MAX_FILE = 65536
_MAX_GAP = 120.0
_OFFLINE_CAP = 1800.0
_SAVE_INTERVAL = 30.0
_MAX_COUNT = 2147483647


def _number(value):
    try:
        return type(value) in (int, float) and math.isfinite(value)
    except OverflowError:
        return False


def _controls(text):
    return any(unicodedata.category(char) in ("Cc", "Cf", "Cs") for char in text)


def _chinese(text, limit, *, empty=False):
    return (isinstance(text, str) and (empty and text == "" or
            1 <= len(text) <= limit and bool(text.strip())
            and not _controls(text) and bool(re.search(r"[\u3400-\u9fff]", text))))


def _event(text):
    if not isinstance(text, str):
        return ""
    text = "".join(char for char in text if unicodedata.category(char) not in ("Cc", "Cf", "Cs"))
    text = text.strip()[:80]
    return text if _chinese(text, 80) else ""


def _default_characters():
    return {key: dict(_DEFAULTS[key], mood="steady", thought="", last_thought_at=0.0,
                     last_action="", memories=[], preferences={action: 0 for action in _ACTIONS})
            for key in _IDENTITIES}


class SoulState:
    """Persist two designed personalities and observations, never a call budget.

    ``complete`` is called only after the scene has actually finished an action;
    ``apply_thought`` only applies the model's mood and thought.
    """

    profiles = PROFILES

    def __init__(self, path=None):
        self.path = Path(path) if path is not None else None
        self._characters = _default_characters()
        self._now = time.time()
        self._last_saved_at = self._now
        self._dirty, self._closed = False, False
        self.error = ""
        self._load()

    def snapshot(self, key):
        """Return an independent snapshot; memories are newest first."""
        character = self._characters[key]
        profile = PROFILES[key]
        return {"profile": {"temperament": profile["temperament"], "likes": list(profile["likes"])},
                **{need: round(character[need], 1) for need in _NEEDS},
                "mood": character["mood"], "thought": character["thought"],
                "last_thought_at": character["last_thought_at"], "last_action": character["last_action"],
                "memories": [dict(item) for item in character["memories"][:8]],
                "preferences": dict(character["preferences"]), "error": self.error}

    def tick(self, states):
        """Slowly advance needs using wall time and observed real work states."""
        if self._closed:
            return
        now = time.time()
        if now <= self._now:
            return
        minutes = min(now - self._now, _MAX_GAP) / 60.0
        self._now = now
        states = states if isinstance(states, dict) else {}
        for key, character in self._characters.items():
            working = states.get(key) == "active"
            character["energy"] -= (0.18 + (0.26 if working else 0.0)) * minutes
            character["social"] -= 0.12 * minutes
            character["curiosity"] -= (0.10 + (0.10 if working else 0.0)) * minutes
            self._clamp(character)
        self._dirty = True

    def apply_thought(self, key, entry, at=None):
        """Accept a model mood/thought without recording or completing its action."""
        if self._closed or key not in self._characters or not isinstance(entry, dict):
            return False
        mood, thought = entry.get("mood"), entry.get("thought")
        at = time.time() if at is None else at
        if (not isinstance(mood, str) or mood not in _MOODS or not _chinese(thought, 60)
                or not thought.startswith("我") or not _number(at) or at <= 0):
            return False
        character = self._characters[key]
        if at < character["last_thought_at"]:
            return False
        character.update(mood=mood, thought=thought, last_thought_at=float(at))
        self._dirty = True
        return True

    def complete(self, key, action, event):
        """Apply a completed scene action, its preference count and a real memory."""
        if (self._closed or key not in self._characters or not isinstance(action, str)
                or action not in _ACTIONS):
            return False
        text = _event(event)
        if not text:
            return False
        character = self._characters[key]
        for need, change in _EFFECTS[action].items():
            character[need] += change
        self._clamp(character)
        character["last_action"] = action
        character["preferences"][action] = min(_MAX_COUNT, character["preferences"][action] + 1)
        self._remember(character, text)
        return True

    def remember(self, key, event):
        """Record a local observation without altering needs or action preferences."""
        if self._closed or key not in self._characters:
            return False
        text = _event(event)
        if not text:
            return False
        self._remember(self._characters[key], text)
        return True

    def _remember(self, character, text):
        character["memories"].insert(0, {"at": time.time(), "event": text})
        del character["memories"][8:]
        self._dirty = True

    @staticmethod
    def _clamp(character):
        for need in _NEEDS:
            character[need] = max(0.0, min(100.0, character[need]))

    def save(self, force=False):
        """Atomically save at most every 30 seconds unless force is requested."""
        now = time.time()
        if (self.path is None or not force and (not self._dirty
                or now - self._last_saved_at < _SAVE_INTERVAL)):
            return False
        temporary = None
        try:
            state = {"version": 1, "characters": self._characters, "last_saved_at": self._now}
            data = json.dumps(state, ensure_ascii=False, allow_nan=False).encode("utf-8")
            if len(data) > _MAX_FILE:
                raise ValueError("size")
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(dir=self.path.parent, prefix=self.path.name + ".",
                                             suffix=".tmp", delete=False) as handle:
                temporary = handle.name
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.path)
            self._last_saved_at, self._dirty = now, False
            return True
        except (OSError, ValueError, TypeError):
            self.error = "伙伴日常存档无法保存"
            return False
        finally:
            if temporary:
                try:
                    os.unlink(temporary)
                except OSError:
                    pass

    def _load(self):
        if self.path is None:
            return
        try:
            with self.path.open("rb") as handle:
                raw = handle.read(_MAX_FILE + 1)
            if len(raw) > _MAX_FILE:
                raise ValueError("size")
            state = json.loads(raw)
            if (not isinstance(state, dict) or type(state.get("version")) is not int
                    or state["version"] != 1 or not _number(state.get("last_saved_at"))
                    or state["last_saved_at"] <= 0):
                raise ValueError("version")
            characters = state.get("characters")
            if not isinstance(characters, dict) or set(characters) != set(_IDENTITIES):
                raise ValueError("characters")
            loaded = {}
            for key in _IDENTITIES:
                value = characters[key]
                if (not isinstance(value, dict) or set(value) != set(self._characters[key])
                        or any(not _number(value.get(need)) or not 0 <= value[need] <= 100 for need in _NEEDS)
                        or not isinstance(value.get("mood"), str) or value["mood"] not in _MOODS
                        or not _chinese(value.get("thought"), 60, empty=True)
                        or value["thought"] and not value["thought"].startswith("我")
                        or not _number(value.get("last_thought_at")) or value["last_thought_at"] < 0
                        or not isinstance(value.get("last_action"), str)
                        or value["last_action"] not in ("", *_ACTIONS)):
                    raise ValueError("character")
                memories, preferences = value.get("memories"), value.get("preferences")
                if (not isinstance(memories, list) or len(memories) > 8
                        or not isinstance(preferences, dict) or set(preferences) != set(_ACTIONS)
                        or any(type(count) is not int or not 0 <= count <= _MAX_COUNT for count in preferences.values())):
                    raise ValueError("memories")
                for memory in memories:
                    if (not isinstance(memory, dict) or set(memory) != {"at", "event"}
                            or not _number(memory.get("at")) or memory["at"] <= 0
                            or not _chinese(memory.get("event"), 80)):
                        raise ValueError("memory")
                loaded[key] = dict(value, memories=[dict(item) for item in memories],
                                   preferences=dict(preferences))
            self._characters = loaded
            minutes = min(max(self._now - state["last_saved_at"], 0.0), _OFFLINE_CAP) / 60.0
            for character in self._characters.values():
                character["energy"] += 0.45 * minutes
                character["social"] -= 0.08 * minutes
                character["curiosity"] -= 0.06 * minutes
                self._clamp(character)
            self._dirty = minutes > 0
        except FileNotFoundError:
            pass
        except (OSError, ValueError, TypeError, OverflowError, RecursionError):
            self._characters = _default_characters()
            self._dirty = True
            self.error = "伙伴日常存档异常，已恢复默认人格"

    def close(self):
        if not self._closed:
            self.save(force=True)
            self._closed = True
