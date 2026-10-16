"""Refresh off the event loop before handling an update; background publication too."""
import asyncio
from aiogram import BaseMiddleware
from .registry import get_registry

class EditorialMiddleware(BaseMiddleware):
    async def __call__(self, handler, event, data):
        registry = get_registry()
        snapshot = await asyncio.to_thread(registry.loader.refresh)
        with registry.read(snapshot):
            return await handler(event, data)
