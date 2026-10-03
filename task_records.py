"""将只读线程元数据整理为任务记录；不读取聊天正文或执行外部操作。

本地记录使用最新轮次状态。备用 app-server 状态仅描述线程当前状态，
所以 idle / notLoaded 不等于最新轮次完成。所有时间字段均为 Unix 秒，
不以线程或整轮时间推算当前请求耗时。
"""

import math
import re
import time
import unicodedata
import uuid


_MAX_ROWS = 32
_ACTIVE_WINDOW_S = 15 * 60
_ATTENTION_WINDOW_S = 24 * 60 * 60
_CLOCK_TOLERANCE_S = 60
_MAX_UNIX_S = 253402300799   # datetime 支持的最大 Unix 秒（9999年末）
_UUID_PATTERN = re.compile(
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
)
_BUCKET_ORDER = {"attention": 0, "active": 1, "recent": 2}
_WAITING_FLAGS = ("waitingOnApproval", "waitingOnUserInput")
_TERMINAL_STATUSES = ("completed", "interrupted", "failed")


def task_url(thread_id):
    """只为严格的带连字符 UUID 返回聊天地址；不接受省略格式或 URL。"""
    if not isinstance(thread_id, str) or not _UUID_PATTERN.fullmatch(thread_id):
        return None
    return "codex://threads/" + str(uuid.UUID(thread_id))


def _text(value, limit):
    if not isinstance(value, str):
        return ""
    # 移除控制符、不可见格式控制符和孤立代理项，避免进入 Tk 文本或复制内容。
    value = "".join(ch for ch in value
                    if unicodedata.category(ch) not in ("Cc", "Cf", "Cs")).strip()
    return value if limit is None or len(value) <= limit else value[:limit - 1] + "…"


def _identity(value):
    url = task_url(value)
    if url:
        return url.rsplit("/", 1)[-1]
    cleaned = _text(value, 128)
    # 清洗不能把原本不合法的 UUID 变成可打开的聊天 ID。
    return "" if task_url(cleaned) else cleaned


def _number(value):
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        try:
            number = float(value)
        except (ValueError, OverflowError):
            return None
        if math.isfinite(number):
            return number
    return None


def _timestamp(value, now, milliseconds=False):
    number = _number(value)
    if number is None:
        return 0
    if milliseconds:
        number /= 1000
    # 少量时钟偏差可接受；明显未来时间不能使遗留 inProgress 保持活跃。
    return number if 0 < number <= min(now + _CLOCK_TOLERANCE_S, _MAX_UNIX_S) else 0


def _items(value):
    return value if isinstance(value, (list, tuple)) else ()


def _is_subagent(thread):
    source = thread.get("source")
    agent_path = thread.get("agentPath", thread.get("agent_path"))
    return bool(thread.get("parentThreadId") or
                (isinstance(agent_path, str) and agent_path.strip() not in ("", "/root")) or
                (isinstance(source, dict) and "subAgent" in source) or
                (isinstance(source, str) and source.startswith("subAgent")))


def _thread_status(thread):
    status = thread.get("status")
    return status if isinstance(status, dict) else {}


def _waiting_flags(thread):
    status = _thread_status(thread)
    if status.get("type") != "active":
        return ()
    flags = _items(status.get("flags"))
    return tuple(flag for flag in _WAITING_FLAGS if flag in flags)


def _fallback_updated(thread, now):
    return max(_timestamp(thread.get("updatedAt"), now),
               _timestamp(thread.get("recencyAt"), now),
               _timestamp(thread.get("updated_at_ms"), now, milliseconds=True))


def _thread_lookup(threads, now):
    lookup = {}
    for thread in _items(threads):
        if not isinstance(thread, dict) or _is_subagent(thread):
            continue
        thread_id = _identity(thread.get("id"))
        if not thread_id:
            continue
        previous = lookup.get(thread_id)
        if previous is None or _fallback_updated(thread, now) > _fallback_updated(previous, now):
            lookup[thread_id] = thread
    return lookup


def _base_row(record):
    thread_id = _identity(record.get("id"))
    if not thread_id:
        return None
    full_cwd = _text(record.get("cwd"), None)
    cwd = full_cwd if len(full_cwd) <= 1024 else full_cwd[:1023] + "…"
    # 项目名取原路径最后一段，避免超长路径被裁剪后误取中间目录。
    parts = [part for part in re.split(r"[/\\]+", full_cwd) if part]
    return {
        "id": thread_id,
        "title": _text(record.get("name"), 160) or _text(record.get("title"), 160) or "未命名任务",
        "model": _text(record.get("model"), 80),
        "project": _text(parts[-1], 80) if parts else "",
        "cwd": cwd,
        "status": "unknown",
        "status_text": "状态未知",
        "label_reason": "缺少可识别的最新轮次状态",
        "next_action": "打开聊天确认进度",
        "updated_at": 0,
        "started_at": 0,
        "ended_at": 0,
        "bucket": "recent",
        "openable": task_url(thread_id) is not None,
    }


def _set_status(row, status, text, reason, action, bucket):
    row.update(status=status, status_text=text, label_reason=reason,
               next_action=action, bucket=bucket)


def _apply_waiting(row, flags):
    if not flags:
        return
    if len(flags) == 2:
        text, reason, action = "等待审批/输入", "当前活动线程等待审批和用户输入", "打开聊天处理审批或补充输入"
    elif flags[0] == "waitingOnApproval":
        text, reason, action = "等待审批", "当前活动线程等待审批", "打开聊天处理审批请求"
    else:
        text, reason, action = "等待输入", "当前活动线程等待用户输入", "打开聊天补充所需信息"
    _set_status(row, "active", text, reason, action, "attention")


def _local_row(record, thread, now):
    row = _base_row(record)
    if row is None:
        return None
    updated = _timestamp(record.get("updated"), now, milliseconds=True)
    recency = _timestamp(record.get("recency"), now, milliseconds=True)
    freshness = max(updated, _timestamp(record.get("freshness"), now, milliseconds=True))
    started = _timestamp(record.get("started_at"), now)
    status = record.get("status")
    # 状态先检查类型，脏容器值不会参与终态比较或字典索引。
    status = status if isinstance(status, str) else ""
    ended = _timestamp(record.get("completed_at"), now) if status in _TERMINAL_STATUSES else 0
    if started and ended and ended < started:
        ended = 0
    row.update(updated_at=max(updated, recency, freshness, started, ended),
               started_at=started, ended_at=ended)
    if status == "inProgress":
        if freshness and now - freshness <= _ACTIVE_WINDOW_S:
            _set_status(row, "active", "执行中", "最新轮次执行中，15分钟内有活动",
                        "打开聊天查看进展", "active")
            # 备用状态只能补充仍新鲜的本地活动轮次，不能改写终态或过期状态。
            if thread is not None:
                _apply_waiting(row, _waiting_flags(thread))
        else:
            reason = "最新轮次仍执行中，活动已超过15分钟" if freshness else "最新轮次仍执行中，缺少有效活动时间"
            _set_status(row, "uncertain", "待确认", reason,
                        "打开聊天确认是否仍在运行", "attention")
    elif status == "failed":
        _set_status(row, "failed", "执行失败", "最新轮次记录为失败",
                    "打开聊天查看错误并决定是否重试", "attention")
    elif status == "completed":
        _set_status(row, "completed", "已完成", "最新轮次记录为完成",
                    "打开聊天查看结果", "recent")
    elif status == "interrupted":
        _set_status(row, "interrupted", "已中止", "最新轮次记录为中止",
                    "打开聊天确认是否继续", "recent")
    return row


def _fallback_row(thread, now):
    row = _base_row(thread)
    if row is None:
        return None
    row["updated_at"] = _fallback_updated(thread, now)
    # createdAt 是线程创建时间，不能充当最新轮次的开始时间。
    state = _thread_status(thread).get("type")
    state = state if isinstance(state, str) else ""
    if state == "active":
        _set_status(row, "active", "运行中", "备用线程列表明确报告 active",
                    "打开聊天查看进展", "active")
        _apply_waiting(row, _waiting_flags(thread))
    elif state == "systemError":
        _set_status(row, "failed", "运行异常", "备用线程列表报告系统异常",
                    "打开聊天查看异常", "attention")
    elif state == "idle":
        _set_status(row, "idle", "空闲", "备用线程列表仅报告空闲",
                    "打开聊天查看最新轮次", "recent")
    elif state == "notLoaded":
        _set_status(row, "unknown", "未加载", "备用线程列表报告未加载",
                    "打开聊天加载并确认进度", "recent")
    else:
        row["label_reason"] = "备用线程列表缺少可识别状态"
    return row


def _deduplicate(rows):
    by_id = {}
    for row in rows:
        if row is None:
            continue
        previous = by_id.get(row["id"])
        key = (row["updated_at"], row["started_at"], row["ended_at"])
        if previous is None or key > (previous["updated_at"], previous["started_at"], previous["ended_at"]):
            by_id[row["id"]] = row
    return list(by_id.values())


def _apply_attention_window(row, now):
    """旧异常留在最近记录中，保留失败/不确定事实及原处理提示。"""
    if row["status"] not in ("failed", "uncertain"):
        return
    updated = _timestamp(row["updated_at"], now)
    if not updated or now - updated > _ATTENTION_WINDOW_S:
        row["bucket"] = "recent"


def build_task_records(records=None, threads=(), now=None):
    """构建最多32条任务元数据；None 才回退，可信空列表仍保留 local 来源。

    records 的 updated / recency / freshness 为 Unix 毫秒，started_at /
    completed_at 为 Unix 秒。threads 使用 app-server 的 updatedAt /
    recencyAt 秒字段。未知标志和非法字段被忽略，不读取任务或消息正文。
    失败和待确认仅在有24小时内有效更新时间时计入 attention；更旧或
    时间未知的异常保留原状态并归 recent。明确等待标志仍计入 attention。
    counts 统计最终显示行各 bucket 的数量，各组互斥。
    """
    sampled_now = _number(now)
    if sampled_now is None or not 0 <= sampled_now <= _MAX_UNIX_S:
        sampled_now = time.time()
    lookup = _thread_lookup(threads, sampled_now)
    if records is None:
        rows = [_fallback_row(thread, sampled_now) for thread in lookup.values()]
        source = "fallback" if rows else "unavailable"
    else:
        rows = [_local_row(record, lookup.get(_identity(record.get("id"))), sampled_now)
                for record in _items(records) if isinstance(record, dict)]
        source = "local"
    rows = _deduplicate(rows)
    for row in rows:
        _apply_attention_window(row, sampled_now)
    rows.sort(key=lambda row: (_BUCKET_ORDER[row["bucket"]], -row["updated_at"]))
    rows = rows[:_MAX_ROWS]
    counts = {"active": 0, "attention": 0, "recent": 0}
    for row in rows:
        counts[row["bucket"]] += 1
    return {"rows": rows, "source": source, "counts": counts}
