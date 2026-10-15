"""Copy the application's own PHP files into a separate folder for a C3 run.

Third-party code bundled inside a cloned app (for example TCPDF, CKEditor or a
validation library) is excluded so that C3 is evaluated on the application's
own data-access code only. The exact file list is written next to the copy and
must be recorded in benchmarks/benchmark_manifest.json.

This script does not change any PHP file. It never executes PHP.

Usage:
    python make_app_only_copy.py <source_app_dir> <target_dir> [--exclude DIR ...]
"""

import argparse
import json
import shutil
from pathlib import Path


def collect_php_files(source: Path, excluded: list[str]) -> list[Path]:
    """Return app PHP files relative to source, sorted, without excluded dirs."""

    excluded_lower = {name.lower() for name in excluded}
    files = []

    for path in sorted(source.rglob("*.php")):
        relative = path.relative_to(source)
        parts = [part.lower() for part in relative.parts[:-1]]

        if ".git" in parts:
            continue

        if any(part in excluded_lower for part in parts):
            continue

        files.append(relative)

    return files


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source")
    parser.add_argument("target")
    parser.add_argument("--exclude", nargs="*", default=[])
    args = parser.parse_args()

    source = Path(args.source).resolve()
    target = Path(args.target).resolve()

    if target.exists():
        shutil.rmtree(target)

    files = collect_php_files(source, args.exclude)

    for relative in files:
        destination = target / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source / relative, destination)

    listing = {
        "source": source.name,
        "excluded_directories": args.exclude,
        "php_file_count": len(files),
        "php_files": [relative.as_posix() for relative in files],
    }

    (target.parent / f"{target.name}_file_list.json").write_text(
        json.dumps(listing, indent=2),
        encoding="utf-8",
    )

    print(f"Copied {len(files)} PHP files to {target}")


if __name__ == "__main__":
    main()
