"""AT-SPI2 D-Bus Inspector Client."""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Any

from dbus_fast import BusType, Message, MessageType
from dbus_fast.aio import MessageBus

from wayland_computer_use_mcp._async import PersistentLoop
from wayland_computer_use_mcp.a11y.constants import (
    ATSPI_ROLE_ID_MAP,
    CONTAINER_ROLES,
    IGNORED_ROLES,
    INTERACTIVE_ROLES,
)
from wayland_computer_use_mcp.a11y.synthetic import build_synthetic_tree

logger = logging.getLogger(__name__)

_cached_bus_address: str | None = None
_bus_pool: dict[int, MessageBus] = {}
_dynamic_role_names: dict[int, str] = {}
_atspi_loop = PersistentLoop(name="AtspiAsyncLoop")


def run_atspi_sync(coro_fn: Any, *args: Any, **kwargs: Any) -> Any:
    """Executes an async AT-SPI helper on the persistent AT-SPI event loop thread."""
    return _atspi_loop.run(coro_fn(*args, **kwargs))


def reset_atspi_cache() -> None:
    """Clears cached AT-SPI bus address and pooled connections after bus environment switch."""
    global _cached_bus_address
    for bus in list(_bus_pool.values()):
        try:
            bus.disconnect()
        except Exception:
            pass
    _bus_pool.clear()
    _cached_bus_address = None


def close_atspi_pool() -> None:
    """Closes all pooled AT-SPI D-Bus connections and stops the background AT-SPI loop."""
    reset_atspi_cache()
    _atspi_loop.stop()


async def resolve_atspi_bus_address(force_refresh: bool = False) -> str | None:
    """Discovers the AT-SPI2 D-Bus socket address from session context, bus, or env."""
    global _cached_bus_address
    from wayland_computer_use_mcp.core.context import get_active_context

    ctx = get_active_context()
    if ctx and ctx.atspi_bus_address:
        return ctx.atspi_bus_address
    if os.environ.get("AT_SPI_BUS_ADDRESS") and not (ctx and ctx.is_isolated):
        return os.environ["AT_SPI_BUS_ADDRESS"]
    if _cached_bus_address and not force_refresh:
        return _cached_bus_address

    try:
        if ctx and ctx.dbus_address:
            session_bus = await MessageBus(bus_address=ctx.dbus_address).connect()
        else:
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

            # Enable toolkit accessibility only (ScreenReaderEnabled launches Orca on GNOME)
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
            _cached_bus_address = str(reply.body[0])
            return _cached_bus_address
    except Exception as exc:
        logger.debug("Failed to query org.a11y.Bus: %s", exc)

    uid = os.getuid()
    runtime_dir = (
        (ctx.xdg_runtime_dir if ctx and ctx.xdg_runtime_dir else None)
        or os.environ.get("XDG_RUNTIME_DIR")
        or f"/run/user/{uid}"
    )
    fallback_socket = os.path.join(runtime_dir, "at-spi", "bus_0")
    if os.path.exists(fallback_socket):
        _cached_bus_address = f"unix:path={fallback_socket}"
        return _cached_bus_address

    return None


class AtspiInspector:
    """Async inspector for AT-SPI2 accessibility tree with D-Bus connection pooling."""

    def __init__(self, bus_address: str | None = None) -> None:
        self.bus_address = bus_address
        self.bus: MessageBus | None = None
        self._pooled = False

    async def connect(self) -> bool:
        """Connects to (or reuses a pooled connection on) the AT-SPI2 D-Bus daemon."""
        loop = asyncio.get_running_loop()
        loop_id = id(loop)

        if self.bus_address is None:
            pooled = _bus_pool.get(loop_id)
            if pooled is not None and getattr(pooled, "connected", True):
                self.bus = pooled
                self._pooled = True
                return True
            _bus_pool.pop(loop_id, None)

        if not self.bus_address:
            self.bus_address = await resolve_atspi_bus_address()

        if not self.bus_address:
            logger.debug("AT-SPI2 bus address could not be resolved.")
            return False

        try:
            self.bus = await MessageBus(bus_address=self.bus_address).connect()
            _bus_pool[loop_id] = self.bus
            self._pooled = True
            return True
        except Exception as exc:
            logger.debug("Failed to connect to AT-SPI bus at %s: %s", self.bus_address, exc)
            return False

    async def close(self) -> None:
        """Closes non-pooled bus connections (pooled connections stay warm for reuse)."""
        if self.bus and not self._pooled:
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

        def _empty_tree(reason: str) -> dict[str, Any]:
            return {
                "role": "application",
                "name": f"PID-{pid}",
                "states": [],
                "bounds": [0, 0, 0, 0],
                "children": [],
                "atspi_available": False,
                "reason": reason,
            }

        connected = await self.connect()
        if not connected or not self.bus:
            logger.debug("AT-SPI unavailable for PID %s", pid)
            return _empty_tree("AT-SPI bus unavailable")

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
                return _empty_tree("Empty AT-SPI registry")

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
                logger.debug("No accessible AT-SPI application matched PID %s", pid)
                return _empty_tree(f"PID {pid} has not exposed an AT-SPI tree yet")

            cached_tree = await self._fetch_tree_from_cache(
                matched_app[0], matched_app[1], max_depth=max_depth
            )
            if cached_tree is not None and (cached_tree.get("children") or max_depth <= 0):
                return cached_tree

            visited: set[tuple[str, str]] = set()
            sem = asyncio.Semaphore(16)
            tree = await self._traverse_node(
                matched_app[0],
                matched_app[1],
                current_depth=0,
                max_depth=max_depth,
                visited=visited,
                sem=sem,
            )
            pruned = self._prune_tree(tree)
            return pruned or tree

        except Exception as exc:
            logger.debug("Error traversing AT-SPI tree for PID %s: %s", pid, exc)
            return _empty_tree(f"AT-SPI traversal error: {exc}")
        finally:
            await self.close()

    async def _fetch_tree_from_cache(
        self,
        bus_name: str,
        root_path: str,
        max_depth: int,
    ) -> dict[str, Any] | None:
        """Fast path fetching all subtree metadata in 1 D-Bus call via Cache.GetItems."""
        if not self.bus:
            return None
        try:
            reply = await self.bus.call(
                Message(
                    destination=bus_name,
                    path="/org/a11y/atspi/cache",
                    interface="org.a11y.atspi.Cache",
                    member="GetItems",
                )
            )
        except Exception:
            return None

        if not reply or reply.message_type == MessageType.ERROR or not reply.body:
            return None
        items = reply.body[0]
        if not isinstance(items, (list, tuple)) or not items:
            return None

        nodes_by_key: dict[tuple[str, str], dict[str, Any]] = {}
        ifaces_by_key: dict[tuple[str, str], set[str]] = {}
        declared_children: dict[tuple[str, str], int] = {}
        children_by_parent: dict[tuple[str, str], list[tuple[int, int, tuple[str, str]]]] = {}
        unknown_roles: list[tuple[int, str, str]] = []

        for order, item in enumerate(items):
            if not isinstance(item, (list, tuple)) or len(item) < 10:
                continue
            (
                self_ref,
                _,
                parent_ref,
                idx_in_parent,
                child_count,
                ifaces,
                name,
                role_id,
                _,
                raw_states,
            ) = item
            if not isinstance(self_ref, (list, tuple)) or len(self_ref) < 2:
                continue
            n_bus, n_path = str(self_ref[0]), str(self_ref[1])
            key = (n_bus, n_path)
            rid = int(role_id)
            role = ATSPI_ROLE_ID_MAP.get(rid) or _dynamic_role_names.get(rid)
            if role is None:
                unknown_roles.append((rid, n_bus, n_path))
                role = "unknown"

            nodes_by_key[key] = {
                "bus_name": n_bus,
                "path": n_path,
                "role": role,
                "name": str(name or ""),
                "states": self._decode_states(raw_states),
                "bounds": [0, 0, 0, 0],
                "children": [],
                "index_in_parent": int(idx_in_parent),
            }
            ifaces_by_key[key] = {str(i) for i in (ifaces or [])}
            declared_children[key] = max(0, int(child_count))

            if isinstance(parent_ref, (list, tuple)) and len(parent_ref) >= 2:
                p_bus, p_path = str(parent_ref[0]), str(parent_ref[1])
                if p_path and p_path != "/org/a11y/atspi/null":
                    children_by_parent.setdefault((p_bus, p_path), []).append(
                        (int(idx_in_parent), order, key)
                    )

        # Resolve any toolkit-specific custom role IDs once and cache globally
        seen_rids: set[int] = set()
        for rid, r_bus, r_path in unknown_roles:
            if rid in seen_rids or rid in _dynamic_role_names:
                continue
            seen_rids.add(rid)
            try:
                r_rep = await self.bus.call(
                    Message(
                        destination=r_bus,
                        path=r_path,
                        interface="org.a11y.atspi.Accessible",
                        member="GetRoleName",
                    )
                )
                if r_rep and r_rep.message_type != MessageType.ERROR and r_rep.body:
                    _dynamic_role_names[rid] = str(r_rep.body[0]).lower()
            except Exception:
                pass
        if seen_rids:
            for key, node in nodes_by_key.items():
                if node["role"] == "unknown":
                    for rid, r_bus, r_path in unknown_roles:
                        if (r_bus, r_path) == key and rid in _dynamic_role_names:
                            node["role"] = _dynamic_role_names[rid]

        root_key = (bus_name, root_path)
        if root_key not in nodes_by_key:
            return None

        visited: set[tuple[str, str]] = set()

        def _assemble(cur_key: tuple[str, str], depth: int) -> tuple[dict[str, Any] | None, bool]:
            if depth > max_depth or cur_key in visited:
                return None, False
            node = nodes_by_key.get(cur_key)
            if node is None:
                return None, False
            visited.add(cur_key)
            if depth < max_depth:
                child_entries = sorted(
                    children_by_parent.get(cur_key, []),
                    key=lambda x: (x[0], x[1]),
                )
                states = node.get("states", [])
                is_visible = "visible" in states or "showing" in states or not states
                if is_visible and declared_children.get(cur_key, 0) > len(child_entries):
                    return None, True
                for _, _, c_key in child_entries:
                    child, incomplete = _assemble(c_key, depth + 1)
                    if incomplete:
                        return None, True
                    if child is not None:
                        node["children"].append(child)
            return node, False

        raw_tree, incomplete = _assemble(root_key, 0)
        if raw_tree is None or incomplete:
            return None
        pruned = self._prune_tree(raw_tree) or raw_tree

        # Batch Component extents & Value only on surviving pruned nodes
        need_win_ext: list[dict[str, Any]] = []
        need_scr_ext: list[dict[str, Any]] = []
        need_val: list[dict[str, Any]] = []

        def _collect(node: dict[str, Any], depth: int) -> None:
            key = (str(node.get("bus_name", "")), str(node.get("path", "")))
            ifaces = ifaces_by_key.get(key, set())
            role = str(node.get("role", "")).lower()
            has_comp = "org.a11y.atspi.Component" in ifaces
            if has_comp and (
                role in INTERACTIVE_ROLES
                or role in ("label", "static", "window", "frame", "dialog", "tab panel")
            ):
                need_win_ext.append(node)
            if has_comp and (depth == 0 or role in ("window", "frame", "dialog")):
                need_scr_ext.append(node)
            if "org.a11y.atspi.Value" in ifaces and role in (
                "scale",
                "slider",
                "spin button",
                "progress bar",
                "progressbar",
            ):
                need_val.append(node)
            for child in node.get("children", []):
                _collect(child, depth + 1)

        _collect(pruned, 0)

        sem = asyncio.Semaphore(16)

        async def _call_bounded(msg: Message) -> Any:
            async with sem:
                return await self.bus.call(msg)  # type: ignore[union-attr]

        tasks: list[Any] = []
        targets: list[tuple[str, dict[str, Any]]] = []
        for node in need_win_ext:
            targets.append(("win", node))
            tasks.append(
                _call_bounded(
                    Message(
                        destination=node["bus_name"],
                        path=node["path"],
                        interface="org.a11y.atspi.Component",
                        member="GetExtents",
                        signature="u",
                        body=[1],
                    )
                )
            )
        for node in need_scr_ext:
            targets.append(("scr", node))
            tasks.append(
                _call_bounded(
                    Message(
                        destination=node["bus_name"],
                        path=node["path"],
                        interface="org.a11y.atspi.Component",
                        member="GetExtents",
                        signature="u",
                        body=[0],
                    )
                )
            )
        for node in need_val:
            targets.append(("val", node))
            tasks.append(
                _call_bounded(
                    Message(
                        destination=node["bus_name"],
                        path=node["path"],
                        interface="org.a11y.atspi.Value",
                        member="GetCurrentValue",
                    )
                )
            )

        if tasks:
            replies = await asyncio.gather(*tasks, return_exceptions=True)
            for (kind, node), rep in zip(targets, replies, strict=True):
                if (
                    isinstance(rep, BaseException)
                    or rep is None
                    or getattr(rep, "message_type", None) == MessageType.ERROR
                    or not getattr(rep, "body", None)
                ):
                    continue
                if kind == "win":
                    b = rep.body[0]
                    if isinstance(b, (list, tuple)) and len(b) >= 4:
                        node["bounds"] = [int(b[0]), int(b[1]), int(b[2]), int(b[3])]
                elif kind == "scr":
                    sb = rep.body[0]
                    if isinstance(sb, (list, tuple)) and len(sb) >= 4:
                        node["screen_bounds"] = [int(sb[0]), int(sb[1]), int(sb[2]), int(sb[3])]
                elif kind == "val":
                    node["value"] = float(rep.body[0])

        return pruned

    async def _traverse_node(
        self,
        bus_name: str,
        path: str,
        current_depth: int,
        max_depth: int,
        visited: set[tuple[str, str]] | None = None,
        sem: asyncio.Semaphore | None = None,
    ) -> dict[str, Any]:
        """Recursively inspects an AT-SPI accessible node with bounded concurrency."""
        if not self.bus or current_depth > max_depth:
            return {}

        if visited is None:
            visited = set()
        node_key = (bus_name, path)
        if node_key in visited:
            return {}
        visited.add(node_key)

        node: dict[str, Any] = {
            "bus_name": bus_name,
            "path": path,
            "role": "unknown",
            "name": "",
            "states": [],
            "bounds": [0, 0, 0, 0],
            "children": [],
        }

        async def _call_bounded(msg: Message) -> Any:
            if sem is None:
                return await self.bus.call(msg)  # type: ignore[union-attr]
            async with sem:
                return await self.bus.call(msg)  # type: ignore[union-attr]

        try:
            calls = [
                _call_bounded(
                    Message(
                        destination=bus_name,
                        path=path,
                        interface="org.freedesktop.DBus.Properties",
                        member="Get",
                        signature="ss",
                        body=["org.a11y.atspi.Accessible", "Name"],
                    )
                ),
                _call_bounded(
                    Message(
                        destination=bus_name,
                        path=path,
                        interface="org.a11y.atspi.Accessible",
                        member="GetRoleName",
                    )
                ),
                _call_bounded(
                    Message(
                        destination=bus_name,
                        path=path,
                        interface="org.a11y.atspi.Accessible",
                        member="GetState",
                    )
                ),
                _call_bounded(
                    Message(
                        destination=bus_name,
                        path=path,
                        interface="org.a11y.atspi.Component",
                        member="GetExtents",
                        signature="u",
                        body=[1],  # 1 = ATSPI_COORD_TYPE_WINDOW
                    )
                ),
            ]
            if current_depth < max_depth:
                calls.append(
                    _call_bounded(
                        Message(
                            destination=bus_name,
                            path=path,
                            interface="org.a11y.atspi.Accessible",
                            member="GetChildren",
                        )
                    )
                )

            results = await asyncio.gather(*calls, return_exceptions=True)

            def _ok(rep: Any) -> bool:
                return (
                    not isinstance(rep, BaseException)
                    and rep is not None
                    and getattr(rep, "message_type", None) != MessageType.ERROR
                    and bool(getattr(rep, "body", None))
                )

            name_reply = results[0]
            if _ok(name_reply):
                val = name_reply.body[0]
                node["name"] = str(val.value if hasattr(val, "value") else val)

            role_reply = results[1]
            if _ok(role_reply):
                node["role"] = str(role_reply.body[0]).lower()

            state_reply = results[2]
            if _ok(state_reply):
                node["states"] = self._decode_states(state_reply.body[0])

            extents_reply = results[3]
            if _ok(extents_reply):
                b = extents_reply.body[0]
                if isinstance(b, (list, tuple)) and len(b) >= 4:
                    node["bounds"] = [int(b[0]), int(b[1]), int(b[2]), int(b[3])]

            extra_calls: list[Any] = []
            want_screen = current_depth == 0 or node["role"] in ("frame", "window", "dialog")
            want_val = node["role"] in ("scale", "slider", "spin button", "progressbar")
            if want_screen:
                extra_calls.append(
                    _call_bounded(
                        Message(
                            destination=bus_name,
                            path=path,
                            interface="org.a11y.atspi.Component",
                            member="GetExtents",
                            signature="u",
                            body=[0],  # 0: ATSPI_COORD_TYPE_SCREEN
                        )
                    )
                )
            if want_val:
                extra_calls.append(
                    _call_bounded(
                        Message(
                            destination=bus_name,
                            path=path,
                            interface="org.a11y.atspi.Value",
                            member="GetCurrentValue",
                        )
                    )
                )
            if extra_calls:
                extra_res = await asyncio.gather(*extra_calls, return_exceptions=True)
                e_idx = 0
                if want_screen:
                    screen_reply = extra_res[e_idx]
                    e_idx += 1
                    if _ok(screen_reply):
                        sb = screen_reply.body[0]
                        if isinstance(sb, (list, tuple)) and len(sb) >= 4:
                            node["screen_bounds"] = [
                                int(sb[0]),
                                int(sb[1]),
                                int(sb[2]),
                                int(sb[3]),
                            ]
                if want_val:
                    val_reply = extra_res[e_idx]
                    if _ok(val_reply):
                        node["value"] = float(val_reply.body[0])

            # Traverse children concurrently while preserving order and index_in_parent
            if current_depth < max_depth and len(results) > 4:
                children_reply = results[4]
                if _ok(children_reply):
                    child_refs = children_reply.body[0]
                    child_results = await asyncio.gather(
                        *[
                            self._traverse_node(
                                c_ref[0],
                                c_ref[1],
                                current_depth + 1,
                                max_depth,
                                visited=visited,
                                sem=sem,
                            )
                            for c_ref in child_refs
                        ],
                        return_exceptions=True,
                    )
                    for idx, child_node in enumerate(child_results):
                        if isinstance(child_node, dict) and child_node:
                            child_node["index_in_parent"] = idx
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
