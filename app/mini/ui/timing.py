"""Bounded, payload-free Mythic timings; never log callback data, names or URLs."""
import asyncio
import contextvars
import json
import logging
import time
from contextlib import contextmanager
from aiogram import BaseMiddleware
from aiogram.client.session.middlewares.base import BaseRequestMiddleware

_current = contextvars.ContextVar("mini_callback_timing", default=None)
log = logging.getLogger(__name__)

@contextmanager
def span(name):
    metric = _current.get()
    started = time.perf_counter()
    try:
        yield
    finally:
        if metric is not None:
            metric[name + "_ms"] = metric.get(name + "_ms", 0) + (time.perf_counter()-started)*1000
            metric[name + "_count"] = metric.get(name + "_count", 0) + 1

async def run_sync(name, function, *args, **kwargs):
    def execute():
        cpu = time.thread_time()
        with span(name):
            try:
                return function(*args, **kwargs)
            finally:
                metric = _current.get()
                if metric is not None:
                    metric["worker_cpu_ms"] = metric.get("worker_cpu_ms", 0) + (time.thread_time()-cpu)*1000
    return await asyncio.to_thread(execute)

class CallbackTiming(BaseMiddleware):
    async def __call__(self, handler, event, data):
        cb = getattr(event, "callback_query", None)
        payload = (getattr(cb, "data", None) or "")
        prefixes = ("mini:myth:", "mini:mythic:", "mini:raid:", "mini:raids:", "mini:raidopen:", "mini:launch:", "mini:home:", "mini:gacha:")
        if not payload.startswith(prefixes):
            return await handler(event, data)
        received = time.perf_counter()
        metric = {"screen": "raid" if payload.startswith(("mini:raid:", "mini:raids:", "mini:raidopen:")) else ("pet" if payload.startswith(("mini:myth:", "mini:mythic:")) else "mini_open")}
        token = _current.set(metric)
        stop = asyncio.Event()
        async def lag():
            while not stop.is_set():
                began = time.perf_counter()
                await asyncio.sleep(.05)
                metric["max_loop_lag_ms"] = max(metric.get("max_loop_lag_ms", 0), max(0,(time.perf_counter()-began-.05)*1000))
        monitor = asyncio.create_task(lag())
        try:
            with span("update_dispatch"):
                return await handler(event, data)
        finally:
            stop.set()
            monitor.cancel()
            try:
                await monitor
            except asyncio.CancelledError:
                pass
            metric["total_ms"] = (time.perf_counter()-received)*1000
            log.warning("mini_callback_timing %s", json.dumps({k:round(v,3) if isinstance(v,float) else v for k,v in metric.items()},sort_keys=True))
            _current.reset(token)

class TelegramTiming(BaseRequestMiddleware):
    async def __call__(self, make_request, bot, method):
        # Class names only. Never inspect/stringify request bodies or exceptions.
        name = type(method).__name__
        if _current.get() is None:
            return await make_request(bot, method)
        group = "image_api" if name in ("SendPhoto","EditMessageMedia","EditMessageCaption") else "telegram_api"
        with span(group), span("api_" + name):
            return await make_request(bot, method)
