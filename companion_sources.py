"""常驻伙伴只读数据源：为 PixelStudio 画卷提供 Codex 与 Kimi 状态快照。

仅使用 Python 标准库；所有上游失败都会降级为 unknown，不向 UI 抛异常。
"""

import json
import math
import os
import re
import sqlite3
import time
import urllib.error
import urllib.parse
import urllib.request

from task_records import build_task_records

_FRESH_MS = 15 * 60 * 1000      # Codex 活动新鲜度窗口
_KIMI_STALE_S = 15              # 网关 observed_at 过期窗口
_KIMI_FAIL_S = 60               # 最近失败计入 systemError 的窗口
_KIMI_TIMEOUT = 1.5
_KIMI_READ_LIMIT = 128 * 1024
_KIMI_MODELS = ("kimi-for-coding", "k3", "k3-256k")
_LOOPBACK = ("127.0.0.1", "localhost", "::1")
_STATUS_TYPES = ("active", "idle", "unknown", "systemError")
_PHASES = ("working", "summary", "idle", "unknown")
_KIMI_OUTCOMES = {"transport_complete": "完成", "transport_error": "失败",
                  "rejected": "被拒绝", "upstream_error": "上游错误"}


def _num(value):
    return value if (isinstance(value, (int, float)) and not isinstance(value, bool)
                     and math.isfinite(value)) else 0


def _clip(text, limit):
    text = "" if text is None else str(text)
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _rel_time(ts):
    """把 Unix 秒时间戳转成口语化的相对时间。"""
    if not ts:
        return "时间未知"
    delta = max(0, time.time() - float(ts))
    if delta < 60:
        return "刚刚"
    if delta < 3600:
        return "%d 分钟前" % (delta // 60)
    if delta < 86400:
        return "%d 小时前" % (delta // 3600)
    return "%d 天前" % (delta // 86400)


class _LoopbackRedirectHandler(urllib.request.HTTPRedirectHandler):
    """只允许跳转到回环地址；远程跳转返回 None，由 urllib 按 HTTPError 抛出。"""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        target = urllib.parse.urlparse(newurl)
        if target.scheme == "http" and (target.hostname or "").lower() in _LOOPBACK:
            return super().redirect_request(req, fp, code, msg, headers, newurl)
        return None


class CompanionMonitor:
    """常驻伙伴状态监控；snapshot 永远按 Codex/Kimi 顺序返回两条记录。"""

    def snapshot(self, threads):
        return [self._codex_entry(threads), self._kimi_entry()]

    def snapshot_with_tasks(self, threads):
        """Share one metadata read between the mascot and the task overview."""
        try:
            records = self._read_codex_threads()
        except Exception:
            records = None
        try:
            codex = (self._codex_entry_from_records(records) if records is not None
                     else self._codex_entry_from_threads(threads))
        except Exception:
            codex = self._entry("companion:codex", "Codex", "unknown", "状态未知",
                                task="任务源暂不可用")
        return {"companions": [codex, self._kimi_entry()],
                "tasks": build_task_records(records, threads)}

    def _entry(self, companion_id, name, status, status_text, task="", detail="",
               model="", activity_count=0, phase="unknown",
               turn_id="", thread_id="", ended_at=0):
        try:
            count = max(0, int(activity_count))
        except (TypeError, ValueError):
            count = 0
        return {
            "id": companion_id,
            "name": name,
            "agentNickname": name,
            "agentRole": "常驻伙伴",
            "status": {"type": status if status in _STATUS_TYPES else "unknown"},
            "statusText": _clip(status_text, 40),
            "task": _clip(task, 80),
            "detail": _clip(detail, 120),
            "model": _clip(model, 60),
            "activity_count": count,
            "phase": phase if phase in _PHASES else "unknown",
            "turn_id": "" if turn_id is None else str(turn_id),
            "thread_id": "" if thread_id is None else str(thread_id),
            "ended_at": max(0, int(ended_at)) if isinstance(ended_at, (int, float)) else 0,
        }

    # ---------- Codex 来源（本地 sqlite，只读） ----------

    def _codex_entry(self, threads):
        records = None
        try:
            records = self._read_codex_threads()
        except Exception:
            records = None
        try:
            if records:
                return self._codex_entry_from_records(records)
        except Exception:
            pass
        try:
            return self._codex_entry_from_threads(threads if isinstance(threads, list) else [])
        except Exception:
            return self._entry("companion:codex", "Codex", "unknown", "状态未知", task="任务源暂不可用")

    def _read_codex_threads(self):
        home = os.environ.get("CODEX_HOME") or os.path.join(os.path.expanduser("~"), ".codex")
        paths = {}
        for prefix, key in (("state_", "state"), ("thread_history_", "history")):
            best, best_num = None, -1
            try:
                with os.scandir(home) as entries:
                    for entry in entries:
                        match = re.fullmatch(re.escape(prefix) + r"(\d+)\.sqlite", entry.name)
                        if match and int(match.group(1)) > best_num:
                            best_num, best = int(match.group(1)), entry.path
            except OSError:
                return None
            paths[key] = best
        if not paths["state"] or not paths["history"]:
            return None
        conns = []
        try:
            for path in (paths["state"], paths["history"]):
                uri = "file:%s?mode=ro" % urllib.parse.quote(path.replace("\\", "/"), safe="/:")
                conns.append(sqlite3.connect(uri, uri=True, timeout=1))
            columns = {r[1] for r in conns[0].execute("PRAGMA table_info(threads)")}
            cwd_column = "cwd" if "cwd" in columns else "'' AS cwd"
            archived_clause = " AND COALESCE(archived, 0) = 0" if "archived" in columns else ""
            rows = conns[0].execute(
                "SELECT id, name, title, agent_path, source, updated_at_ms, recency_at_ms,"
                " model, " + cwd_column + " FROM threads WHERE COALESCE(agent_path, '') IN ('', '/root')"
                " AND COALESCE(source, '') NOT LIKE '%subAgent%'"
                + archived_clause +
                " ORDER BY COALESCE(updated_at_ms, 0) DESC LIMIT 32").fetchall()
            records = [r for r in (self._codex_thread_record(conns[1], row) for row in rows) if r]
            records.sort(key=lambda r: r["recency"] or r["updated"] or 0, reverse=True)
            return records[:32]
        finally:
            for conn in conns:
                conn.close()

    @staticmethod
    def _codex_thread_record(history, row):
        tid, name, title, agent_path, source, updated, recency, model, cwd = row
        if isinstance(source, str) and "subAgent" in source:
            return None
        agent_path = (agent_path or "").strip()
        if agent_path and agent_path != "/root":
            return None
        turn_id, turn_status, started_at, completed_at, final_item = "", None, 0, 0, None
        if tid:
            found = history.execute(
                "SELECT turn_id, status, started_at, completed_at, final_agent_item_id"
                " FROM thread_turns WHERE thread_id = ?"
                " ORDER BY rollout_ordinal DESC LIMIT 1", (tid,)).fetchone()
            if found:
                turn_id, turn_status = found[0] or "", found[1]
                started_at = _num(found[2])
                completed_at = _num(found[3])
                final_item = found[4]
        item_ms, final_phase = 0, False
        if turn_id:
            value = history.execute(
                "SELECT MAX(COALESCE(completed_at_ms, started_at_ms, created_at_ms))"
                " FROM thread_items WHERE thread_id = ? AND turn_id = ?",
                (tid, turn_id)).fetchone()[0]
            item_ms = _num(value)
            phase_row = history.execute(
                "SELECT CASE WHEN json_valid(item_json) THEN json_extract(item_json, '$.phase') END"
                " FROM thread_items WHERE thread_id = ? AND turn_id = ? AND item_type = 'agentMessage'"
                " ORDER BY updated_at_ordinal DESC LIMIT 1", (tid, turn_id)).fetchone()
            final_phase = bool(phase_row and phase_row[0] == "final_answer")
        updated = _num(updated); recency = _num(recency) or updated
        return {"id": tid or "", "name": name or title or "未命名任务", "model": model or "",
                "updated": updated, "recency": recency, "turn_id": turn_id,
                "cwd": cwd or "", "started_at": started_at,
                "status": turn_status, "completed_at": completed_at,
                "final_item": final_item or final_phase, "freshness": max(updated, item_ms)}

    @staticmethod
    def _record_kind(record, now_ms):
        status = record.get("status")
        if status == "inProgress":
            return "active" if now_ms - record["freshness"] <= _FRESH_MS else "unknown"
        if status == "failed":
            return "error"
        if status in ("completed", "interrupted"):
            return "idle"
        return "unknown"

    def _codex_entry_from_records(self, records):
        now_ms = time.time() * 1000
        kinds = [(self._record_kind(r, now_ms), r) for r in records]
        fresh = [r for k, r in kinds if k == "active"]
        if fresh:
            return self._codex_record_entry(max(fresh, key=lambda r: r["freshness"]),
                                            "active", len(fresh))
        if kinds:
            kind, chosen = max(kinds, key=lambda kr: max(kr[1]["freshness"], kr[1]["completed_at"]*1000))
            return self._codex_record_entry(chosen, kind, 0)
        return self._entry("companion:codex", "Codex", "unknown", "状态未知",
                           detail="本地 Codex 暂无主线程记录")

    def _codex_record_entry(self, record, kind, activity_count):
        phase, text = "unknown", {"active": "正在工作", "idle": "空闲",
                                  "error": "运行失败", "unknown": "状态待确认"}[kind]
        if kind == "active":
            phase = "summary" if record.get("final_item") else "working"
            text = "整理结果" if phase == "summary" else text
        elif kind == "idle":
            phase = "idle"
            text = "已中止" if record.get("status") == "interrupted" else text
        model = record.get("model") or ""
        detail = "本地 Codex" + (" · " + model if model else "")
        detail += " · " + _rel_time((record.get("freshness") or 0) / 1000)
        if activity_count > 1:
            detail += " · %d 项进行中" % activity_count
        prefix = "" if kind == "active" else "最近中止：" if record.get("status") == "interrupted" else "最近完成：" if kind == "idle" else "最近失败：" if kind == "error" else "待确认："
        return self._entry("companion:codex", "Codex", "systemError" if kind == "error" else kind, text,
                           task=prefix+(record.get("name") or "未命名任务"), detail=detail,
                           model=model, activity_count=activity_count, phase=phase,
                           turn_id=record.get("turn_id"), thread_id=record.get("id"),
                           ended_at=0 if kind == "active" else record.get("completed_at"))

    @staticmethod
    def _is_subagent(thread):
        if thread.get("parentThreadId"):
            return True
        source = thread.get("source")
        if isinstance(source, dict):
            return "subAgent" in source
        return isinstance(source, str) and source.startswith("subAgent")

    def _codex_entry_from_threads(self, threads):
        mains = [t for t in threads
                 if isinstance(t, dict) and t.get("id") and not self._is_subagent(t)]
        if not mains:
            return self._entry("companion:codex", "Codex", "unknown", "状态未知",
                               detail="线程列表不可用")
        actives = [t for t in mains if (t.get("status") or {}).get("type") == "active"]
        def updated(t):
            return _num(t.get("updatedAt") or t.get("recencyAt")) or _num(t.get("updated_at_ms"))/1000
        if actives:
            chosen = max(actives, key=updated)
            kind, text, phase = "active", "运行中", "working"
        else:
            chosen = max(mains, key=updated)
            state = (chosen.get("status") or {}).get("type")
            if state == "notLoaded":
                kind, text, phase = "unknown", "未加载", "unknown"
            elif state == "systemError":
                kind, text, phase = "systemError", "运行失败", "unknown"
            elif state == "idle":
                kind, text, phase = "idle", "空闲", "idle"
            else:
                kind, text, phase = "unknown", "状态未知", "unknown"
        name = chosen.get("name") or chosen.get("title") or "未命名任务"
        model = chosen.get("model") or ""
        detail = "备用线程列表" + (" · " + model if model else "")
        detail += " · " + _rel_time(updated(chosen))
        return self._entry("companion:codex", "Codex", kind, text, task=name,
                           detail=detail, model=model, activity_count=len(actives),
                           phase=phase, thread_id=chosen.get("id") or "")

    # ---------- Kimi 来源（本地模型网关 /activity） ----------

    def _kimi_entry(self):
        try:
            payload = self._fetch_kimi_activity()
        except urllib.error.HTTPError as exc:
            text = "未接入" if exc.code == 404 else "网关异常"
            return self._entry("companion:kimi", "Kimi", "unknown", text,
                               detail="Kimi 网关 HTTP %s" % exc.code)
        except Exception:
            return self._entry("companion:kimi", "Kimi", "unknown", "网关离线",
                               detail="无法连接 Kimi 网关")
        try:
            return self._kimi_entry_from_payload(payload)
        except Exception:
            return self._entry("companion:kimi", "Kimi", "unknown", "响应异常",
                               detail="Kimi 网关响应无法解析")

    def _fetch_kimi_activity(self):
        base = (os.environ.get("PIXELSTUDIO_GATEWAY_URL") or "http://127.0.0.1:15723").strip()
        parsed = urllib.parse.urlparse(base)
        if (parsed.scheme != "http" or (parsed.hostname or "").lower() not in _LOOPBACK
                or parsed.username or parsed.password or parsed.query or parsed.fragment):
            raise OSError("gateway url must be loopback http")
        opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({}), _LoopbackRedirectHandler())
        request = urllib.request.Request(base.rstrip("/") + "/activity",
                                         headers={"Accept": "application/json"})
        with opener.open(request, timeout=_KIMI_TIMEOUT) as response:
            body = response.read(_KIMI_READ_LIMIT + 1)
        if len(body) > _KIMI_READ_LIMIT:
            raise ValueError("activity response too large")
        return json.loads(body.decode("utf-8"))

    def _kimi_entry_from_payload(self, payload):
        if not isinstance(payload, dict):
            return self._entry("companion:kimi", "Kimi", "unknown", "响应异常",
                               detail="Kimi 网关响应格式不符")
        if payload.get("schema_version") != 1 or payload.get("service") != "codex-model-gateway":
            return self._entry("companion:kimi", "Kimi", "unknown", "响应异常",
                               detail="Kimi 网关 schema 不符")
        observed = _num(payload.get("observed_at"))
        if not observed or time.time() - observed > _KIMI_STALE_S:
            return self._entry("companion:kimi", "Kimi", "unknown", "状态过期",
                               detail="Kimi 网关数据超过 %d 秒未更新" % _KIMI_STALE_S)

        def entries(key):
            value = payload.get(key)
            if not isinstance(value, list):
                return []
            return [e for e in value if isinstance(e, dict)
                    and e.get("route") == "kimi" and e.get("model") in _KIMI_MODELS]

        active = entries("active")
        if active:
            latest = max(active, key=lambda e: _num(e.get("started_at")))
            model = latest.get("model") or ""
            phase = latest.get("phase") or "unknown"
            elapsed = _num(latest.get("elapsed_seconds"))
            detail = "%s · %g 秒 · %d 路调用" % (
                {"preparing": "准备请求", "waiting_upstream": "等待模型", "receiving": "接收响应"}.get(phase, "调用中"), elapsed, len(active))
            task = "正在调用 %s" % model if model else "正在调用 Kimi 模型"
            return self._entry("companion:kimi", "Kimi", "active",
                               "接收中" if phase == "receiving" else "调用中",
                               task=task, detail=detail, model=model,
                               activity_count=len(active), phase="working")
        recent = entries("recent")
        if not recent:
            return self._entry("companion:kimi", "Kimi", "idle", "无调用",
                               task="暂无调用记录", detail="Kimi 网关在线",
                               activity_count=0, phase="idle")
        last = max(recent, key=lambda e: _num(e.get("ended_at")))
        model = last.get("model") or ""
        outcome = last.get("outcome") or "unknown"
        ended = _num(last.get("ended_at"))
        task = "%s 最近一次传输：%s" % (model or "Kimi", _KIMI_OUTCOMES.get(outcome, outcome))
        detail = "Kimi 网关 · %s" % _rel_time(ended)
        failed = outcome != "transport_complete" and ended \
            and time.time() - ended <= _KIMI_FAIL_S
        return self._entry("companion:kimi", "Kimi",
                           "systemError" if failed else "idle",
                           "传输失败" if failed else "无调用",
                           task=task, detail=detail, model=model,
                           activity_count=0, phase="unknown" if failed else "idle",
                           ended_at=ended)
