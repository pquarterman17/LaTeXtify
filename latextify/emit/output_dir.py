"""Where a conversion is allowed to write: ``output_root/<journal>``.

Split out of :mod:`latextify.emit.project` (2026-09-07) when that module hit
the repo's 500-line ceiling again. A clean seam: this is the one piece of the
emit that exists purely to keep a request-supplied string from steering writes
somewhere it should not go, and it needs nothing else from the emitter.

The journal name reaches here from the CLI and from a GUI form field, and it
becomes the directory name every generated file is written under. That makes it
the same class of input as the GUI's export destination -- a caller-chosen path
component -- so it gets the same treatment: validate the spelling, then prove
the normalized result still sits directly inside the root.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

from latextify.templates.loader import ManifestError

#: A journal name doubles as the per-journal output directory name, so it must
#: be one plain path component: no separators, no leading dot, nothing that
#: could climb out of ``output_root``. Every shipped manifest already fits.
_JOURNAL_NAME_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")


def journal_output_dir(output_root: Path, journal_name: str) -> Path:
    """Return ``output_root / journal_name``, refusing a name that escapes the root.

    Validating the spelling first, and then checking that the normalized
    destination still sits directly inside ``output_root``, keeps a crafted
    name such as ``../../etc`` from steering the writes anywhere else. Raises
    :class:`~latextify.templates.loader.ManifestError` (a ``ValueError``) so
    every caller's existing bad-journal handling applies unchanged.
    """
    if not _JOURNAL_NAME_RE.fullmatch(journal_name):
        raise ManifestError(
            f"{journal_name!r}: journal name must be a single plain path component "
            "(letters, digits, '.', '_' and '-' only)"
        )
    root = os.path.normpath(os.path.abspath(output_root))
    candidate = os.path.normpath(os.path.join(root, journal_name))
    if not candidate.startswith(os.path.join(root, "")) or os.path.dirname(candidate) != root:
        raise ManifestError(f"{journal_name!r}: journal name resolves outside the output directory")
    return Path(candidate)
