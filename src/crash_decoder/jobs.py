import asyncio
import hashlib
import logging
import time
from collections.abc import Awaitable, Callable

import httpx

from .decoder import Decoder
from .images import download
from .models import Result

log = logging.getLogger(__name__)


class BusyError(ValueError):
    pass


class JobQueue:
    def __init__(self, decoder: Decoder, workers: int = 2, capacity: int = 8, cooldown: float = 20):
        if workers < 1 or capacity < 1 or cooldown < 0:
            raise ValueError("Invalid queue settings")
        self.decoder = decoder
        self.queue = asyncio.Queue(maxsize=capacity)
        self.tasks = []
        self.workers = workers
        self.cooldown = cooldown
        self.users = {}
        self.active = set()
        self.recent = {}

    def start(self):
        self.tasks = [asyncio.create_task(self.worker()) for _ in range(self.workers)]

    async def close(self):
        for task in self.tasks:
            task.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)
        while not self.queue.empty():
            _, _, future = self.queue.get_nowait()
            if not future.done():
                future.set_exception(BusyError("Bot shutting down"))
            self.queue.task_done()

    async def submit(self, user: int, message: int, action: Callable[[], Awaitable]):
        now = time.monotonic()
        self.users = {k: v for k, v in self.users.items() if now - v < self.cooldown}
        self.recent = {k: v for k, v in self.recent.items() if now - v < self.cooldown}
        if user in self.users:
            raise BusyError("Please wait before decoding again.")
        if message in self.active or message in self.recent:
            raise BusyError("This message is already being decoded or was just decoded.")
        future = asyncio.get_running_loop().create_future()
        try:
            self.queue.put_nowait((message, action, future))
        except asyncio.QueueFull as exc:
            raise BusyError("Decoder queue is full; try again shortly.") from exc
        self.active.add(message)
        self.users[user] = now
        return await future

    async def worker(self):
        while True:
            message, action, future = await self.queue.get()
            try:
                if not future.cancelled():
                    result = await action()
                    if not future.done():
                        future.set_result(result)
            except asyncio.CancelledError:
                if not future.done():
                    future.set_exception(BusyError("Bot shutting down"))
                raise
            except Exception as exc:
                if not future.done():
                    future.set_exception(exc)
            finally:
                self.active.discard(message)
                self.recent[message] = time.monotonic()
                self.queue.task_done()

    async def images(
        self,
        images: list[tuple[str, str]],
        client: httpx.AsyncClient,
        progress: Callable[[int, int], Awaitable[None]] | None = None,
    ) -> list[Result]:
        results = []
        seen = set()
        for index, (label, url) in enumerate(images, 1):
            if progress:
                await progress(index, len(images))
            start = time.monotonic()
            try:
                data = await download(client, url)
                digest = hashlib.sha256(data).digest()
                if digest in seen:
                    continue
                seen.add(digest)
                result = await asyncio.to_thread(self.decoder.image, data, label)
            except Exception as exc:
                log.warning("Image failed: %s", type(exc).__name__)
                # Avoid exposing URLs, tokens or internal paths in public reports.
                message = str(exc) if isinstance(exc, ValueError) else "Download or OCR failed."
                result = Result(
                    label, error=message[:300], bundle_checksum=self.decoder.bundle["checksum"]
                )
            log.info(
                "decode seconds=%.2f build=%s bundle=%s error=%s",
                time.monotonic() - start,
                result.detection.build_id if result.detection else None,
                result.bundle_checksum,
                bool(result.error),
            )
            results.append(result)
        return results
