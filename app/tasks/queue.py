import os
import threading
import time
from queue import Queue
from flask import current_app, has_app_context


_queue = Queue()
_worker_started = False
_worker_pid = None
_worker_lock = threading.Lock()

def _reset_after_fork():
    global _queue, _worker_started, _worker_pid, _worker_lock
    _queue = Queue()
    _worker_started = False
    _worker_pid = None
    _worker_lock = threading.Lock()

if hasattr(os, "register_at_fork"):
    os.register_at_fork(after_in_child=_reset_after_fork)

try:
    from redis import Redis
    from rq import Queue as RqQueue
except Exception:  # pragma: no cover - optional dependency
    Redis = None
    RqQueue = None


def get_mode():
    return "rq" if (Redis and RqQueue and os.environ.get("REDIS_URL")) else "inline"


def enqueue(func, *args, **kwargs):
    if get_mode() == "rq":
        conn = Redis.from_url(os.environ["REDIS_URL"], socket_connect_timeout=2, socket_timeout=2)
        rq_queue = RqQueue("default", connection=conn)
        rq_queue.enqueue(func, *args, **kwargs)
        return
    if has_app_context():
        if os.environ.get("START_INLINE_WORKER", "1") != "1":
            raise RuntimeError("Inline worker nonaktif tanpa backend Redis/RQ. Notifikasi tidak dapat diantrikan.")
        start_worker(current_app._get_current_object())
    _queue.put((func, args, kwargs, time.time()))


def start_worker(app):
    if get_mode() != "inline":
        return
    global _worker_started, _worker_pid
    with _worker_lock:
        if _worker_started and _worker_pid == os.getpid():
            return
        thread = threading.Thread(target=_run, args=(app,), daemon=True)
        thread.start()
        _worker_started = True
        _worker_pid = os.getpid()


def get_stats():
    if get_mode() == "rq":
        try:
            conn = Redis.from_url(os.environ["REDIS_URL"], socket_connect_timeout=2, socket_timeout=2)
            rq_queue = RqQueue("default", connection=conn)
            count = rq_queue.count
            oldest_age = None
            try:
                jobs = rq_queue.get_jobs(0, 1)
                if jobs:
                    enq = jobs[0].enqueued_at or jobs[0].created_at
                    if enq:
                        oldest_age = max(time.time() - enq.timestamp(), 0)
            except Exception:
                oldest_age = None
            return {"mode": "rq", "count": count, "oldest_age_sec": oldest_age}
        except Exception:
            return {"mode": "rq", "count": None, "oldest_age_sec": None}

    with _queue.mutex:
        items = list(_queue.queue)
    count = len(items)
    oldest_age = None
    if items:
        oldest_age = max(time.time() - items[0][3], 0)
    return {"mode": "inline", "count": count, "oldest_age_sec": oldest_age}


def _run(app):
    while True:
        func, args, kwargs, _enqueued_at = _queue.get()
        try:
            with app.app_context():
                func(*args, **kwargs)
        except Exception as exc:
            app.logger.warning(f"[QUEUE] job failed: {exc}")
        finally:
            _queue.task_done()
