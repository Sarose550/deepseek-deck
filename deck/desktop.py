"""Native desktop app for the DeepSeek Deck (macOS).

A proper Dock app: boots (or reuses) the daemon and shows the web UI in a real
WKWebView window with our icon in the Dock. Exposes a native folder picker to
the page via `window.pywebview.api.pick_folder()`. Closing the window quits the
app — but the daemon (and any running workers) keeps going independently, so
relaunching from the Dock reliably opens a fresh window.
"""
from __future__ import annotations

import os
import sys

import webview

from . import cli

ICNS = os.path.join(os.path.dirname(os.path.dirname(__file__)), "assets", "icon.icns")


class Api:
    """JS bridge — callable from the page as window.pywebview.api.*"""

    def pick_folder(self) -> str:
        try:
            wins = webview.windows
            res = wins[0].create_file_dialog(webview.FOLDER_DIALOG)
            if res:
                return res[0] if isinstance(res, (list, tuple)) else str(res)
        except Exception:  # noqa: BLE001
            pass
        return ""


def _set_dock_icon() -> None:
    try:
        from AppKit import NSApplication, NSImage  # type: ignore
        img = NSImage.alloc().initWithContentsOfFile_(ICNS)
        if img is not None:
            NSApplication.sharedApplication().setApplicationIconImage_(img)
    except Exception:  # noqa: BLE001
        pass


def main() -> None:
    base = cli.ensure_daemon()
    _set_dock_icon()
    webview.create_window(
        "DeepSeek Deck",
        url=base,
        js_api=Api(),
        width=1320,
        height=860,
        min_size=(820, 520),
        text_select=True,
    )
    webview.start()
    sys.exit(0)


if __name__ == "__main__":
    main()
