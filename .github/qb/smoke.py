"""Smoke runs of a CREST binary for the Windows port (docs/WINDOWS.md).

    python smoke.py CREST_EXE OUT_DIR [--threads N] [--cases a,b] [--json summary.json]

Every case runs in its own folder under OUT_DIR with the built-in calculators (tblite / gfnff, no xtb
program). The summary holds the exit code, wall time, energies, conformer count and the files left in
the folder, so compare.py can put a Windows and a Linux run of the same commit side by side.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
MOLECULES = HERE / "molecules"

#: name -> (molecule, CREST arguments after the input file, kind)
CASES = {
    "sp_gfn2_alpb": ("butanol", ["--sp", "--gfn2", "--alpb", "water"], "energy"),
    "sp_gfnff": ("pfba", ["--sp", "--gfnff"], "energy"),
    "opt_gfn2_alpb": ("pfba", ["--ancopt", "--gfn2", "--alpb", "water"], "energy"),
    "imtdgc_gfnff": ("butanol", ["--gfnff"], "search"),
    "imtdgc_gfn2_alpb_quick": ("pfba", ["--gfn2", "--alpb", "water", "--quick"], "search"),
}

ENERGY_RE = re.compile(r"TOTAL ENERGY\s+(-?\d+\.\d+)")


def xyz_energies(path: Path) -> list[float]:
    """Energies from the comment lines of a multi-structure xyz file."""
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    out, i = [], 0
    while i < len(lines):
        try:
            n = int(lines[i].split()[0])
        except (ValueError, IndexError):
            i += 1
            continue
        comment = lines[i + 1] if i + 1 < len(lines) else ""
        for tok in comment.split():
            try:
                out.append(float(tok))
                break
            except ValueError:
                continue
        i += n + 2
    return out


def run_case(exe: str, name: str, root: Path, threads: int, timeout: float) -> dict:
    mol, args, kind = CASES[name]
    d = root / name
    if d.exists():
        shutil.rmtree(d)
    d.mkdir(parents=True)
    shutil.copy(MOLECULES / f"{mol}.xyz", d / "input.xyz")
    env = dict(os.environ, OMP_NUM_THREADS=str(threads), OPENBLAS_NUM_THREADS="1",
               OMP_MAX_ACTIVE_LEVELS="1", OMP_STACKSIZE=os.environ.get("OMP_STACKSIZE", "1G"))
    cmd = [exe, "input.xyz", *args, "-T", str(threads)]
    t0 = time.perf_counter()
    with open(d / "crest.out", "wb") as out:
        try:
            cp = subprocess.run(cmd, cwd=d, stdout=out, stderr=subprocess.STDOUT, env=env, timeout=timeout)
            code = cp.returncode
        except subprocess.TimeoutExpired:
            code = "timeout"
    seconds = time.perf_counter() - t0
    text = (d / "crest.out").read_text(encoding="utf-8", errors="replace")
    res = {"case": name, "molecule": mol, "args": args, "threads": threads, "exit": code,
           "seconds": round(seconds, 2), "normal_termination": "CREST terminated normally" in text}
    if kind == "energy":
        m = ENERGY_RE.findall(text)
        res["energy"] = float(m[-1]) if m else None
    else:
        conf = d / "crest_conformers.xyz"
        energies = xyz_energies(conf) if conf.is_file() else []
        res["n_conformers"] = len(energies)
        res["lowest_energy"] = min(energies) if energies else None
        res["energies"] = energies
        best = d / "crest_best.xyz"
        res["best_energy"] = xyz_energies(best)[0] if best.is_file() and xyz_energies(best) else None
    res["files_left"] = sorted(p.name + ("/" if p.is_dir() else "") for p in d.iterdir())
    res["output_tail"] = text.splitlines()[-25:]
    return res


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("crest")
    ap.add_argument("out_dir")
    ap.add_argument("--threads", type=int, default=2)
    ap.add_argument("--cases", default=",".join(CASES))
    ap.add_argument("--timeout", type=float, default=1800)
    ap.add_argument("--json", default="")
    a = ap.parse_args(argv)
    exe = shutil.which(a.crest) or a.crest
    root = Path(a.out_dir).resolve()
    root.mkdir(parents=True, exist_ok=True)
    version = subprocess.run([exe, "--version"], capture_output=True, text=True, errors="replace")
    summary = {"crest": exe, "platform": sys.platform, "version": (version.stdout + version.stderr).strip()[-2000:],
               "cases": []}
    ok = True
    for name in a.cases.split(","):
        r = run_case(exe, name.strip(), root, a.threads, a.timeout)
        summary["cases"].append(r)
        good = r["exit"] == 0 and r["normal_termination"] and (r.get("energy") is not None or
                                                                (r.get("n_conformers") or 0) > 0)
        ok &= good
        brief = {k: r[k] for k in ("exit", "seconds", "energy", "n_conformers", "lowest_energy") if k in r}
        print(f"{'ok  ' if good else 'FAIL'} {name}: {brief}", flush=True)
        if not good:
            print("\n".join("    " + line for line in r["output_tail"]), flush=True)
    if a.json:
        Path(a.json).write_text(json.dumps(summary, indent=1), encoding="utf-8")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
