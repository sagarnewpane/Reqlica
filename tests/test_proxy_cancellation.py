"""Cancel gateway requests without abandoning worker request leases."""

import asyncio
import threading
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi import Request
from reqlica.runtime.app import proxy
from reqlica.runtime.worker import WorkerManager


async def wait_until(predicate):
    async with asyncio.timeout(5):
        while not predicate():
            await asyncio.sleep(0.01)


class ProxyCancellationTests(unittest.TestCase):
    def test_cancellation_during_acquisition_releases_late_lease_or_consumes_startup_error(
        self,
    ):
        for startup_fails in (False, True):
            with self.subTest(startup_fails=startup_fails):
                self.cancel_during_acquisition(startup_fails)

    def cancel_during_acquisition(self, startup_fails):
        manager = WorkerManager()
        self.addCleanup(manager.close)
        request = MagicMock(spec=Request)
        request.app.state.worker_manager = manager
        worker = object()
        entered = threading.Event()
        finish = threading.Event()
        acquire_request = manager.acquire_request

        def blocked_acquire(project_id):
            entered.set()
            if not finish.wait(5):
                raise RuntimeError("Test did not unblock worker acquisition")
            if startup_fails:
                raise RuntimeError("Worker startup failed after cancellation")
            return acquire_request(project_id)

        async def scenario():
            loop = asyncio.get_running_loop()
            errors = []
            loop.set_exception_handler(lambda loop, context: errors.append(context))
            with (
                patch.object(manager, "_get_or_start", return_value=worker),
                patch.object(manager, "acquire_request", side_effect=blocked_acquire),
                patch.object(
                    manager, "release_request", wraps=manager.release_request
                ) as release,
                patch(
                    "reqlica.runtime.app._proxy_to_worker", new_callable=AsyncMock
                ) as forward,
            ):
                task = asyncio.create_task(proxy(request, "project"))
                try:
                    await wait_until(entered.is_set)
                    acquisitions = [
                        pending
                        for pending in asyncio.all_tasks()
                        if pending.get_coro().__qualname__ == "to_thread"
                    ]
                    self.assertEqual(len(acquisitions), 1)
                    acquisition = acquisitions[0]
                    task.cancel()
                    with self.assertRaises(asyncio.CancelledError):
                        await task
                    self.assertFalse(acquisition.done())
                    self.assertEqual(manager._requests, {})
                    release.assert_not_called()
                finally:
                    finish.set()
                await wait_until(acquisition.done)
                # Done callbacks run on the real loop, after task completion.
                await asyncio.sleep(0)
                self.assertEqual(manager._requests, {})
                if startup_fails:
                    release.assert_not_called()
                    # The production callback must consume the abandoned exception.
                    self.assertFalse(acquisition._log_traceback)
                else:
                    release.assert_called_once_with("project")
                forward.assert_not_called()
                self.assertEqual(errors, [])

        asyncio.run(scenario())

    def test_cancellation_while_proxying_releases_acquired_lease_exactly_once(self):
        manager = WorkerManager()
        self.addCleanup(manager.close)
        request = MagicMock(spec=Request)
        request.app.state.worker_manager = manager
        worker = object()

        async def scenario():
            forwarding = asyncio.Event()
            never_finished = asyncio.Event()

            async def blocked_proxy(request, acquired_worker):
                self.assertIs(acquired_worker, worker)
                forwarding.set()
                await never_finished.wait()

            with (
                patch.object(manager, "_get_or_start", return_value=worker),
                patch.object(
                    manager, "release_request", wraps=manager.release_request
                ) as release,
                patch(
                    "reqlica.runtime.app._proxy_to_worker", side_effect=blocked_proxy
                ) as forward,
            ):
                task = asyncio.create_task(proxy(request, "project"))
                async with asyncio.timeout(5):
                    await forwarding.wait()
                self.assertEqual(manager._requests, {"project": 1})
                release.assert_not_called()
                task.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await task
                await asyncio.sleep(0)
                self.assertEqual(manager._requests, {})
                release.assert_called_once_with("project")
                forward.assert_awaited_once_with(request, worker)

        asyncio.run(scenario())


if __name__ == "__main__":
    unittest.main()
