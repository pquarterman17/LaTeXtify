"""Native folder picker for the GUI Export panel, run out-of-process.

A GUI dialog cannot run on uvicorn's worker thread on every platform (macOS
requires the main thread), so :func:`pick_folder_native` shells tkinter's
``askdirectory`` into a separate process -- which also contains a crash or hang
behind a timeout. It returns "" when the user cancels or no GUI/tkinter is
available (a headless server) so the caller falls back to manual path entry;
it never raises.
"""

from __future__ import annotations

import subprocess
import sys

_PICK_FOLDER_SCRIPT = (
    "import tkinter, tkinter.filedialog as fd, sys\n"
    "r = tkinter.Tk(); r.withdraw()\n"
    "try: r.attributes('-topmost', True)\n"
    "except Exception: pass\n"
    "p = fd.askdirectory(title='Choose an export folder') or ''\n"
    "r.destroy()\n"
    "sys.stdout.write(p)\n"
)

#: A folder dialog is interactive, but a walked-away dialog shouldn't pin a
#: server worker thread indefinitely; 3 minutes is ample to choose a folder
#: while bounding the abandoned-dialog case (was 5 minutes -- tech-debt finding 7).
_PICK_FOLDER_TIMEOUT = 180.0


def pick_folder_native(timeout: float = _PICK_FOLDER_TIMEOUT) -> str:
    """Open a native folder picker on the server host; return the path or "".

    Returns "" on cancel, a headless host, a crash, or the timeout -- never
    raises, so the caller can fall back to manual entry.
    """
    try:
        proc = subprocess.run(
            [sys.executable, "-c", _PICK_FOLDER_SCRIPT],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except (subprocess.SubprocessError, OSError):
        return ""
    return proc.stdout.strip() if proc.returncode == 0 else ""
