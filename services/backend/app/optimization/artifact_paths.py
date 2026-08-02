"""Shared canonical relative-path rules for optimization artifact references."""

from __future__ import annotations

import re
from pathlib import PurePosixPath

MAX_OPTIMIZATION_ARTIFACT_PATH_CHARACTERS = 512

_WINDOWS_DRIVE = re.compile(r"^[A-Za-z]:")
_URI_SCHEME = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*:")


def is_canonical_artifact_path(value: object, run_id: object) -> bool:
    """Return whether *value* is one safe canonical path ending exactly in *run_id*."""

    if (
        not isinstance(value, str)
        or not isinstance(run_id, str)
        or not value
        or len(value) > MAX_OPTIMIZATION_ARTIFACT_PATH_CHARACTERS
        or "\\" in value
        or _WINDOWS_DRIVE.match(value) is not None
        or _URI_SCHEME.match(value) is not None
    ):
        return False
    path = PurePosixPath(value)
    return (
        not path.is_absolute()
        and value == path.as_posix()
        and bool(path.parts)
        and all(part not in {"", ".", ".."} for part in path.parts)
        and path.name == run_id
    )
