"""Server-Sent Events progress streaming helper."""
import json
import queue
import threading
from flask import Response, stream_with_context


def create_sse_progress() -> tuple:
    """Create a progress queue and a generator for SSE streaming.

    Returns:
        (queue, generator_func) — queue is used by the worker thread
        to push progress dicts; generator_func is passed to Response().
    """
    # Sentinel pushed by the generator's finally when the client disconnects,
    # so a blocked worker thread can wake up and release its run-lock.
    _CANCEL = object()
    q = queue.Queue(maxsize=100)
    cancel_flag = threading.Event()

    def generate():
        event_id = 0
        try:
            while True:
                try:
                    item = q.get(timeout=30)
                    if item is None:
                        break
                    event_id += 1
                    stage = item.get('stage', 'progress')
                    if stage == 'done':
                        yield f"id: {event_id}\nevent: done\ndata: {json.dumps(item, ensure_ascii=False)}\n\n"
                    elif stage == 'error':
                        yield f"id: {event_id}\nevent: error\ndata: {json.dumps(item, ensure_ascii=False)}\n\n"
                    elif stage == 'select':
                        yield f"id: {event_id}\nevent: select\ndata: {json.dumps(item, ensure_ascii=False)}\n\n"
                    else:
                        yield f"id: {event_id}\nevent: progress\ndata: {json.dumps(item, ensure_ascii=False)}\n\n"
                except queue.Empty:
                    event_id += 1
                    yield f"id: {event_id}\nevent: heartbeat\ndata: {json.dumps({'stage': 'heartbeat'})}\n\n"
        except GeneratorExit:
            # Client closed the connection. Drain the queue so the worker
            # thread is never blocked on a full queue, which would otherwise
            # hold the run-lock forever and freeze the feature.
            cancel_flag.set()
            while True:
                try:
                    item = q.get_nowait()
                    if item is None:
                        break
                except queue.Empty:
                    break
            raise
        finally:
            cancel_flag.set()

    def push(stage: str, detail: str = "", progress: float = 0.0, result=None):
        """Push a progress event to the queue.

        Never blocks indefinitely: if the client has gone away (cancel_flag) or
        the queue is full, progress events are dropped so the worker can reach
        its finally block and release the run-lock. Terminal events (done/
        error) still wait briefly to guarantee delivery on a full-but-live queue.
        """
        item = {"stage": stage, "detail": detail, "progress": progress}
        if result is not None:
            item["result"] = result
        terminal = stage in ('done', 'error')
        if cancel_flag.is_set():
            return
        try:
            q.put(item, timeout=2.0 if terminal else 0.1)
        except queue.Full:
            # Queue full and client not consuming — drop non-terminal events;
            # for terminal events the worker still proceeds to its finally.
            pass

    def done(result=None):
        """Signal completion."""
        if cancel_flag.is_set():
            return
        q.put({"stage": "done", "detail": "", "progress": 1.0, "result": result})
        q.put(None)

    def error(message: str):
        """Signal an error."""
        if cancel_flag.is_set():
            return
        q.put({"stage": "error", "message": message, "progress": 0.0})
        q.put(None)

    def select(matches: list):
        """Signal that user selection is needed from a list of matching chats."""
        if cancel_flag.is_set():
            return
        q.put({"stage": "select", "detail": "", "progress": 0.15, "matches": matches})
        q.put(None)

    push.done = done
    push.error = error
    push.select = select

    return push, generate


def sse_response(generator) -> Response:
    """Return a Flask Response configured for SSE streaming."""
    return Response(
        stream_with_context(generator()),
        content_type='text/event-stream',
        headers={
            'Cache-Control': 'no-cache',
            'X-Accel-Buffering': 'no',
            'Connection': 'keep-alive',
        }
    )

