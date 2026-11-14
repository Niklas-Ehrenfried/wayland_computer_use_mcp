"""Accessible Action Execution (AT-SPI DoAction, EditableText, Text)."""

from __future__ import annotations

import logging
import time
from typing import Any

from dbus_fast import Message, MessageType, Variant

from wayland_computer_use_mcp.a11y.client import AtspiInspector
from wayland_computer_use_mcp.a11y.client import run_atspi_sync as _run_async_sync
from wayland_computer_use_mcp.a11y.tree import get_application_tree, get_cached_node

logger = logging.getLogger(__name__)


async def perform_accessible_action(bus_name: str, path: str, action_index: int = 0) -> bool:
    """Invokes org.a11y.atspi.Action.DoAction on an accessible object."""
    inspector = AtspiInspector()
    connected = await inspector.connect()
    if not connected or not inspector.bus:
        return False
    try:
        # Check action name - don't treat scroll actions as click/activation
        try:
            name_rep = await inspector.bus.call(
                Message(
                    destination=bus_name,
                    path=path,
                    interface="org.a11y.atspi.Action",
                    member="GetName",
                    signature="i",
                    body=[action_index],
                )
            )
            if name_rep and name_rep.body:
                act_name = str(name_rep.body[0]).lower()
                if "scroll" in act_name:
                    logger.debug("Action '%s' is a scroll action, skipping activation.", act_name)
                    return False
        except Exception:
            pass

        reply = await inspector.bus.call(
            Message(
                destination=bus_name,
                path=path,
                interface="org.a11y.atspi.Action",
                member="DoAction",
                signature="i",
                body=[action_index],
            )
        )
        if not reply or reply.message_type == MessageType.ERROR:
            return False
        return bool(reply.body and reply.body[0])
    except Exception as exc:
        logger.debug("Failed DoAction on %s %s: %s", bus_name, path, exc)
        return False
    finally:
        await inspector.close()


def do_accessible_action(bus_name: str, path: str, action_index: int = 0) -> bool:
    """Synchronous entry point for perform_accessible_action."""
    try:
        return bool(_run_async_sync(perform_accessible_action, bus_name, path, action_index))
    except Exception as exc:
        logger.debug("do_accessible_action error: %s", exc)
        return False


async def perform_accessible_set_text(bus_name: str, path: str, text: str) -> bool:
    """Invokes org.a11y.atspi.EditableText.SetTextContents on an accessible object."""
    inspector = AtspiInspector()
    connected = await inspector.connect()
    if not connected or not inspector.bus:
        return False
    try:
        reply = await inspector.bus.call(
            Message(
                destination=bus_name,
                path=path,
                interface="org.a11y.atspi.EditableText",
                member="SetTextContents",
                signature="s",
                body=[text],
            )
        )
        if not reply or reply.message_type == MessageType.ERROR:
            return False
        return bool(reply.body and reply.body[0])
    except Exception as exc:
        logger.debug("Failed SetTextContents on %s %s: %s", bus_name, path, exc)
        return False
    finally:
        await inspector.close()


def do_accessible_set_text(bus_name: str, path: str, text: str) -> bool:
    """Synchronous entry point for perform_accessible_set_text."""
    try:
        return bool(_run_async_sync(perform_accessible_set_text, bus_name, path, text))
    except Exception as exc:
        logger.debug("do_accessible_set_text error: %s", exc)
        return False


async def perform_accessible_set_value(bus_name: str, path: str, value: float) -> bool:
    """Sets numerical value on a range/slider widget via org.a11y.atspi.Value CurrentValue."""
    inspector = AtspiInspector()
    connected = await inspector.connect()
    if not connected or not inspector.bus:
        return False
    try:
        reply = await inspector.bus.call(
            Message(
                destination=bus_name,
                path=path,
                interface="org.freedesktop.DBus.Properties",
                member="Set",
                signature="ssv",
                body=["org.a11y.atspi.Value", "CurrentValue", Variant("d", float(value))],
            )
        )
        if not reply or reply.message_type == MessageType.ERROR:
            return False
        return True
    except Exception as exc:
        logger.debug("perform_accessible_set_value error: %s", exc)
        return False
    finally:
        await inspector.close()


def do_accessible_set_value(bus_name: str, path: str, value: float) -> bool:
    """Synchronous entry point for perform_accessible_set_value."""
    try:
        return bool(_run_async_sync(perform_accessible_set_value, bus_name, path, value))
    except Exception as exc:
        logger.debug("do_accessible_set_value error: %s", exc)
        return False


async def perform_accessible_select_child(bus_name: str, path: str, child_index: int) -> bool:
    """Selects a child item in a container (list, table) via org.a11y.atspi.Selection."""
    inspector = AtspiInspector()
    connected = await inspector.connect()
    if not connected or not inspector.bus:
        return False
    try:
        reply = await inspector.bus.call(
            Message(
                destination=bus_name,
                path=path,
                interface="org.a11y.atspi.Selection",
                member="SelectChild",
                signature="i",
                body=[int(child_index)],
            )
        )
        if not reply or reply.message_type == MessageType.ERROR:
            return False
        return bool(reply.body and reply.body[0])
    except Exception as exc:
        logger.debug("perform_accessible_select_child error: %s", exc)
        return False
    finally:
        await inspector.close()


def do_accessible_select_child(bus_name: str, path: str, child_index: int) -> bool:
    """Synchronous entry point for perform_accessible_select_child."""
    try:
        return bool(_run_async_sync(perform_accessible_select_child, bus_name, path, child_index))
    except Exception as exc:
        logger.debug("do_accessible_select_child error: %s", exc)
        return False


async def perform_accessible_text_action(
    bus_name: str,
    path: str,
    action: str,
    start_offset: int = 0,
    end_offset: int = -1,
    position: int = 0,
) -> bool:
    """Performs semantic text operations (select, copy, cut, paste) via AT-SPI."""
    inspector = AtspiInspector()
    connected = await inspector.connect()
    if not connected or not inspector.bus:
        return False
    try:
        act = action.strip().lower()
        if act == "select_all":
            reply = await inspector.bus.call(
                Message(
                    destination=bus_name,
                    path=path,
                    interface="org.a11y.atspi.Text",
                    member="SetSelection",
                    signature="iii",
                    body=[0, 0, -1],
                )
            )
            if not reply or reply.message_type == MessageType.ERROR:
                return False
            return bool(reply.body and reply.body[0])
        elif act in ("select_range", "select"):
            reply = await inspector.bus.call(
                Message(
                    destination=bus_name,
                    path=path,
                    interface="org.a11y.atspi.Text",
                    member="SetSelection",
                    signature="iii",
                    body=[0, start_offset, end_offset],
                )
            )
            if not reply or reply.message_type == MessageType.ERROR:
                return False
            return bool(reply.body and reply.body[0])
        elif act == "copy":
            reply = await inspector.bus.call(
                Message(
                    destination=bus_name,
                    path=path,
                    interface="org.a11y.atspi.EditableText",
                    member="CopyText",
                    signature="ii",
                    body=[start_offset, end_offset],
                )
            )
            if not reply or reply.message_type == MessageType.ERROR:
                return False
            return True
        elif act == "cut":
            reply = await inspector.bus.call(
                Message(
                    destination=bus_name,
                    path=path,
                    interface="org.a11y.atspi.EditableText",
                    member="CutText",
                    signature="ii",
                    body=[start_offset, end_offset],
                )
            )
            if not reply or reply.message_type == MessageType.ERROR:
                return False
            return bool(reply.body and reply.body[0])
        elif act == "paste":
            reply = await inspector.bus.call(
                Message(
                    destination=bus_name,
                    path=path,
                    interface="org.a11y.atspi.EditableText",
                    member="PasteText",
                    signature="i",
                    body=[position],
                )
            )
            if not reply or reply.message_type == MessageType.ERROR:
                return False
            return bool(reply.body and reply.body[0])
        return False
    except Exception as exc:
        logger.debug("AT-SPI text action '%s' failed on %s %s: %s", action, bus_name, path, exc)
        return False
    finally:
        await inspector.close()


def do_accessible_text_action(
    bus_name: str,
    path: str,
    action: str,
    start_offset: int = 0,
    end_offset: int = -1,
    position: int = 0,
) -> bool:
    """Synchronous entry point for perform_accessible_text_action."""
    try:
        return bool(
            _run_async_sync(
                perform_accessible_text_action,
                bus_name,
                path,
                action,
                start_offset=start_offset,
                end_offset=end_offset,
                position=position,
            )
        )
    except Exception as exc:
        logger.debug("do_accessible_text_action error: %s", exc)
        return False


def _prepare_pointer_target(pid: int, target_x: int, target_y: int) -> tuple[int, int]:
    """Focuses window and auto-scrolls element into viewport ONLY for Level 2 pointer actions."""
    from wayland_computer_use_mcp.portal import global_portal_session
    from wayland_computer_use_mcp.security import global_clamper

    if pid > 0 and not global_portal_session.is_mock:
        try:
            from wayland_computer_use_mcp.tools.visual_tools import focus_window

            prev_offsets = (
                global_portal_session.window_offset_x,
                global_portal_session.window_offset_y,
            )
            focus_window(pid)
            new_offsets = (
                global_portal_session.window_offset_x,
                global_portal_session.window_offset_y,
            )
            # Allow compositor surface activation/placement to settle before clicking
            if new_offsets != prev_offsets or new_offsets != (0, 0):
                time.sleep(0.05)
        except Exception:
            pass

    w, h = global_clamper.dimensions
    if w > 0 and h > 0:
        if target_y > h - 40:
            scroll_dy = target_y - (h // 2)
            try:
                global_portal_session.dispatch_hover(
                    max(1, min(target_x, w - 1)), h // 2, duration_ms=0
                )
                time.sleep(0.01)
                global_portal_session.dispatch_scroll(0, scroll_dy)
                time.sleep(0.08)
                target_y = max(40, min(target_y - scroll_dy, h - 40))
            except Exception as exc:
                logger.debug("Auto-scroll failed: %s", exc)
        elif target_y < 40:
            scroll_dy = (h // 2) - target_y
            try:
                global_portal_session.dispatch_hover(
                    max(1, min(target_x, w - 1)), h // 2, duration_ms=0
                )
                time.sleep(0.01)
                global_portal_session.dispatch_scroll(0, -scroll_dy)
                time.sleep(0.08)
                target_y = max(40, min(target_y + scroll_dy, h - 40))
            except Exception as exc:
                logger.debug("Auto-scroll failed: %s", exc)

    cx = max(1, min(target_x, w - 1)) if w > 0 else target_x
    cy = max(1, min(target_y, h - 1)) if h > 0 else target_y
    return cx, cy


def _has_unknown_window_offset(pid: int) -> bool:
    """True when live compositor IPC cannot resolve the window's screen offset (e.g. GNOME)."""
    from wayland_computer_use_mcp.portal import global_portal_session

    if global_portal_session.is_mock or pid <= 0:
        return False
    try:
        from wayland_computer_use_mcp.compositors import query_window_geometry

        return query_window_geometry(pid=pid) is None
    except Exception:
        return False


async def _find_focused_child_index(bus_name: str, path: str) -> int:
    """Returns the index of the focused child inside a container node, or 0 if unknown."""
    inspector = AtspiInspector()
    if not await inspector.connect() or not inspector.bus:
        return 0
    try:
        rep = await inspector.bus.call(
            Message(
                destination=bus_name,
                path=path,
                interface="org.a11y.atspi.Accessible",
                member="GetChildren",
            )
        )
        children = rep.body[0] if rep and rep.body else []
        for idx, (ch_bus, ch_path) in enumerate(children):
            st_rep = await inspector.bus.call(
                Message(
                    destination=ch_bus,
                    path=ch_path,
                    interface="org.a11y.atspi.Accessible",
                    member="GetState",
                )
            )
            if st_rep and st_rep.body and "focused" in inspector._decode_states(st_rep.body[0]):
                return idx
        return 0
    except Exception:
        return 0
    finally:
        await inspector.close()


def _activate_combobox_item_via_keyboard(
    pid: int,
    node_id: str,
    name: str,
    role: str,
    container_bus: str,
    container_path: str,
    target_idx: int,
) -> str | None:
    """Navigates to target_idx inside a focused dropdown list via arrow + Return keys."""
    from wayland_computer_use_mcp.portal import global_portal_session

    try:
        focused_idx = int(_run_async_sync(_find_focused_child_index, container_bus, container_path))
        delta = target_idx - focused_idx
        key = "down" if delta > 0 else "up"
        for _ in range(abs(delta)):
            global_portal_session.dispatch_key_combination([key])
            time.sleep(0.03)
        global_portal_session.dispatch_key_combination(["return"])
        return (
            f"Selected {node_id} ('{name}' [{role}]) in dropdown "
            f"via keyboard navigation (delta={delta})."
        )
    except Exception as exc:
        logger.debug("Dropdown keyboard navigation failed: %s", exc)
        return None


def _find_actionable_child(
    node: dict[str, Any], target_roles: tuple[str, ...], max_depth: int = 2
) -> dict[str, Any] | None:
    """BFS for the first descendant matching one of target_roles with a D-Bus reference."""
    queue: list[tuple[dict[str, Any], int]] = [(ch, 1) for ch in node.get("children", [])]
    while queue:
        cur, depth = queue.pop(0)
        if cur.get("role", "").lower() in target_roles and cur.get("bus_name") and cur.get("path"):
            return cur
        if depth < max_depth:
            for ch in cur.get("children", []):
                queue.append((ch, depth + 1))
    return None


def invoke_node_action(
    pid: int,
    node_id: str,
    action: str = "click",
    text: str | None = None,
    value: float | None = None,
    start_offset: int = 0,
    end_offset: int = -1,
) -> str:
    """Interacts with an accessible widget by its compact 1D node ID.

    1. Level 1: Silent programmatic execution via AT-SPI (DoAction, EditableText,
       Value, Text) with Semantic Parent Bubbling — does not move the mouse cursor.
    2. Level 2: Only if AT-SPI fails (or a pointer action is requested), focuses window,
       scrolls into view if needed, and falls back to coordinate/keyboard input.
    """
    cached = get_cached_node(pid, node_id)
    if not cached:
        get_application_tree(pid)
        cached = get_cached_node(pid, node_id)

    if not cached:
        raise ValueError(
            f"Element with ID '{node_id}' not found in accessibility tree for PID {pid}."
        )

    center = cached.get("center", [0, 0])
    target_x, target_y = center[0], center[1]
    name = cached.get("name", "")
    role = cached.get("role", "")
    bounds = cached.get("bounds", [0, 0, 0, 0])

    from wayland_computer_use_mcp.portal import global_portal_session

    bus_name = cached.get("bus_name")
    path = cached.get("path")

    act = action.strip().lower()
    if act in ("click", "press", "activate"):
        level1_success = False
        skip_do_action = role in (
            "label",
            "static",
            "combo box",
            "combobox",
            "dropdown",
            "drop down",
        )
        if bus_name and path and not skip_do_action:
            try:
                level1_success = do_accessible_action(bus_name, path, 0)
            except Exception as exc:
                logger.debug("AT-SPI DoAction failed on %s %s: %s", bus_name, path, exc)
                level1_success = False

        if level1_success:
            return f"Activated {node_id} ('{name}' [{role}]) via AT-SPI."

        # Composite dropdowns (e.g. GTK4 Gtk.DropDown) expose NActions=0 on the outer
        # combo box while an internal toggle/push button performs the popup open action.
        if role in ("combo box", "combobox", "dropdown", "drop down"):
            child_btn = _find_actionable_child(
                cached, ("toggle button", "push button", "button"), max_depth=2
            )
            if child_btn:
                ch_bus = child_btn.get("bus_name")
                ch_path = child_btn.get("path")
                if ch_bus and ch_path:
                    try:
                        if do_accessible_action(ch_bus, ch_path, 0):
                            return (
                                f"Activated {node_id} ('{name}' [{role}]) via child "
                                f"{child_btn.get('role')} via AT-SPI."
                            )
                    except Exception as exc:
                        logger.debug(
                            "Child button DoAction failed on %s %s: %s",
                            ch_bus,
                            ch_path,
                            exc,
                        )

        # Semantic Parent Bubbling: If target lacks DoAction or failed (e.g. static
        # label or icon), walk up ancestor chain to find actionable container.
        ancestors = cached.get("ancestors", [])
        is_inside_combobox = any(
            a.get("role") in ("combo box", "combobox", "dropdown", "drop down") for a in ancestors
        )

        if (
            not is_inside_combobox
            and role in ("list item", "table row", "table cell")
            and ancestors
        ):
            parent = ancestors[0]
            if parent.get("role") in ("list", "table", "tree table", "tree"):
                p_bus = parent.get("bus_name")
                p_path = parent.get("path")
                idx = cached.get("index_in_parent", 0)
                if p_bus and p_path:
                    try:
                        if do_accessible_select_child(p_bus, p_path, idx):
                            return (
                                f"Selected {node_id} ('{name}' [{role}]) via parent container "
                                f"{parent.get('role')} SelectChild({idx}) via AT-SPI."
                            )
                    except Exception as exc:
                        logger.debug("Parent SelectChild failed on %s %s: %s", p_bus, p_path, exc)

        for i, anc in enumerate(ancestors):
            anc_role = anc.get("role", "")
            if anc_role in (
                "list item",
                "table row",
                "table cell",
                "push button",
                "button",
                "check box",
                "checkbox",
                "radio button",
                "radiobutton",
                "tree item",
                "menu item",
                "tab",
                "page tab",
            ):
                anc_bus = anc.get("bus_name")
                anc_path = anc.get("path")
                if anc_bus and anc_path:
                    try:
                        if do_accessible_action(anc_bus, anc_path, 0):
                            return (
                                f"Activated {node_id} ('{name}' [{role}]) via parent "
                                f"{anc_role} ('{anc.get('name')}') via AT-SPI."
                            )
                    except Exception as exc:
                        logger.debug(
                            "Parent bubbling DoAction failed on %s %s: %s",
                            anc_bus,
                            anc_path,
                            exc,
                        )

                    # If DoAction failed on list item, check container SelectChild
                    # (or arrow+Return keyboard fallback on GNOME with unknown offset).
                    if anc_role in ("list item", "table row") and i + 1 < len(ancestors):
                        container_anc = ancestors[i + 1]
                        if container_anc.get("role") in ("list", "table", "tree table", "tree"):
                            c_bus = container_anc.get("bus_name")
                            c_path = container_anc.get("path")
                            idx = anc.get("index_in_parent", 0)
                            if c_bus and c_path:
                                if is_inside_combobox and _has_unknown_window_offset(pid):
                                    kbd_msg = _activate_combobox_item_via_keyboard(
                                        pid, node_id, name, role, c_bus, c_path, idx
                                    )
                                    if kbd_msg:
                                        return kbd_msg
                                elif not is_inside_combobox:
                                    try:
                                        if do_accessible_select_child(c_bus, c_path, idx):
                                            return (
                                                f"Selected {node_id} ('{name}' [{role}]) via "
                                                f"container {container_anc.get('role')} "
                                                f"SelectChild({idx}) via AT-SPI."
                                            )
                                    except Exception as exc:
                                        logger.debug(
                                            "Container SelectChild failed on %s %s: %s",
                                            c_bus,
                                            c_path,
                                            exc,
                                        )

        if (
            role in ("check box", "checkbox", "toggle button", "radio button", "radiobutton")
            and "focused" in cached.get("states", [])
            and _has_unknown_window_offset(pid)
        ):
            try:
                global_portal_session.dispatch_key_combination(["space"])
                return f"Toggled focused {node_id} ('{name}' [{role}]) via spacebar."
            except Exception as exc:
                logger.debug("Focused checkbox spacebar toggle failed: %s", exc)

        cx, cy = _prepare_pointer_target(pid, target_x, target_y)
        click_msg = global_portal_session.dispatch_click(cx, cy, button="left")
        return f"{click_msg} (Fallback Level 2 for {node_id} '{name}' [{role}])"

    elif act == "type":
        if text is None:
            raise ValueError("Action 'type' requires 'text' parameter.")
        level1_success = False
        if bus_name and path:
            try:
                level1_success = do_accessible_set_text(bus_name, path, text)
            except Exception as exc:
                logger.debug("AT-SPI SetTextContents failed on %s %s: %s", bus_name, path, exc)
                level1_success = False

        if level1_success:
            return f"Set text '{text}' on {node_id} ('{name}' [{role}]) via AT-SPI EditableText."

        cx, cy = _prepare_pointer_target(pid, target_x, target_y)
        type_msg = global_portal_session.dispatch_type_text(text, x=cx, y=cy)
        return f"{type_msg} (Fallback Level 2 for {node_id} '{name}' [{role}])"

    elif act == "clear":
        level1_success = False
        if bus_name and path:
            try:
                level1_success = do_accessible_set_text(bus_name, path, "")
            except Exception as exc:
                logger.debug("SetTextContents(clear) failed on %s: %s", path, exc)
                level1_success = False

        if level1_success:
            return f"Cleared text in {node_id} ('{name}' [{role}]) via AT-SPI EditableText."

        cx, cy = _prepare_pointer_target(pid, target_x, target_y)
        global_portal_session.dispatch_click(cx, cy, button="left")
        time.sleep(0.015)
        global_portal_session.dispatch_key_combination(["ctrl", "a"])
        time.sleep(0.01)
        global_portal_session.dispatch_key_combination(["BackSpace"])
        return f"Cleared text in {node_id} via Ctrl+A + BackSpace fallback."

    elif act in ("select_all", "select"):
        level1_success = False
        if bus_name and path:
            level1_success = do_accessible_text_action(bus_name, path, "select_all")

        if level1_success:
            return f"Selected all text in {node_id} ('{name}' [{role}]) via AT-SPI Text."

        cx, cy = _prepare_pointer_target(pid, target_x, target_y)
        global_portal_session.dispatch_click(cx, cy, button="left")
        time.sleep(0.015)
        global_portal_session.dispatch_key_combination(["ctrl", "a"])
        return f"Selected all text in {node_id} via Ctrl+A fallback."

    elif act in ("select_range", "select_text"):
        level1_success = False
        if bus_name and path:
            level1_success = do_accessible_text_action(
                bus_name, path, "select_range", start_offset=start_offset, end_offset=end_offset
            )

        if level1_success:
            return f"Selected range [{start_offset}:{end_offset}] in {node_id} via AT-SPI Text."

        return invoke_node_action(pid, node_id, action="drag_select")

    elif act == "copy":
        level1_success = False
        if bus_name and path:
            level1_success = do_accessible_text_action(
                bus_name, path, "copy", start_offset=start_offset, end_offset=end_offset
            )

        if level1_success:
            return f"Copied text from {node_id} ('{name}' [{role}]) via AT-SPI EditableText."

        cx, cy = _prepare_pointer_target(pid, target_x, target_y)
        global_portal_session.dispatch_click(cx, cy, button="left")
        time.sleep(0.015)
        global_portal_session.dispatch_key_combination(["ctrl", "c"])
        return f"Copied text from {node_id} via Ctrl+C fallback."

    elif act == "paste":
        level1_success = False
        if bus_name and path:
            level1_success = do_accessible_text_action(
                bus_name, path, "paste", position=start_offset
            )

        if level1_success:
            return f"Pasted text into {node_id} ('{name}' [{role}]) via AT-SPI EditableText."

        cx, cy = _prepare_pointer_target(pid, target_x, target_y)
        global_portal_session.dispatch_click(cx, cy, button="left")
        time.sleep(0.015)
        global_portal_session.dispatch_key_combination(["ctrl", "v"])
        return f"Pasted text into {node_id} via Ctrl+V fallback."

    elif act == "cut":
        level1_success = False
        if bus_name and path:
            level1_success = do_accessible_text_action(
                bus_name, path, "cut", start_offset=start_offset, end_offset=end_offset
            )

        if level1_success:
            return f"Cut text from {node_id} ('{name}' [{role}]) via AT-SPI EditableText."

        cx, cy = _prepare_pointer_target(pid, target_x, target_y)
        global_portal_session.dispatch_click(cx, cy, button="left")
        time.sleep(0.015)
        global_portal_session.dispatch_key_combination(["ctrl", "x"])
        return f"Cut text from {node_id} via Ctrl+X fallback."

    elif act == "drag_select":
        cx, cy = _prepare_pointer_target(pid, target_x, target_y)
        if len(bounds) == 4 and bounds[2] > 10:
            start_x = bounds[0] + 6
            end_x = bounds[0] + bounds[2] - 6
            start_y = cy
            end_y = cy
        else:
            start_x, start_y = cx - 20, cy
            end_x, end_y = cx + 20, cy

        drag_msg = global_portal_session.dispatch_drag(start_x, start_y, end_x, end_y)
        return f"Drag-selected text in {node_id}: {drag_msg}"

    elif act in ("set_value", "value"):
        val_to_set = value if value is not None else (float(text) if text is not None else None)
        if val_to_set is None:
            raise ValueError("Action 'set_value' requires 'value' or numerical 'text' parameter.")
        level1_success = False
        if bus_name and path:
            try:
                level1_success = do_accessible_set_value(bus_name, path, float(val_to_set))
            except Exception as exc:
                logger.debug("AT-SPI SetCurrentValue failed on %s %s: %s", bus_name, path, exc)
                level1_success = False

        if level1_success:
            return f"Set value {val_to_set} on {node_id} ('{name}' [{role}]) via AT-SPI Value."

        # Fallback Level 2 for slider: interpolate drag across bounds
        _prepare_pointer_target(pid, target_x, target_y)
        if len(bounds) == 4 and bounds[2] > 0:
            sy = bounds[1] + bounds[3] // 2
            sx = bounds[0] + 4
            ex = bounds[0] + bounds[2] - 4
            drag_msg = global_portal_session.dispatch_drag(sx, sy, ex, sy)
            return f"{drag_msg} (Fallback Level 2 drag for {node_id} '{name}' [{role}])"
        return f"Failed to set value on {node_id}."

    elif act == "double_click":
        cx, cy = _prepare_pointer_target(pid, target_x, target_y)
        return global_portal_session.dispatch_double_click(cx, cy)
    elif act == "right_click":
        cx, cy = _prepare_pointer_target(pid, target_x, target_y)
        return global_portal_session.dispatch_right_click(cx, cy)
    elif act == "hover":
        cx, cy = _prepare_pointer_target(pid, target_x, target_y)
        return global_portal_session.dispatch_hover(cx, cy)
    else:
        raise ValueError(
            f"Unsupported action '{action}'. "
            "Supported actions: click, type, set_value, clear, select_all, select_range, "
            "copy, paste, cut, drag_select, double_click, right_click, hover"
        )
