"""Read-only inventory for career source onboarding."""

from __future__ import annotations

import hashlib
import mimetypes
from dataclasses import asdict, dataclass
from pathlib import Path


SUPPORTED_SUFFIXES = {
    ".tex",
    ".pdf",
    ".md",
    ".txt",
    ".docx",
    ".rtf",
    ".json",
    ".yaml",
    ".yml",
}
SOURCE_CATEGORIES = (
    "cv",
    "projects",
    "linkedin",
    "publications",
    "diplomas",
    "references",
)


@dataclass(frozen=True)
class SourceFile:
    category: str
    relative_path: str
    suffix: str
    media_type: str
    size_bytes: int
    sha256: str
    supported: bool


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def inventory_sources(workspace: Path) -> dict[str, object]:
    workspace = workspace.resolve()
    documents = (workspace / "documents").resolve()
    files: list[SourceFile] = []
    counts = {category: 0 for category in SOURCE_CATEGORIES}
    for category in SOURCE_CATEGORIES:
        root = documents / category
        if not root.is_dir():
            continue
        for path in sorted(root.rglob("*")):
            if not path.is_file() or path.name == ".gitkeep":
                continue
            resolved = path.resolve()
            if not resolved.is_relative_to(root.resolve()):
                continue
            suffix = path.suffix.casefold()
            item = SourceFile(
                category=category,
                relative_path=str(resolved.relative_to(workspace)),
                suffix=suffix,
                media_type=mimetypes.guess_type(path.name)[0] or "application/octet-stream",
                size_bytes=path.stat().st_size,
                sha256=_sha256(path),
                supported=suffix in SUPPORTED_SUFFIXES,
            )
            files.append(item)
            counts[category] += 1
    supported = sum(item.supported for item in files)
    return {
        "documents_root": str(documents),
        "counts": counts,
        "file_count": len(files),
        "supported_count": supported,
        "unsupported_count": len(files) - supported,
        "files": [asdict(item) for item in files],
        "ready_for_ingestion": counts["cv"] > 0 and counts["projects"] > 0,
        "minimum_needed": {
            "cv": "Overleaf source project plus compiled PDF",
            "projects": "At least 3–5 project folders or documents",
        },
    }
