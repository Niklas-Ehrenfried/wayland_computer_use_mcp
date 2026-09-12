"""AT-SPI2 D-Bus Inspector Client."""

from __future__ import annotations

import logging
import os
from typing import Any

from dbus_fast import BusType, Message
from dbus_fast.aio import MessageBus

from wayland_computer_use_mcp.a11y.constants import CONTAINER_ROLES, IGNORED_ROLES
from wayland_computer_use_mcp.a11y.synthetic import build_synthetic_tree

logger = logging.getLogger(__name__)


async def resolve_atspi_bus_address() -> str | None:
    """Discovers the AT-SPI2 D-Bus socket address from session bus or environment."""
    if os.environ.get("AT_SPI_BUS_ADDRESS"):
        return os.environ["AT_SPI_BUS_ADDRESS"]

    try:
        session_bus = await MessageBus(bus_type=BusType.SESSION).connect()
        reply = await session_bus.call(
            Message(
                destination="org.a11y.Bus",
                path="/org/a11y/bus",
                interface="org.a11y.Bus",
                member="GetAddress",
            )
        )
        try:
            from dbus_fast import Variant

            await session_bus.call(
                Message(
                    destination="org.a11y.Bus",
                    path="/org/a11y/bus",
                    interface="org.freedesktop.DBus.Properties",
                    member="Set",
                    signature="ssv",
                    body=["org.a11y.Status", "ScreenReaderEnabled", Variant("b", True)],
                )
            )
            await session_bus.call(
                Message(
                    destination="org.a11y.Bus",
                    path="/org/a11y/bus",
                    interface="org.freedesktop.DBus.Properties",
                    member="Set",
                    signature="ssv",
                    body=["org.a11y.Status", "IsEnabled", Variant("b", True)],
                )
            )
        except Exception:
            pass

        session_bus.disconnect()
        if reply and reply.body:
            return str(reply.body[0])
    except Exception as exc:
        logger.debug("Failed to query org.a11y.Bus: %s", exc)

    # Standard system fallback path
    uid = os.getuid()
    fallback_socket = f"/run/user/{uid}/at-spi/bus_0"
    if os.path.exists(fallback_socket):
        return f"unix:path={fallback_socket}"

    return None


class AtspiInspector:
    """Async inspector for AT-SPI2 accessibility tree."""

    def __init__(self, bus_address: str | None = None) -> None:
        self.bus_address = bus_address
        self.bus: MessageBus | None = None

    async def connect(self) -> bool:
        """Connects to the AT-SPI2 D-Bus daemon."""
        if not self.bus_address:
            self.bus_address = await resolve_atspi_bus_address()

        if not self.bus_address:
            logger.debug("AT-SPI2 bus address could not be resolved.")
            return False

        try:
            self.bus = await MessageBus(bus_address=self.bus_address).connect()
            return True
        except Exception as exc:
            logger.debug("Failed to connect to AT-SPI bus at %s: %s", self.bus_address, exc)
            return False

    async def close(self) -> None:
        """Closes the bus connection."""
        if self.bus:
            try:
                self.bus.disconnect()
            except Exception:
                pass
            self.bus = None

    async def get_application_tree(self, pid: int, max_depth: int = 24) -> dict[str, Any]:
        """Discovers and traverses the accessibility tree for a specific application PID."""
        from wayland_computer_use_mcp.config import get_config

        if pid <= 0 or get_config().mock_mode:
            return build_synthetic_tree(pid)

        connected = await self.connect()
        if not connected or not self.bus:
            logger.debug("AT-SPI unavailable; falling back to synthetic tree for PID %s", pid)
            return build_synthetic_tree(pid)

        try:
            root_msg = Message(
                destination="org.a11y.atspi.Registry",
                path="/org/a11y/atspi/accessible/root",
                interface="org.a11y.atspi.Accessible",
                member="GetChildren",
            )
            reply = await self.bus.call(root_msg)
            if not reply or not reply.body:
                logger.debug("Empty children from AT-SPI root registry")
                return build_synthetic_tree(pid)

            apps = reply.body[0]
            matched_app: tuple[str, str] | None = None

            for app_ref in apps:
                bus_name, path = app_ref[0], app_ref[1]
                try:
                    pid_reply = await self.bus.call(
                        Message(
                            destination="org.freedesktop.DBus",
                            path="/org/freedesktop/DBus",
                            interface="org.freedesktop.DBus",
                            member="GetConnectionUnixProcessID",
                            signature="s",
                            body=[bus_name],
                        )
                    )
                    if pid_reply and pid_reply.body and pid_reply.body[0] == pid:
                        matched_app = (bus_name, path)
                        break
                except Exception:
                    pass

            if not matched_app:
                logger.debug(
                    "No accessible AT-SPI application matched PID %s; using synthetic tree", pid
                )
                return build_synthetic_tree(pid)

            tree = await self._traverse_node(
                matched_app[0], matched_app[1], current_depth=0, max_depth=max_depth
            )
            pruned = self._prune_tree(tree)
            return pruned or tree

        except Exception as exc:
            logger.debug("Error traversing AT-SPI tree for PID %s: %s", pid, exc)
            return build_synthetic_tree(pid)
        finally:
            await self.close()

    async def _traverse_node(
        self,
        bus_name: str,
        path: str,
        current_depth: int,
        max_depth: int,
    ) -> dict[str, Any]:
        """Recursively inspects an AT-SPI accessible node."""
        if not self.bus or current_depth > max_depth:
            return {}

        node: dict[str, Any] = {
            "bus_name": bus_name,
            "path": path,
            "role": "unknown",
            "name": "",
            "states": [],
            "bounds": [0, 0, 0, 0],
            "children": [],
        }

        try:
            try:
                name_reply = await self.bus.call(
                    Message(
                        destination=bus_name,
                        path=path,
                        interface="org.freedesktop.DBus.Properties",
                        member="Get",
                        signature="ss",
                        body=["org.a11y.atspi.Accessible", "Name"],
                    )
                )
                if name_reply and name_reply.body:
                    val = name_reply.body[0]
                    node["name"] = str(val.value if hasattr(val, "value") else val)
            except Exception:
                pass

            try:
                role_reply = await self.bus.call(
                    Message(
                        destination=bus_name,
                        path=path,
                        interface="org.a11y.atspi.Accessible",
                        member="GetRoleName",
                    )
                )
                if role_reply and role_reply.body:
                    node["role"] = str(role_reply.body[0]).lower()
            except Exception:
                pass

            try:
                state_reply = await self.bus.call(
                    Message(
                        destination=bus_name,
                        path=path,
                        interface="org.a11y.atspi.Accessible",
                        member="GetState",
                    )
                )
                if state_reply and state_reply.body:
                    node["states"] = self._decode_states(state_reply.body[0])
            except Exception:
                pass

            # Geometry query: Component interface (WINDOW coordinate type = 1)
            try:
                extents_reply = await self.bus.call(
                    Message(
                        destination=bus_name,
                        path=path,
                        interface="org.a11y.atspi.Component",
                        member="GetExtents",
                        signature="u",
                        body=[1],  # 1 = ATSPI_COORD_TYPE_WINDOW
                    )
                )

                if extents_reply and extents_reply.body:
                    b = extents_reply.body[0]
                    node["bounds"] = [int(b[0]), int(b[1]), int(b[2]), int(b[3])]
            except Exception:
                pass

            if current_depth == 0:
                try:
                    screen_reply = await self.bus.call(
                        Message(
                            destination=bus_name,
                            path=path,
                            interface="org.a11y.atspi.Component",
                            member="GetExtents",
                            signature="u",
                            body=[0],  # 0: ATSPI_COORD_TYPE_SCREEN
                        )
                    )
                    if screen_reply and screen_reply.body:
                        sb = screen_reply.body[0]
                        node["screen_bounds"] = [int(sb[0]), int(sb[1]), int(sb[2]), int(sb[3])]
                except Exception:
                    pass

            # Query value if object implements Value interface
            if node["role"] in ("scale", "slider", "spin button", "progressbar"):
                try:
                    val_reply = await self.bus.call(
                        Message(
                            destination=bus_name,
                            path=path,
                            interface="org.a11y.atspi.Value",
                            member="GetCurrentValue",
                        )
                    )
                    if val_reply and val_reply.body:
                        node["value"] = float(val_reply.body[0])
                except Exception:
                    pass

            # Traverse children
            if current_depth < max_depth:
                child_msg = Message(
                    destination=bus_name,
                    path=path,
                    interface="org.a11y.atspi.Accessible",
                    member="GetChildren",
                )
                children_reply = await self.bus.call(child_msg)
                if children_reply and children_reply.body:
                    child_refs = children_reply.body[0]
                    for c_ref in child_refs:
                        c_bus, c_path = c_ref[0], c_ref[1]
                        child_node = await self._traverse_node(
                            c_bus, c_path, current_depth + 1, max_depth
                        )
                        if child_node:
                            node["children"].append(child_node)

        except Exception as exc:
            logger.debug("Failed traversing node %s %s: %s", bus_name, path, exc)

        return node

    def _decode_states(self, state_array: Any) -> list[str]:
        """Decodes raw uint32 bitset into readable AT-SPI state strings."""
        states = []
        if isinstance(state_array, (list, tuple)) and len(state_array) >= 2:
            s0 = state_array[0]
            s1 = state_array[1]
            if s0 & (1 << 3):
                states.append("busy")
            if s0 & (1 << 4):
                states.append("checked")
            if s0 & (1 << 7) or s0 & (1 << 23):
                states.append("enabled")
            if s0 & (1 << 9):
                states.append("expandable")
            if s0 & (1 << 10):
                states.append("expanded")
            if s0 & (1 << 11):
                states.append("focusable")
            if s0 & (1 << 12) or s0 & (1 << 13):
                states.append("focused")
            if s0 & (1 << 24):
                states.append("sensitive")
            if s0 & (1 << 25):
                states.append("showing")
            if (s1 & (1 << (40 - 32))) or (s0 & (1 << 31)):
                states.append("visible")
        if not states:
            states = ["visible", "enabled"]
        return states

    def _prune_tree(self, node: dict[str, Any]) -> dict[str, Any] | None:
        """Prunes anonymous container wrappers and invisible subtrees to optimize token size."""
        if not node:
            return None

        role = node.get("role", "unknown")
        name = node.get("name", "").strip()
        states = node.get("states", [])

        # Omit invisible nodes
        if "visible" not in states and "showing" not in states and states:
            return None

        if role in IGNORED_ROLES:
            return None

        # Recursively prune children first
        pruned_children = []
        for child in node.get("children", []):
            pruned_child = self._prune_tree(child)
            if pruned_child:
                pruned_children.append(pruned_child)

        # Omit empty anonymous containers
        if role in CONTAINER_ROLES and not name and not pruned_children:
            return None

        result = dict(node)
        result["children"] = pruned_children

        # Collapse redundant nameless containers that have a single child
        if role in CONTAINER_ROLES and not name and len(pruned_children) == 1:
            return pruned_children[0]

        return result
