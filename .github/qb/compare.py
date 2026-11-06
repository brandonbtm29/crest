"""Compare smoke.py summaries of two builds (e.g. Windows and Linux of the same commit).

    python compare.py REFERENCE.json OTHER.json [--md table.md]

Single points and optimizations must agree tightly (same code, same parameters, different compiler
runtime and libm); a conformer search follows chaotic MD trajectories, so only its lowest energy is
held to a tolerance and the conformer counts are reported. Exit code 1 if a tolerance is exceeded or a
case failed on either side.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

TOL = {"sp": 1e-8, "opt": 1e-6, "imtdgc": 1e-4}  # Eh


def tol_for(case: str) -> float:
    return TOL[case.split("_")[0]]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("reference")
    ap.add_argument("other")
    ap.add_argument("--md", default="")
    a = ap.parse_args(argv)
    ref = json.loads(Path(a.reference).read_text(encoding="utf-8"))
    oth = json.loads(Path(a.other).read_text(encoding="utf-8"))
    rows = ["| case | ref exit | other exit | ref E / Eh | other E / Eh | dE / Eh | ref n | other n | ref s | other s | ok |",
            "|---|---|---|---|---|---|---|---|---|---|---|"]
    ok_all = True
    by_name = {c["case"]: c for c in oth["cases"]}
    for r in ref["cases"]:
        o = by_name.get(r["case"])
        if o is None:
            rows.append(f"| {r['case']} | {r['exit']} | missing | | | | | | | | no |")
            ok_all = False
            continue
        key = "energy" if "energy" in r else "lowest_energy"
        er, eo = r.get(key), o.get(key)
        de = abs(er - eo) if er is not None and eo is not None else None
        ok = r["exit"] == 0 and o["exit"] == 0 and de is not None and de <= tol_for(r["case"])
        ok_all &= ok
        nr, no = r.get("n_conformers", ""), o.get("n_conformers", "")
        rows.append(f"| {r['case']} | {r['exit']} | {o['exit']} | {er} | {eo} | "
                    f"{'' if de is None else f'{de:.1e}'} | {nr} | {no} | {r['seconds']} | {o['seconds']} | "
                    f"{'yes' if ok else 'NO'} |")
        left_r, left_o = set(r["files_left"]), set(o["files_left"])
        if left_r != left_o:
            rows.append(f"|  files only in ref: {sorted(left_r - left_o)}; only in other: "
                        f"{sorted(left_o - left_r)} | | | | | | | | | | |")
    table = "\n".join(rows)
    print(f"reference: {ref['platform']}  other: {oth['platform']}\n")
    print(table)
    if a.md:
        Path(a.md).write_text(table + "\n", encoding="utf-8")
    return 0 if ok_all else 1


if __name__ == "__main__":
    sys.exit(main())
