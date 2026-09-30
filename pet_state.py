"""PixelStudio 宠物猫橘子的本地持久化状态（仅标准库）。

只负责四维需求数值、事件日志与 v1 JSON 原子存档；无网络、无外部依赖。
时钟使用 Unix 秒；单个运行时间隙最多折算 120 秒，避免长时间最小化后
惩罚过重。
"""

import json
import math
import os
import tempfile
import time
from pathlib import Path

_VERSION = 1
_NAME = "橘子"
_MAX_GAP = 120.0          # 单次运行时间隙上限（秒）
_OFFLINE_CAP = 1800.0     # 离线折算上限（30 分钟）
_SAVE_INTERVAL = 30.0     # 普通刷盘最小间隔（秒）
_MAX_FILE = 64 * 1024     # 存档大小上限

_DEFAULTS = {"fullness": 52.0, "water": 72.0, "energy": 76.0, "affection": 42.0}
_NEED_KEYS = tuple(_DEFAULTS)
# 每分钟变化：清醒状态
_RATES = {"fullness": -0.9, "water": -0.7, "energy": -0.6, "affection": -0.3}
_SLEEP_ENERGY = 3.0       # 睡眠能量回复 / 分钟
_OFFLINE_ENERGY = 0.5     # 离线能量回复 / 分钟
_PLAY_ENERGY = -1.0       # 玩耍额外能量消耗 / 分钟

_COOLDOWNS = {"feed": 60.0, "water": 60.0, "play": 60.0, "pet": 8.0}
_CARE_TEXT = {
    "feed": "喂了猫粮，橘子吃得很香",
    "water": "换了清水",
    "play": "陪橘子玩了一会儿",
    "pet": "摸了摸橘子",
}


def _finite_number(value):
    try:
        return type(value) in (int, float) and math.isfinite(value)
    except OverflowError:
        return False


class PetState:
    """管理橘子的四维需求、照料冷却与独立存档。

    path 为 None 时仅内存预览，绝不写盘。存档位于调用方指定路径，
    损坏或版本不符时使用默认值。所有计时基于 Unix 秒。
    """

    def __init__(self, path: Path | None):
        self._path = Path(path) if path is not None else None
        self._needs = dict(_DEFAULTS)
        self._journal = []       # 新事件插到头部，最多 6 条
        self._last_care = None
        self._cooldowns = {}     # action -> 成功时的 Unix 秒
        self._now = time.time()
        self._last_saved_at = self._now
        self._dirty = False
        self._load()

    # ---------- 时钟与数值 ----------

    def _advance(self, now, *, sleeping=False, playing=False):
        if not _finite_number(now):
            now = time.time()
        now = float(now)
        if not math.isfinite(now):
            now = time.time()
        if now < self._now:            # 时钟不倒退
            return
        gap = min(now - self._now, _MAX_GAP)
        self._now = now
        if gap <= 0:
            return
        minutes = gap / 60.0
        for key in ("fullness", "water", "affection"):
            self._needs[key] += _RATES[key] * minutes
        if sleeping:
            self._needs["energy"] += _SLEEP_ENERGY * minutes
        else:
            self._needs["energy"] += _RATES["energy"] * minutes
        if playing:
            self._needs["energy"] += _PLAY_ENERGY * minutes
        self._clamp()
        self._dirty = True

    def _clamp(self):
        for key in _NEED_KEYS:
            value = self._needs[key]
            if not math.isfinite(value):
                value = _DEFAULTS[key]
            self._needs[key] = min(100.0, max(0.0, value))

    def tick(self, now=None, *, sleeping=False, playing=False):
        """按真实经过秒推进需求；sleeping 回能，playing 额外耗能。"""
        self._advance(now, sleeping=bool(sleeping), playing=bool(playing))

    # ---------- 查询 ----------

    def snapshot(self):
        """返回新 dict；数值保留 1 位小数，journal 新→旧最多 6 条。"""
        return {
            "name": _NAME,
            "fullness": round(self._needs["fullness"], 1),
            "water": round(self._needs["water"], 1),
            "energy": round(self._needs["energy"], 1),
            "affection": round(self._needs["affection"], 1),
            "journal": [dict(item) for item in self._journal[:6]],
            "last_care": dict(self._last_care) if self._last_care else None,
        }

    def need(self):
        full = self._needs["fullness"]
        water = self._needs["water"]
        energy = self._needs["energy"]
        affection = self._needs["affection"]
        if full < 55:
            return "feed"
        if water < 50:
            return "water"
        if energy < 35:
            return "sleep"
        if affection < 50:
            return "play"
        return None

    # ---------- 照料 ----------

    def apply_care(self, action, caregiver, now=None):
        """执行 feed/water/play/pet；未知、拒绝或冷却中返回 False。"""
        if action not in _COOLDOWNS:
            return False
        name = str(caregiver).strip()[:24] if caregiver is not None else ""
        if not name:
            return False
        self._advance(now)
        at = self._now
        last = self._cooldowns.get(action, 0.0)
        if at - last < _COOLDOWNS[action]:
            return False
        if action == "feed":
            if self._needs["fullness"] >= 92:
                return False
            self._needs["fullness"] += 42.0
            self._needs["affection"] += 5.0
        elif action == "water":
            if self._needs["water"] >= 92:
                return False
            self._needs["water"] += 45.0
            self._needs["affection"] += 2.0
        elif action == "play":
            if self._needs["energy"] < 20:
                return False
            self._needs["affection"] += 18.0
            self._needs["energy"] -= 4.0
        else:  # pet
            self._needs["affection"] += 5.0
        self._clamp()
        self._cooldowns[action] = at
        event = {"at": at, "actor": name, "text": f"{name} {_CARE_TEXT[action]}"}
        self._journal.insert(0, event)
        del self._journal[6:]
        self._last_care = dict(event, action=action)
        self._dirty = True
        return True

    # ---------- 存档 ----------

    def save(self, force=False):
        """脏数据时原子写盘；非 force 至少间隔 30 秒。返回是否落盘。"""
        if self._path is None or (not force and not self._dirty):
            return False
        if not force and time.time() - self._last_saved_at < _SAVE_INTERVAL:
            return False
        data = {
            "version": _VERSION,
            "needs": dict(self._needs),
            "journal": [dict(item) for item in self._journal[:6]],
            "last_care": dict(self._last_care) if self._last_care else None,
            "last_saved_at": self._now,
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
            self._last_saved_at = time.time()
            self._dirty = False
            return True
        except OSError:
            return False
        finally:
            if tmp_name is not None:
                try:
                    os.unlink(tmp_name)
                except OSError:
                    pass

    def _load(self):
        if self._path is None:
            return
        try:
            with self._path.open("rb") as fp:
                raw = fp.read(_MAX_FILE+1)
            if len(raw) > _MAX_FILE:
                return
            data = json.loads(raw.decode("utf-8"))
            if not isinstance(data, dict) or data.get("version") != _VERSION:
                return
        except (OSError, ValueError, UnicodeDecodeError, RecursionError):
            return
        needs = data.get("needs")
        if isinstance(needs, dict):
            for key in _NEED_KEYS:
                value = needs.get(key)
                if not _finite_number(value):
                    continue
                self._needs[key] = min(100.0, max(0.0, float(value)))
        journal = data.get("journal")
        if isinstance(journal, list):
            for item in journal[:6]:
                if not isinstance(item, dict):
                    continue
                at = item.get("at")
                actor = item.get("actor")
                text = item.get("text")
                if (not _finite_number(at)
                        or not isinstance(actor, str) or not isinstance(text, str)):
                    continue
                self._journal.append({"at": float(at), "actor": actor, "text": text})
        last_care = data.get("last_care")
        if isinstance(last_care, dict):
            at = last_care.get("at")
            if _finite_number(at):
                self._last_care = {
                    key: last_care[key]
                    for key in ("at", "actor", "text", "action")
                    if key in last_care
                }
                action = last_care.get("action")
                if isinstance(action, str) and action in _COOLDOWNS:
                    self._cooldowns[action] = min(float(at), self._now)
        saved_at = data.get("last_saved_at")
        if not _finite_number(saved_at):
            return
        self._apply_offline(float(saved_at))

    def _apply_offline(self, saved_at):
        gap = min(max(self._now - saved_at, 0.0), _OFFLINE_CAP)
        if gap <= 0:
            return
        minutes = gap / 60.0
        for key in ("fullness", "water", "affection"):
            self._needs[key] += _RATES[key] * minutes
        self._needs["energy"] += _OFFLINE_ENERGY * minutes
        self._clamp()
