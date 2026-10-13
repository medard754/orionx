import unittest

from orionx.core.bus import EventBus
from orionx.core.events import KillSwitchActivated


class BusTests(unittest.IsolatedAsyncioTestCase):
    async def test_failing_handler_does_not_block_others(self) -> None:
        bus, seen = EventBus(), []

        async def bad(_: object) -> None:
            raise RuntimeError("boom")

        async def good(e: object) -> None:
            seen.append(e)

        bus.subscribe(KillSwitchActivated, bad)
        bus.subscribe(KillSwitchActivated, good)
        await bus.publish(KillSwitchActivated(3, "test"))
        self.assertEqual(len(seen), 1)


if __name__ == "__main__":
    unittest.main()
