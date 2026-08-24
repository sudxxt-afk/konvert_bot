import asyncio
from typing import Awaitable, Callable

from config import MAX_PARALLEL


class TaskQueue:
    def __init__(self, workers: int):
        self.workers = workers
        self._q: asyncio.Queue = asyncio.Queue()
        self._started = False
        self._tasks: list[asyncio.Task] = []

    def ensure_started(self) -> None:
        if not self._started:
            self._tasks = [
                asyncio.create_task(self._worker()) for _ in range(self.workers)
            ]
            self._started = True

    async def _worker(self) -> None:
        while True:
            fut, job = await self._q.get()
            try:
                if fut.cancelled():
                    continue
                result = await job()
                if not fut.done():
                    fut.set_result(result)
            except asyncio.CancelledError:
                if not fut.done():
                    fut.cancel()
                raise
            except Exception as e:
                if not fut.done():
                    fut.set_exception(e)
            finally:
                self._q.task_done()

    def pending(self) -> int:
        return self._q.qsize()

    def submit(self, job: Callable[[], Awaitable]) -> asyncio.Future:
        self.ensure_started()
        fut = asyncio.get_running_loop().create_future()
        self._q.put_nowait((fut, job))
        return fut


_queue: TaskQueue | None = None


def get_queue() -> TaskQueue:
    global _queue
    if _queue is None:
        _queue = TaskQueue(MAX_PARALLEL)
    return _queue
