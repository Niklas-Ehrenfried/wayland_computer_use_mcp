"""Security module: Boundary clamping and secure token store.

Provides:
- CoordinateClamper: strict window coordinate validation against current dimensions.
- TokenStore: securely stores and retrieves XDG portal session restoration tokens
  with 0700/0600 permissions.
"""

from __future__ import annotations

import json
import os
import stat
import threading
import time
from pathlib import Path

from wayland_computer_use_mcp.config import get_config


class CoordinateClamper:
    """Clamps and validates input injection coordinates to surface dimensions (W, H)."""

    def __init__(self, width: int = 1920, height: int = 1080) -> None:
        self._lock = threading.Lock()
        self._width = max(1, width)
        self._height = max(1, height)

    @property
    def dimensions(self) -> tuple[int, int]:
        with self._lock:
            return self._width, self._height

    def set_bounds(self, width: int, height: int) -> None:
        """Update window bounds when a new video frame is captured."""
        if width <= 0 or height <= 0:
            raise ValueError(f"Invalid window dimensions: {width}x{height}")
        with self._lock:
            self._width = width
            self._height = height

    def validate_and_clamp(self, x: int, y: int) -> tuple[int, int]:
        """Validate that (x, y) resides within window bounds [0, W] and [0, H].

        Raises ValueError if coordinates fall outside surface boundaries.
        Returns validated (x, y).
        """
        with self._lock:
            w = self._width
            h = self._height

        if x < 0 or y < 0 or x > w or y > h:
            raise ValueError(f"Coordinate ({x}, {y}) out of window bounds ({w}x{h})")
        return x, y


class TokenStore:
    """Thread-safe, secure storage for XDG Desktop Portal restore tokens.

    Ensures parent directory has 0700 permissions and tokens.json has 0600 permissions.
    """

    def __init__(self, storage_path: Path | None = None) -> None:
        self._lock = threading.Lock()
        if storage_path is not None:
            self._path = Path(storage_path)
        else:
            cfg = get_config()
            self._path = cfg.token_cache_dir / "tokens.json"
        self._ensure_secure_dir()

    @property
    def path(self) -> Path:
        return self._path

    def _ensure_secure_dir(self) -> None:
        parent = self._path.parent
        if not parent.exists():
            parent.mkdir(parents=True, exist_ok=True)
        # Ensure 0700 permissions on directory
        try:
            os.chmod(parent, stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR)
        except OSError:
            pass

    def _load_tokens(self) -> dict[str, str]:
        self._ensure_secure_dir()
        if not self._path.exists():
            return {}
        try:
            with open(self._path, "r", encoding="utf-8") as f:
                data = json.load(f)
                return data if isinstance(data, dict) else {}
        except Exception:
            return {}

    def get_token(self, app_id: str) -> str | None:
        """Retrieve stored restore token for an app_id."""
        with self._lock:
            tokens = self._load_tokens()
            return tokens.get(app_id)

    def set_token(self, app_id: str, token: str) -> None:
        """Persist a restore token for an app_id securely with 0600 file permissions."""
        with self._lock:
            tokens = self._load_tokens()
            tokens[app_id] = token
            self._ensure_secure_dir()

            # Write file with 0600 permissions
            content = json.dumps(tokens, indent=2)
            flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
            mode = stat.S_IRUSR | stat.S_IWUSR  # 0600
            fd = os.open(self._path, flags, mode)
            with open(fd, "w", encoding="utf-8") as f:
                f.write(content)
            try:
                os.chmod(self._path, stat.S_IRUSR | stat.S_IWUSR)
            except OSError:
                pass


class SystemShortcutFilter:
    """Enforces strict isolation by blocking system-level and desktop-wide shortcuts.

    Prohibits:
    - Any combo containing Super/Meta/Win (prevents accessing desktop shell/launchers).
    - Any combo containing both Ctrl and Alt (prevents VT switching, lock screen, terminal spawn).
    - Dangerous hardware keys (SysRq, PrintScreen, Power, Sleep).
    """

    PROHIBITED_MODIFIERS = {
        "super",
        "meta",
        "win",
        "windows",
        "hyper",
        "cmd",
        "command",
    }

    PROHIBITED_KEYS = {
        "sysrq",
        "printscreen",
        "prtsc",
        "power",
        "sleep",
        "wake",
        "pause",
        "break",
    }

    # Map friendly key names to evdev keycodes
    KEY_MAP: dict[str, int] = {
        # Modifiers
        "ctrl": 29,
        "control": 29,
        "leftctrl": 29,
        "rightctrl": 97,
        "shift": 42,
        "leftshift": 42,
        "rightshift": 54,
        "alt": 56,
        "leftalt": 56,
        "rightalt": 100,
        "altgr": 100,
        # Standard keys
        "enter": 28,
        "return": 28,
        "tab": 15,
        "space": 57,
        "backspace": 14,
        "escape": 1,
        "esc": 1,
        "delete": 111,
        "del": 111,
        "insert": 110,
        # Navigation
        "up": 103,
        "down": 108,
        "left": 105,
        "right": 106,
        "pageup": 104,
        "pagedown": 109,
        "home": 102,
        "end": 107,
        # Alphabet
        "a": 30,
        "b": 48,
        "c": 46,
        "d": 32,
        "e": 18,
        "f": 33,
        "g": 34,
        "h": 35,
        "i": 23,
        "j": 36,
        "k": 37,
        "l": 38,
        "m": 50,
        "n": 49,
        "o": 24,
        "p": 25,
        "q": 16,
        "r": 19,
        "s": 31,
        "t": 20,
        "u": 22,
        "v": 47,
        "w": 17,
        "x": 45,
        "y": 21,
        "z": 44,
        # Numbers
        "0": 11,
        "1": 2,
        "2": 3,
        "3": 4,
        "4": 5,
        "5": 6,
        "6": 7,
        "7": 8,
        "8": 9,
        "9": 10,
        # Function keys
        "f1": 59,
        "f2": 60,
        "f3": 61,
        "f4": 62,
        "f5": 63,
        "f6": 64,
        "f7": 65,
        "f8": 66,
        "f9": 67,
        "f10": 68,
        "f11": 87,
        "f12": 88,
    }

    @classmethod
    def validate_and_resolve(cls, keys: list[str]) -> list[int]:
        """Validates key list against security rules and returns evdev keycodes.

        Raises:
            PermissionError: If shortcut targets system-level controls or Super/Meta keys.
            ValueError: If an unrecognized key name is supplied.
        """
        normalized = [k.strip().lower() for k in keys if k.strip()]
        if not normalized:
            raise ValueError("No keys specified in key combination.")

        # 1. Prohibit Super / Meta / Win
        for k in normalized:
            if k in cls.PROHIBITED_MODIFIERS:
                raise PermissionError(
                    f"Shortcut '{'+'.join(keys)}' is blocked: "
                    "Super/Meta/Win keys are prohibited to prevent escaping window isolation."
                )

        # 2. Prohibit Ctrl+Alt combinations
        has_ctrl = any(k in ("ctrl", "control", "leftctrl", "rightctrl") for k in normalized)
        has_alt = any(k in ("alt", "leftalt", "rightalt", "altgr") for k in normalized)
        if has_ctrl and has_alt:
            raise PermissionError(
                f"Shortcut '{'+'.join(keys)}' is blocked: "
                "Ctrl+Alt combinations are prohibited to prevent desktop/VT switching."
            )

        # 3. Prohibit dangerous hardware/system keys
        for k in normalized:
            if k in cls.PROHIBITED_KEYS:
                raise PermissionError(f"Key '{k}' is prohibited for system stability.")

        # 4. Resolve to evdev keycodes
        keycodes = []
        for k in normalized:
            if k in cls.KEY_MAP:
                keycodes.append(cls.KEY_MAP[k])
            else:
                raise ValueError(
                    f"Unrecognized key '{k}'. Supported keys: alphanumeric, navigation, "
                    "ctrl, shift, alt, tab, enter, escape, and f1-f12."
                )

        return keycodes


class GeometryDivergenceDetector:
    """Detects unexpected window movements or resizes by the user during agent operations."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.last_dimensions: tuple[int, int] | None = None
        self.last_position: tuple[int, int] | None = None
        self.last_capture_time: float = 0.0

    def record_capture(
        self, width: int, height: int, x: int | None = None, y: int | None = None
    ) -> None:
        """Called upon each frame capture to sync baseline dimensions and position."""
        with self._lock:
            self.last_dimensions = (width, height)
            if x is not None and y is not None:
                self.last_position = (x, y)
            self.last_capture_time = time.time()

    def validate_stability(
        self,
        current_width: int,
        current_height: int,
        current_x: int | None = None,
        current_y: int | None = None,
    ) -> None:
        """Validates that current surface dimensions and position match baseline.

        Raises RuntimeError if geometry drifted since last capture.
        """
        with self._lock:
            baseline_dim = self.last_dimensions
            baseline_pos = self.last_position

        if baseline_dim is not None and (current_width, current_height) != baseline_dim:
            old_w, old_h = baseline_dim
            raise RuntimeError(
                f"Window geometry changed from ({old_w}x{old_h}) to "
                f"({current_width}x{current_height}) while action was pending. "
                "The action was aborted for safety. "
                "Please call capture_window_frame to inspect the updated layout."
            )

        if (
            baseline_pos is not None
            and current_x is not None
            and current_y is not None
            and (current_x, current_y) != baseline_pos
        ):
            old_x, old_y = baseline_pos
            raise RuntimeError(
                f"Window geometry changed (moved from ({old_x}, {old_y}) to "
                f"({current_x}, {current_y})) while action was pending. "
                "The action was aborted for safety. "
                "Please call capture_window_frame to inspect the updated layout."
            )


class UserPreemptionManager:
    """Enforces user input priority: waits for user physical input to settle before proceeding.

    If user physical input was recently detected or paused:
    Waits (sleeps) up to max_wait_seconds (default 10s) until the user
    finishes and cooldown expires.
    Only if the user continues interacting past the timeout is a RuntimeError raised.
    """

    def __init__(self, cooldown_seconds: float = 1.5, max_wait_seconds: float = 10.0) -> None:
        self._lock = threading.Lock()
        self.last_user_activity: float = 0.0
        self.cooldown_seconds = cooldown_seconds
        self.max_wait_seconds = max_wait_seconds
        self.is_paused = False

    def record_user_activity(self) -> None:
        """Records timestamp of physical user interaction."""
        with self._lock:
            self.last_user_activity = time.time()

    def set_paused(self, paused: bool) -> None:
        with self._lock:
            self.is_paused = paused

    def wait_for_user_idle(self, max_wait: float | None = None) -> None:
        """Waits until user physical interaction settles and cooldown expires."""
        cfg = get_config()
        if cfg.display_mode == "virtual":
            return

        timeout = max_wait if max_wait is not None else self.max_wait_seconds
        deadline = time.time() + timeout

        while time.time() < deadline:
            with self._lock:
                paused = self.is_paused
                last_act = self.last_user_activity
                cooldown = self.cooldown_seconds

            if not paused and (time.time() - last_act >= cooldown):
                return  # User has settled and is idle

            time.sleep(0.05)

        with self._lock:
            paused = self.is_paused

        if paused:
            raise RuntimeError(
                "Agent actions are currently paused by user. Please wait until resumed."
            )

        raise RuntimeError(
            f"User physical input ongoing (timeout {timeout:.1f}s exceeded): "
            "agent action postponed to preserve user control. "
            "Please call capture_window_frame when user finishes."
        )

    def check_preemption(self, max_wait: float | None = None) -> None:
        """Enforces preemption: waits for user interaction to settle before proceeding."""
        self.wait_for_user_idle(max_wait=max_wait)


# Global singleton instances
global_clamper = CoordinateClamper()
global_token_store = TokenStore()
global_shortcut_filter = SystemShortcutFilter()
global_geometry_detector = GeometryDivergenceDetector()
global_preemption_manager = UserPreemptionManager()
