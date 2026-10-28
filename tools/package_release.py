"""Build the small Windows distribution without long historical sample paths.

Usage: python tools/package_release.py --output /path/to/FFHS_Archiver.zip
Only runtime files, launchers and current user documentation are distributed.
"""

from __future__ import annotations

import argparse
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

TOP_FOLDER = "FFHS_Archiver"
MAX_MEMBER_PATH = 140
_PRIVATE_NAMES = {"credentials.json", "credentials.json.tmp", "settings.json"}
_EXCLUDED_PARTS = {"__pycache__", ".git", ".venv", ".archiver", "browser-profile", "tests", "node_modules"}


def package_files(root: Path) -> list[Path]:
    """Explicit runtime allowlist; never add a whole developer checkout."""
    files = [root / "LEGGIMI.md", root / "FFHS Moodle Archiver.pyw",
             root / "Avvia FFHS Moodle Archiver.bat"]
    files.extend(p for p in (root / "ffhs_archiver").rglob("*") if p.is_file())
    files.extend((root / "docs").glob("*.md"))
    selected = []
    for file in files:
        rel = file.relative_to(root)
        if any(part in _EXCLUDED_PARTS for part in rel.parts) or file.name in _PRIVATE_NAMES:
            continue
        if file.suffix in {".pyc", ".pyo", ".part", ".tmp"}:
            continue
        if file.is_symlink() or not file.is_file():
            raise ValueError(f"Missing or unsupported distribution file: {rel}")
        selected.append(file)
    return sorted(set(selected))


def build_package(root: Path, output: Path) -> list[str]:
    root = root.resolve()
    files = package_files(root)
    entries = [(p, f"{TOP_FOLDER}/{p.relative_to(root).as_posix()}") for p in files]
    for _, member in entries:
        if len(member) > MAX_MEMBER_PATH:
            raise ValueError(f"Distribution path exceeds {MAX_MEMBER_PATH} characters: {member}")
    output.parent.mkdir(parents=True, exist_ok=True)
    with ZipFile(output, "w", compression=ZIP_DEFLATED) as archive:
        for source, member in entries:
            info = ZipInfo(member, date_time=(2020, 1, 1, 0, 0, 0))
            info.compress_type = ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            archive.writestr(info, source.read_bytes())
    with ZipFile(output) as archive:
        if archive.testzip() is not None:
            raise ValueError("Distribution checksum validation failed")
    return [member for _, member in entries]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parent.parent
    members = build_package(root, args.output)
    print(f"Created {args.output}: {len(members)} files; longest path {max(map(len, members))} characters")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
