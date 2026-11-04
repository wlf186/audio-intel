"""Event-driven local managers with recovery polling for out-of-process writes."""
import asyncio


async def wait_for_work(event: asyncio.Event) -> None:
    try:
        await asyncio.wait_for(event.wait(), timeout=5.0)
    except TimeoutError:
        pass
