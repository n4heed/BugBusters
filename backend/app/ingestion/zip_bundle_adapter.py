"""ZIP bundle handling — extraction only, not a SourceAdapter itself (a
ZIP yields multiple member files, each of which is then parsed by the
appropriate csv/json/jsonl adapter). Safety-first: rejects path traversal,
caps total uncompressed size and member count before touching member
content, and never executes anything from the archive.
"""
from __future__ import annotations

import zipfile
from pathlib import PurePosixPath

from .base import SUPPORTED_EXTENSIONS

MAX_MEMBERS = 50
MAX_TOTAL_UNCOMPRESSED_BYTES = 200 * 1024 * 1024  # 200 MB
MAX_SINGLE_MEMBER_BYTES = 100 * 1024 * 1024


class UnsafeZipError(ValueError):
    pass


def _is_safe_member_name(name: str) -> bool:
    if name.startswith("/") or name.startswith("\\"):
        return False
    posix = PurePosixPath(name)
    if ".." in posix.parts:
        return False
    if posix.is_absolute():
        return False
    return True


def extract_zip_bundle(content: bytes) -> list[tuple[str, bytes]]:
    """Returns [(member_filename, member_content_bytes), ...] for every
    supported-format member in the archive. Unsupported member types
    (e.g. a README) are skipped, not treated as an error — a bundle may
    legitimately contain non-dataset files.
    """
    import io

    results: list[tuple[str, bytes]] = []
    try:
        zf = zipfile.ZipFile(io.BytesIO(content))
    except zipfile.BadZipFile as exc:
        raise UnsafeZipError(f"Not a valid ZIP archive: {exc}") from exc

    infos = zf.infolist()
    if len(infos) > MAX_MEMBERS:
        raise UnsafeZipError(f"ZIP contains {len(infos)} entries, exceeding the limit of {MAX_MEMBERS}.")

    total_uncompressed = sum(i.file_size for i in infos)
    if total_uncompressed > MAX_TOTAL_UNCOMPRESSED_BYTES:
        raise UnsafeZipError(
            f"ZIP would expand to {total_uncompressed} bytes, exceeding the limit of "
            f"{MAX_TOTAL_UNCOMPRESSED_BYTES} bytes (zip-bomb protection)."
        )

    for info in infos:
        if info.is_dir():
            continue
        if not _is_safe_member_name(info.filename):
            raise UnsafeZipError(f"Unsafe path in ZIP entry: {info.filename!r} (path traversal rejected).")
        if info.file_size > MAX_SINGLE_MEMBER_BYTES:
            raise UnsafeZipError(f"ZIP member {info.filename!r} exceeds the per-file size limit.")
        if not info.filename.lower().endswith(SUPPORTED_EXTENSIONS):
            continue  # not a dataset file (e.g. README, .gitignore) — skip, not an error
        with zf.open(info) as member:
            results.append((PurePosixPath(info.filename).name, member.read()))

    return results
