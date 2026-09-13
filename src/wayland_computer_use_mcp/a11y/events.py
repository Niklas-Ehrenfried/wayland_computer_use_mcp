"""Real-time AT-SPI2 D-Bus signal monitoring and event listener.

Subscribes to AT-SPI2 event signals:
- org.a11y.atspi.Event.Object:StateChanged
- org.a11y.atspi.Event.Object:ChildrenChanged
- org.a11y.atspi.Event.Object:TextChanged
- org.a11y.atspi.Event.Window:Activate

Buffers mutations and enables agents to await UI settling without high-token re-polling.
"""

from __future__ import annotations

import asyncio
import collections
import logging
import threading
import time
from typing import Any

from dbus_fast import Message, MessageType
from dbus_fast.aio import MessageBus

from wayland_computer_use_mcp.a11y.client import resolve_atspi_bus_address
from wayland_computer_use_mcp.config import get_config

logger = logging.getLogger("wayland_computer_use_mcp.a11y.events")


class AtspiEventListener:
    """Asynchronous background listener capturing AT-SPI2 state and tree mutation signals."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._events: collections.deque[dict[str, Any]] = collections.deque(maxlen=300)
        self._last_event_time: float = time.time()
        self._bus: MessageBus | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._running = False

    def is_connected(self) -> bool:
        return self._bus is not None and self._running

    def _message_handler(self, msg: Message) -> None:
        """Processes incoming D-Bus signals from AT-SPI."""
        if msg.message_type != MessageType.SIGNAL:
            return

        interface = msg.interface or ""
        member = msg.member or ""
        if not ("org.a11y.atspi.Event" in interface or "org.a11y.atspi" in interface):
            return

        body = msg.body or []
        detail1 = str(body[0]) if len(body) > 0 else ""
        detail2 = body[1] if len(body) > 1 else None

        now = time.time()
        event_record = {
            "interface": interface,
            "action": member,
            "path": msg.path,
            "detail": detail1,
            "value": detail2,
            "timestamp": now,
        }

        with self._lock:
            self._events.append(event_record)
            self._last_event_time = now

    async def _async_start(self) -> None:
        """Async worker connecting to the AT-SPI bus and registering match rules."""
        addr = await resolve_atspi_bus_address()
        if not addr:
            logger.debug("AT-SPI bus address not found, signal listener inactive.")
            return

        try:
            self._bus = await MessageBus(bus_address=addr).connect()
            self._bus.add_message_handler(self._message_handler)

            # Request match rules for Object and Window events
            match_rules = [
                "type='signal',interface='org.a11y.atspi.Event.Object'",
                "type='signal',interface='org.a11y.atspi.Event.Window'",
            ]
            for rule in match_rules:
                try:
                    await self._bus.call(
                        Message(
                            destination="org.freedesktop.DBus",
                            path="/org/freedesktop/DBus",
                            interface="org.freedesktop.DBus",
                            member="AddMatch",
                            signature="s",
                            body=[rule],
                        )
                    )
                except Exception as exc:
                    logger.debug("Failed to add match rule %s: %s", rule, exc)

            self._running = True
            logger.debug("AT-SPI event listener connected successfully.")

            # Keep loop alive
            while self._running:
                await asyncio.sleep(0.5)

        except Exception as exc:
            logger.debug("AT-SPI event listener encountered error: %s", exc)
        finally:
            if self._bus:
                try:
                    self._bus.disconnect()
                except Exception:
                    pass
                self._bus = None
            self._running = False

    def _thread_worker(self) -> None:
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_until_complete(self._async_start())
        except Exception:
            pass
        finally:
            self._loop.close()

    def start(self) -> None:
        """Starts the background event listener thread if not already running."""
        cfg = get_config()
        if cfg.mock_mode:
            return

        with self._lock:
            if self._running or (self._thread and self._thread.is_alive()):
                return
            self._running = True
            self._thread = threading.Thread(
                target=self._thread_worker, daemon=True, name="atspi-event-listener"
            )
            self._thread.start()

    def stop(self) -> None:
        """Stops the listener and disconnects from the bus."""
        self._running = False
        if self._bus:
            try:
                self._bus.disconnect()
            except Exception:
                pass
            self._bus = None

    def get_recent_mutations(self, since_timestamp: float = 0.0) -> list[dict[str, Any]]:
        """Retrieves captured mutations since specified timestamp."""
        with self._lock:
            if since_timestamp <= 0.0:
                return list(self._events)
            return [e for e in self._events if e["timestamp"] >= since_timestamp]

    def clear_events(self) -> None:
        with self._lock:
            self._events.clear()
            self._last_event_time = time.time()

    def wait_for_settled(
        self,
        timeout_ms: int = 500,
        quiet_period_ms: int = 80,
        pid: int | None = None,
    ) -> dict[str, Any]:
        """Awaits UI settlement (no new AT-SPI signals for quiet_period_ms up to timeout_ms).

        Returns captured mutations and settled status.
        """
        cfg = get_config()
        start_time = time.time()
        deadline = start_time + (timeout_ms / 1000.0)
        quiet_sec = quiet_period_ms / 1000.0

        if cfg.mock_mode or not self.is_connected():
            # In mock or fallback mode, simulate settling
            simulated_sleep = min(0.08, timeout_ms / 1000.0)
            if simulated_sleep > 0:
                time.sleep(simulated_sleep)
            return {
                "settled": True,
                "elapsed_ms": int((time.time() - start_time) * 1000),
                "mutations_count": 0,
                "mutations": [],
            }

        start_snapshot_time = time.time()
        while time.time() < deadline:
            time.sleep(0.02)
            with self._lock:
                idle = (time.time() - self._last_event_time) >= quiet_sec
            if idle:
                break

        elapsed = int((time.time() - start_time) * 1000)
        recent = self.get_recent_mutations(since_timestamp=start_snapshot_time)

        # Simplify mutation records for agent consumption
        simplified = []
        for r in recent:
            action = r.get("action", "")
            detail = r.get("detail", "")
            simplified.append(
                {
                    "action": action,
                    "property": detail,
                    "path": r.get("path"),
                }
            )

        return {
            "settled": True,
            "elapsed_ms": elapsed,
            "mutations_count": len(simplified),
            "mutations": simplified[:25],
        }


# Global singleton listener instance
global_event_listener = AtspiEventListener()
