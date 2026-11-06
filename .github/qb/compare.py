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

TOL = {"sp": 1e-8, "opt": 1e-6, "imtdgc": 1e-4, "legacy": 1e-4}  # Eh


def tol_for(case: str) -> float:
    return TOL[case.split("_")[0]]


def missed_lowest(r: dict, o: dict, tol: float) -> int:
    """For a search whose lowest energies differ: k > 0 when the run with the higher lowest energy only missed
    the other's lowest conformer, i.e. its lowest is conformer k (1-based) of the other run. A quick search does
    that now and then on one platform too: 2 of 6 Linux runs of imtdgc_gfnff_quick_davidson ended at
    -2.96172 Eh, the other's #2, instead of -2.96187 (docs/MACOS.md). 0 otherwise."""
    er, eo = r.get("lowest_energy"), o.get("lowest_energy")
    if er is None or eo is None:
        return 0
    high, low_run = (er, o) if er > eo else (eo, r)
    for k, e in enumerate(sorted(low_run.get("energies") or []), 1):
        if abs(e - high) <= tol:
            return k
    return 0


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
        note = ""
        if not ok and key == "lowest_energy" and r["exit"] == 0 and o["exit"] == 0:
            k = missed_lowest(r, o, tol_for(r["case"]))
            if k:
                ok, note = True, f" (sampling: one run's lowest is the other's #{k})"
        ok_all &= ok or bool(r.get("optional"))
        nr, no = r.get("n_conformers", ""), o.get("n_conformers", "")
        rows.append(f"| {r['case']} | {r['exit']} | {o['exit']} | {er} | {eo} | "
                    f"{'' if de is None else f'{de:.1e}'} | {nr} | {no} | {r['seconds']} | {o['seconds']} | "
                    f"{'yes' if ok else 'NO'}{note}{' (optional)' if r.get('optional') else ''} |")
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
