"""Make a macOS program relocatable: copy every non-system dylib it loads into one folder (docs/MACOS.md).

    python macos_bundle.py BIN/crest LIBDIR

- follows the dependencies recursively (``otool -L``), resolving ``@rpath``, ``@loader_path`` and
  ``@executable_path`` the way dyld does (LC_RPATH entries of the loading file, then of the program);
- copies each library outside /usr/lib and /System into LIBDIR under the file name of its install name
  (``libgfortran.5.dylib``, ...), sets its id to ``@rpath/<name>`` and its rpath to ``@loader_path``;
- points the program at them (``@rpath/<name>``) with the rpath ``@loader_path/../<LIBDIR's name>``
  (relative to the program's folder, as LIBDIR is to it) and removes the build's absolute rpaths;
- signs everything ad hoc (arm64 macOS does not run code whose signature no longer matches) and
  checks that no absolute non-system path is left.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

SYSTEM = ("/usr/lib/", "/System/")


def sh(*cmd: str) -> str:
    cp = subprocess.run(cmd, capture_output=True, text=True)
    if cp.returncode != 0:
        raise SystemExit(f"{' '.join(cmd)} failed ({cp.returncode}):\n{cp.stdout}{cp.stderr}")
    return cp.stdout


def install_names(path: Path) -> list[str]:
    """The libraries ``path`` loads (its own id left out)."""
    own = sh("otool", "-D", str(path)).splitlines()[1:]
    out = []
    for line in sh("otool", "-L", str(path)).splitlines()[1:]:
        m = re.match(r"\s+(\S.*?) \(compatibility version", line)
        if m and m.group(1) not in own:
            out.append(m.group(1))
    return out


def rpaths(path: Path) -> list[str]:
    text = sh("otool", "-l", str(path))
    return re.findall(r"cmd LC_RPATH\n\s+cmdsize \d+\n\s+path (.+?) \(offset \d+\)", text)


def resolve(name: str, loader: Path, program: Path) -> Path | None:
    def expand(p: str) -> str:
        return p.replace("@loader_path", str(loader.parent)).replace("@executable_path", str(program.parent))

    if name.startswith("@rpath/"):
        rest = name[len("@rpath/"):]
        for rp in rpaths(loader) + (rpaths(program) if loader != program else []):
            cand = Path(expand(rp)) / rest
            if cand.is_file():
                return cand.resolve()
        return None
    cand = Path(expand(name))
    return cand.resolve() if cand.is_file() else None


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(__doc__)
        return 2
    program = Path(argv[0]).resolve()
    libdir = Path(argv[1]).resolve()
    libdir.mkdir(parents=True, exist_ok=True)
    rel = os.path.relpath(libdir, program.parent)

    # 1. collect: install name -> source file, breadth first from the program
    sources: dict[str, Path] = {}            # file name in LIBDIR -> original file
    renames: dict[Path, dict[str, str]] = {}  # file to fix -> {old install name: new}
    queue = [(program, program)]
    seen = set()
    while queue:
        f, origin = queue.pop(0)
        if f in seen:
            continue
        seen.add(f)
        changes = renames.setdefault(f, {})
        for name in install_names(origin):
            if name.startswith(SYSTEM):
                continue
            src = resolve(name, origin, program)
            if src is None:
                raise SystemExit(f"cannot resolve {name} (loaded by {origin})")
            base = Path(name).name
            if base in sources and sources[base] != src:
                raise SystemExit(f"two different files named {base}: {sources[base]} and {src}")
            sources[base] = src
            changes[name] = f"@rpath/{base}"
            dest = libdir / base
            if not dest.exists():
                shutil.copy2(src, dest)
                dest.chmod(0o755)
            queue.append((dest, src))

    # 2. rewrite install names and rpaths, then sign
    for f, changes in renames.items():
        args = []
        for old, new in changes.items():
            args += ["-change", old, new]
        if f != program:
            args += ["-id", f"@rpath/{f.name}"]
        for rp in dict.fromkeys(rpaths(f)):
            args += ["-delete_rpath", rp]
        if args:
            sh("install_name_tool", *args, str(f))
        want = f"@loader_path/{rel}" if f == program else "@loader_path"
        if want not in rpaths(f):
            sh("install_name_tool", "-add_rpath", want, str(f))
        sh("codesign", "--force", "--sign", "-", str(f))

    # 3. check
    bad = []
    for f in renames:
        for name in install_names(f):
            if not name.startswith(SYSTEM) and not name.startswith("@rpath/"):
                bad.append(f"{f.name}: {name}")
        for rp in rpaths(f):
            if not rp.startswith("@"):
                bad.append(f"{f.name}: rpath {rp}")
        print(f"{f.name}: rpaths {rpaths(f)}")
        for name in install_names(f):
            print(f"    {name}")
    if bad:
        print("absolute paths left:\n  " + "\n  ".join(bad), file=sys.stderr)
        return 1
    print(f"bundled {len(sources)} libraries into {libdir}: {', '.join(sorted(sources))}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
