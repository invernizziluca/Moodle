"""Checks an exported archive: every local link resolves, no placeholders/partials are left, each
course has its overview files. Usage: python tests/verify_archive.py <archive_dir>"""

import json
import re
import sys
from pathlib import Path
from urllib.parse import unquote

LINK = re.compile(r"!?\[[^\]\n]*\]\(([^)\s]+)\)")
REQUIRED = ["course_overview.md", "course_fulltext.md", "course_info.md", "course_structure.json", "verifica_corso.md",
            "elementi_mancanti.md", "file_del_corso.md"]
OPTIONAL_REPORTS = ["video_e_media.md"]


def main(root: Path) -> int:
    problems = []
    md_files = [p for p in root.rglob("*.md") if ".archiver" not in p.parts]
    links = 0
    for md in md_files:
        text = md.read_text(encoding="utf-8")
        if "@@FILE:" in text or "@@ITEM:" in text:
            problems.append(f"placeholder left in {md}")
        if md.parent != root and md.name not in ("index.md",) and "_registro" not in md.parts:
            if not text.startswith("---\n") and md.name not in REQUIRED + OPTIONAL_REPORTS and "_versioni_precedenti" not in md.parts:
                problems.append(f"no front matter: {md}")
        for target in LINK.findall(text):
            if re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*:", target) or target.startswith("#"):
                continue
            links += 1
            path = (md.parent / unquote(target.split("#")[0])).resolve()
            if not path.exists() and "_versioni_precedenti" not in md.parts:
                problems.append(f"broken link in {md.relative_to(root)} -> {target}")
    for leftover in list(root.rglob("*.part")) + list(root.rglob("*.new")) + list(root.rglob("*.tmp")):
        problems.append(f"leftover partial file: {leftover}")
    courses = json.loads((root / "courses.json").read_text(encoding="utf-8"))["courses"]
    for c in courses:
        for name in REQUIRED + [f"{c['folder']}.md"]:  # incl. the course home / folder note
            if not (root / c["folder"] / name).exists():
                problems.append(f"missing {name} in {c['folder']}")
        json.loads((root / c["folder"] / "course_structure.json").read_text(encoding="utf-8"))
    files = [p for p in root.rglob("*") if p.is_file() and ".archiver" not in p.parts]
    print(f"{len(courses)} courses, {len(md_files)} notes, {len(files)} files, {links} local links checked")
    for p in problems:
        print("PROBLEM:", p)
    print("OK" if not problems else f"{len(problems)} problems")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main(Path(sys.argv[1])))
