from __future__ import annotations

import psutil
import os
import time


class WindowController:
    @staticmethod
    def _identity(window):
        import win32process
        pid = win32process.GetWindowThreadProcessId(window._hWnd)[1]
        process = psutil.Process(pid)
        return {"hwnd": int(window._hWnd), "pid": pid, "created": process.create_time(),
                "exe": process.exe(), "name": window.title}

    def candidates(self, query, aliases=()):
        """Read-only local window snapshots; no LLM handles or executable paths."""
        import pygetwindow as gw
        labels = {label.casefold() for label in (query, *aliases) if len(label.strip()) >= 2}
        found = []
        for window in gw.getAllWindows():
            try:
                if not window.title or not any(label in window.title.casefold() for label in labels):
                    continue
                if not (window.visible or window.isMinimized):
                    continue  # Hidden tray-only windows are not ordinary desktop windows.
                found.append(self._identity(window))
            except Exception:
                continue
            if len(found) >= 50:
                break
        return found

    ACTIONS = frozenset({"maximize", "minimize", "restore", "focus", "close"})

    def change_target(self, target, action):
        """Operate once on the approved snapshot, then observe actual window state.

        close only asks the window to close (WM_CLOSE): the program may still ask to
        save, and then the window stays open and the result is reported as unverified.
        """
        if os.name != "nt" or action not in self.ACTIONS:
            return {"accepted": False, "verified": False, "status": "unsupported"}
        import pygetwindow as gw
        try:
            window = gw.Win32Window(target["hwnd"])
            current = self._identity(window)
            if any(current[key] != target[key] for key in ("hwnd", "pid", "created", "exe", "name")):
                return {"accepted": False, "verified": False, "status": "stale"}
        except Exception:
            return {"accepted": False, "verified": False, "status": "stale"}
        try:
            self._apply(window, action)
            deadline = time.monotonic() + 2
            while True:
                verified = self._observed(window, action, target["hwnd"])
                if verified or time.monotonic() >= deadline:
                    return {"accepted": True, "verified": bool(verified),
                            "status": "verified" if verified else "unverified",
                            "foreground": self._foreground(window, action),
                            **self._tray_hint(action, verified, target["hwnd"])}
                time.sleep(.1)
        except Exception:
            # The request may have reached Windows before observation failed.
            return {"accepted": True, "verified": False, "status": "unverified"}

    @staticmethod
    def _apply(window, action):
        if action == "close":
            window.close()
            return
        if action == "focus":
            if window.isMinimized:
                window.restore()
        else:
            getattr(window, action)()
        if action != "minimize":
            try:
                window.activate()
            except Exception:
                pass  # Windows can refuse foreground activation; report separately.

    @staticmethod
    def _observed(window, action, hwnd):
        if action == "close":
            # Gone, or hidden: tray programs (Spotify, messengers) hide their window instead of
            # closing; live test 07.11.2026. A window that is still visible stays unverified.
            import win32gui
            return not win32gui.IsWindow(hwnd) or not win32gui.IsWindowVisible(hwnd)
        if action == "maximize":
            return window.isMaximized and not window.isMinimized
        if action == "minimize":
            return window.isMinimized
        if action == "focus":
            return window.isActive and not window.isMinimized
        return window.visible and not window.isMinimized and not window.isMaximized

    @staticmethod
    def _tray_hint(action, verified, hwnd):
        """{'hidden': True} when a closed window still exists but is no longer visible."""
        if action != "close" or not verified:
            return {}
        import win32gui
        return {"hidden": bool(win32gui.IsWindow(hwnd))}

    @staticmethod
    def _foreground(window, action):
        if action in {"minimize", "close"}:
            return False
        try:
            return bool(window.isActive)
        except Exception:
            return False

    @staticmethod
    def _find(query: str):
        import pygetwindow as gw
        return [
            window for window in gw.getAllWindows()
            if query.lower() in window.title.lower()
        ]

    def minimize(self, query: str) -> int:
        windows = self._find(query)
        for window in windows:
            window.minimize()
        return len(windows)

    def maximize(self, query: str) -> int:
        windows = self._find(query)
        for window in windows:
            window.maximize()
        return len(windows)

    def close(self, query: str) -> int:
        windows = self._find(query)
        for window in windows:
            window.close()
        return len(windows)

    @staticmethod
    def terminate_processes(query: str) -> int:
        count = 0
        for process in psutil.process_iter(["name"]):
            name = process.info.get("name") or ""
            if query.lower() in name.lower():
                process.terminate()
                count += 1
        return count
