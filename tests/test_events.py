from __future__ import annotations

import asyncio

from audio_intel.events import SnapshotHub


def test_snapshot_hub_skips_full_load_while_semantic_revision_is_idle() -> None:
    async def scenario() -> None:
        revision = {"value": 1}
        loads = 0

        def load() -> dict[str, object]:
            nonlocal loads
            loads += 1
            return {"jobs": [{"id": "job", "state": revision["value"]}], "workers": []}

        hub = SnapshotHub(
            load, poll_seconds=.01,
            revision_loader=lambda: dict(revision),
        )
        queue = await hub.subscribe()
        assert (await queue.get())["jobs"][0]["state"] == 1
        await asyncio.sleep(.05)
        assert loads == 1

        revision["value"] = 2
        update = await asyncio.wait_for(queue.get(), timeout=.2)
        assert update["jobs"][0]["state"] == 2
        assert loads == 2
        await hub.unsubscribe(queue)
        await asyncio.sleep(.02)

    asyncio.run(scenario())
