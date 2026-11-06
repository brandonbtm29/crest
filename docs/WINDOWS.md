# CREST on native Windows (fork `brandonbtm29/crest`, branch `qb/windows`)

This branch builds CREST as a native Windows program (`crest.exe`, x86-64) with the MSYS2 UCRT64
MinGW-w64 toolchain (gcc/gfortran) and OpenBLAS. Linux and macOS behave as before: the Windows code is
behind `_WIN32` (C, preprocessed Fortran) or `if(WIN32)` / `host_machine.system() == 'windows'` (CMake,
meson). The only changes that reach them are commas in 11 format strings (needed by gfortran 15+) and a
`close` of a unit left open, neither of which changes the output.

Upstream does not support Windows (crest-lab/crest issues #276 and #307, closed as not planned; a
maintainer noted that some functions, such as QCG, run shell commands, and that CMake is preferred over
meson). conda-forge builds CREST for Linux and macOS only.

## Base

`qb/windows` starts from upstream `master` at `cfdc301` (2026-06-16, "Fix CMake finder (#487)"), not
from the `v3.0.2` tag (2024-08-25). Master still reports version 3.0.2, but it has 80 commits of
"3.0.2 maintenance" on top of the tag:

- its `tblite` submodule is the tblite `v0.5.0` release; `v3.0.2` pins an unreleased tblite
  snapshot (`v0.3.0-72-g4556e28`, a commit that is not on tblite's main branch; conda-forge builds
  3.0.2 against `pprcht/tblite@v0.3.0-fork` instead);
- the fix for a double allocation that crashes some GNU builds (`5ca82fe`; conda-forge carries it
  as a patch on 3.0.2), the ANCOPT max-cycle fix, the newer gfnff submodule, `--uhf` handling,
  libpvol, and the CMake finder fix (#487);
- a Windows patch made against master can go upstream as is.

The cost: the MacBook's conda-forge `crest 3.0.2` is not the same code (older tblite and gfnff), so
results of the Windows build should be compared with a Linux build of this branch (CI does that), not
with the Mac's binary.

## Building

CI: `.github/workflows/qb-windows.yml` (push to `qb/windows`). By hand, in an MSYS2 UCRT64 shell:

```sh
pacman -S --needed git mingw-w64-ucrt-x86_64-{gcc,gcc-fortran,libgomp,openblas,cmake,ninja,pkgconf}
git clone --recursive -b qb/windows https://github.com/brandonbtm29/crest && cd crest
cmake -B _build -G Ninja -DCMAKE_BUILD_TYPE=Release -DBLA_VENDOR=OpenBLAS -DSTATICBUILD=ON -DWITH_TESTS=OFF
cmake --build _build
```

`-DSTATICBUILD=ON` gives one self-contained `crest.exe` (GCC runtime, libgomp, winpthreads and OpenBLAS
linked in). Without it, the build needs `libgomp-1.dll`, `libopenblas.dll`, `libgfortran-5.dll` (used by
OpenBLAS), `libquadmath-0.dll`, `libgcc_s_seh-1.dll` and `libwinpthread-1.dll` next to `crest.exe`; the
CI's shared package copies them there. OpenMP is GCC's libgomp throughout (MSYS2's OpenBLAS uses it
too), so there is one OpenMP runtime in the process; the workflow fails if OpenBLAS or crest.exe ever
imports LLVM's `libomp`.

## What blocks Windows (survey of master `cfdc301`)

### 1. Build system

| Item | Finding | Change |
|---|---|---|
| CMake (preferred upstream) | Configures as is with MinGW-w64; subprojects come from git submodules (toml-f, gfnff, gfn0, tblite, lwoniom, libpvol, test-drive); tblite's own dependencies (mctc-lib v0.3.2, dftd4 v3.7.0, multicharge v0.3.0, s-dftd3 v1.2.1, toml-f v0.4.2) are fetched with FetchContent at pinned tags | none |
| BLAS/LAPACK | `find_package(BLAS/LAPACK)`; `-DBLA_VENDOR=OpenBLAS` finds MSYS2's OpenBLAS (LAPACK included), and CREST then defines `WITH_OPENBLAS` | none (configure flag) |
| meson | Same sources; tblite's meson subprojects need `-D<sub>:lapack=openblas` each (dftd4, multicharge) as in the tblite fork; not used by CI here | `win_compat.c` added; Windows link args as for CMake |
| `_WIN32` in Fortran | gfortran does not predefine OS macros (C compilers and Intel Fortran do) | CMake/meson define `_WIN32` for CREST's own Fortran sources on Windows (not for subprojects) |
| Main-thread stack | Windows reserves 1 MiB for the main thread; CREST is run with `ulimit -s unlimited` on Linux (QuantaBench's wrapper does this) | `-Wl,--stack,1073741824` (1 GiB reserve, `CREST_WINDOWS_STACK`); OpenMP worker threads use `OMP_STACKSIZE` as everywhere |
| `rename()` | see 3 | `-static-libgfortran -Wl,--wrap=rename` |
| `KILL` intrinsic | libgfortran has no `KILL` on Windows: the link fails (`_gfortran_kill_sub`, used by ConfSolv's shutdown) | `cs_kill` in `algos/ConfSolv.F90`: `taskkill /F /T /PID` on Windows, `kill(pid, 9)` elsewhere |
| Format strings | 11 formats lack a comma between descriptors (e.g. `'(1x,"("f7.2"%)")'` in ANCOPT/RFO); gfortran 15 and 16 (Ubuntu 26.04, MSYS2) stop at run time with "Missing comma between descriptors", so `--ancopt` fails on Linux too | commas added (no change in output) |

### 2. POSIX C calls bound from Fortran (`src/iomod.F90`)

These fail to link on Windows (no such functions in the MinGW-w64 CRT) or behave differently:

| Call | Used for | Windows replacement (`src/win_compat.c`) |
|---|---|---|
| `mkdir(path, mode)` | every job folder (`calculation.level.*`, `METADYN*`, `NORMMD*`, ...) | `_mkdir(path)` |
| `setenv` | `OMP_NUM_THREADS` etc. (`ompenvset`) | `_putenv_s` |
| `symlink` | `gfnff_topo` into job folders (legacy modes, propcalc) | `CreateSymbolicLinkA` (Developer Mode), else a copy |
| `realpath` | absolute path of the xtb binary (`--xnam`) | `_fullpath`, `NULL` if the path does not exist |

`getcwd`/`chdir` are gfortran intrinsics and work on Windows. Paths are built with `/`, which the
Windows CRT accepts.

### 3. `rename` must replace an existing file

POSIX `rename()` replaces an existing target; the Windows CRT's `rename()` fails instead (checked in CI
with a 10-line Fortran program). CREST calls gfortran's `RENAME` intrinsic in 135 places and relies on
the POSIX behaviour, e.g. in the iMTD-GC loop: `rename(ensemblefile, crest_rotamers_<n>.xyz)` overwrites
the previous level's file (`algos/search_conformers.f90`), `refine.f90` renames onto its input. On
Windows those renames would silently do nothing and the next step would read stale structures.

The intrinsic is implemented inside libgfortran, so instead of touching 135 call sites the Windows build
links libgfortran statically and redirects its `rename()` with `-Wl,--wrap=rename` to `__wrap_rename`
(`MoveFileExA` with `MOVEFILE_REPLACE_EXISTING`).

### 4. Shell commands (`system()` / `execute_command_line`)

On Windows `system()` runs `cmd.exe`, which has no `rm`, `cp`, `mv`, `command -v` or `/dev/null`, and
whose `cd` does not change drives. CREST sends all its shell commands through `iomod:command()` except
a few legacy failure paths. On Windows `command()` now calls `crest_win_command()`:

- `rm [-rf] names...` (with `*`/`?` patterns, several names, directories), `cp [-r]`, `mv`, `mkdir [-p]`
  and `command -v NAME [> file]` run natively (no shell);
- anything else goes to `cmd.exe` with `/dev/null` -> `NUL`, `a ; b` -> `a & b` (cmd.exe does not split on
  `;`), `cd DIR &&` -> `cd /d DIR &&`, and the program path written with backslashes (cmd.exe reads `/x`
  as a switch).

Two legacy-mode problems only showed up in a run (CI, xtb 6.7.1): `-gfn2//gfnff` optimizes each structure
with `xtb --opt (GFN-FF) ; xtb --sp (GFN2)` in one command line, and without the `;` translation the GFN2
single point never ran (the ensemble kept GFN-FF energies, -2.116 instead of -17.728 Eh); and
`trialMD_legacy`'s `gettime` left `TRIALMD/xtb.out` open, which Windows does not let CREST delete
(`close` added).

Where the commands come from:

| Source | Command lines | Run modes | State on this branch |
|---|---|---|---|
| `cleanup.f90:rmrf` (69 calls in cleanup.f90, used everywhere) | `rm -rf NAMES 2>/dev/null` | all, incl. the default iMTD-GC | native |
| `iomod:checkprog`, `getpath` | `command -v PROG [> pathout.tmp]` | `--legacy`, QCG, turbomole | native |
| legacy xtb runs (`legacy_algos/*`, `--legacy`, `-gfn2//gfnff`, `-gfn2@...`) | `cd DIR && xtb coord --md ... > xtb.out 2>/dev/null`, `xtb --opt ... ; xtb --sp ...` | `--legacy` and the methods that need it | via cmd.exe translation; needs `xtb.exe`; `-gfn2//gfnff --legacy --alpb water --quick` tested |
| subprocess calculators (`calculator/xtb_sc.f90`, `orca_sc`, `turbom_sc`, `generic_sc`) | `cd calcspace && BINARY ... > out 2>/dev/null` | TOML input with `type = "xtb"`, `"orca"`, ... | via cmd.exe translation (untested) |
| QCG (`qcg/solvtool*.f90`) | xtb/xtbiff runs, `cd DIR && ...`, `> out 2> /dev/null` | `--qcg` | via cmd.exe translation (untested; needs xtb, optionally xtbiff) |
| `scratch.f90` | `mktemp -d`, `scp -r ./* DIR/` | `--scratch` | not ported |
| `algos/ConfSolv.F90` | `nohup ... &`, `lsof`, `kill -9` | ConfSolv | not ported (only its `KILL` call, so that CREST links) |
| `entropy/thermocalc.f90`, `propcalc.f90` | `cd DIR && xtb ...` | `--thermo`, property modes | via cmd.exe translation (untested) |
| `msreact/msmod.f90`, `deprecated/*` | `execute_command_line` with xtb | `--msreact`, deprecated modes | not ported (bypasses `command()`) |
| `legacy_algos/confscript2_misc.f90:366`, `normmd.f90:461` | `cp -r DIR FAILED-MTD` | failure path only | first is `system()` (fails harmlessly), second native |

### 5. Signals

`initsignal` installs handlers for signals 2, 3, 9, 15 and 69. On Windows only SIGINT and SIGTERM exist;
the others return `SIG_ERR` (MinGW-w64's CRT start-up installs a no-op invalid-parameter handler), so
nothing changes. A caller that cancels CREST on Windows kills the process tree (`taskkill /T /F`), as
QuantaBench does.

### 6. Run modes and external programs

| Mode | Needs | Windows |
|---|---|---|
| default iMTD-GC, `--quick`, `--sp`, `--ancopt`, `--mdopt`, `--screen`, `--cregen` with GFN2-xTB, GFN1-xTB, GFN0-xTB, GFN-FF (tblite, gfnff, gfn0 in-process), ALPB/GBSA (tblite) | nothing external | built and smoke-tested (see Results) |
| `--gfn2//gfnff`, `--gfn2@gfnff`, ... | `--legacy`, so the `xtb` program ("option not yet available with new calculator") | `--gfn2//gfnff --legacy` smoke-tested with xtb 6.7.1; `--gfn2@...` untested |
| `--legacy` (other run types) | `xtb` program | translation in place, untested |
| QCG (`--qcg`) | `xtb`, optionally `xtbiff` | translation in place, untested |
| ORCA/Turbomole/generic calculators | those programs | translation in place, untested |
| `--scratch`, ConfSolv, MSREACT | POSIX tools | not ported |

## Changes on this branch

- `src/win_compat.c` (new): the Windows replacements above; compiles to nothing elsewhere.
- `src/iomod.F90`: on `_WIN32`, the C bindings point at `crest_win_*` and `command()` calls
  `crest_win_command()`.
- `src/algos/ConfSolv.F90`: `cs_kill` instead of the `KILL` intrinsic.
- `src/legacy_algos/trialmd_legacy.f90`: `gettime` closes its unit.
- `src/optimize/ancopt.f90`, `rfo.f90`, `printouts.f90`, `algos/search_mecp.f90`,
  `parsing/parse_keyvalue.f90`: missing commas in format strings (gfortran >= 15, all platforms).
- `CMakeLists.txt`: `_WIN32` for CREST's Fortran sources with gfortran on Windows; link options
  `-static-libgfortran -Wl,--wrap=rename -Wl,--stack,<CREST_WINDOWS_STACK>` for `crest.exe` (and
  `test/CMakeLists.txt`: the unit-test program).
- `src/CMakeLists.txt`, `src/meson.build`, `config/meson.build`: the new source, and the same flags
  for meson.
- `.github/workflows/qb-windows.yml`, `.github/qb/`: CI (Windows static + shared, Linux reference,
  comparison) and the smoke cases.

## Results

CI run [42260268137](https://github.com/brandonbtm29/crest/actions/runs/42260268137) (commit `856b9be`):
MSYS2 UCRT64 gcc/gfortran 16.2.0, OpenBLAS 0.3.34, CMake 4.4, Release (`-O3`); Linux reference on
ubuntu-latest (26.04) with gfortran 15.2 and OpenBLAS. Smoke cases (`.github/qb/smoke.py`, 2 threads):

| Case | Linux | Windows static | Windows shared |
|---|---|---|---|
| `--sp --gfn2 --alpb water` (butanol) | -17.7257864996 Eh | -17.7257864996 | -17.7257864996 |
| `--sp --gfnff` (PFBA) | -1.9343648178 | -1.9343648178 | -1.9343648178 |
| `--ancopt --gfn2 --alpb water` (PFBA) | -50.3240491673 | -50.3240491675 | -50.3240491674 |
| iMTD-GC `--gfnff` (butanol): conformers, lowest | 15, -2.11067924 | 14, -2.11067924 | 15, -2.11067924 |
| iMTD-GC `--gfn2 --alpb water --quick` (PFBA) | 19, -50.3240537 | 25, -50.3240412 | 23, -50.3240194 |
| legacy `--gfn2//gfnff --legacy --alpb water --quick` (butanol, xtb 6.7.1) | 22, -17.7284423 | 20, -17.7284569 | 21, -17.7284594 |

- Single points and optimizations agree to 2e-10 Eh. The conformer searches find the same lowest
  conformer to 1e-5 to 3e-5 Eh (0.01-0.02 kcal/mol); their conformer counts change from run to run on
  the same platform too (Linux gave 14 and 15 for butanol, 19 to 23 for PFBA in four runs), because the
  MTD trajectories depend on thread timing.
- After each run the job folders hold the same files on Windows and Linux (the native `rm` cleans up
  `METADYN*`, `NORMMD*`, `calculation.level.*`, `.cre_*`, `TRIALMD`, ...).
- The static `crest.exe` imports only `KERNEL32.dll` and the UCRT (`api-ms-win-crt-*`): one 48 MB
  file (stripped; OpenBLAS's kernels for every CPU make up most of it).
- Wall times on the hosted runners vary by 2x between runs; legacy mode is about 2x slower on Windows
  than on Linux (it starts cmd.exe + xtb.exe for every MD and optimization).

BTM NOVA (Ryzen 9 7900X, Windows 11), the static `crest.exe` of run 42255766640, 2 threads:
the same smoke cases give identical single-point/optimization energies; iMTD-GC GFN-FF butanol 15
conformers in 16.5 s, GFN2/ALPB PFBA `--quick` 24 conformers in 61.8 s (lowest -50.32406431 Eh).
QuantaBench's CREST engine (`chem/crest.py`, unchanged) runs it when given by `QUANTABENCH_CREST`:
butanol GFN-FF 14 conformers in 3.1 s, PFBA GFN2/ALPB(water) 17 conformers in 60.9 s (QuantaBench's
`OMP_STACKSIZE=4G`), and its cancel stops `crest.exe` within the poll interval.

Unit tests (`ctest -R crest/`, run as upstream's CI does with `OMP_NUM_THREADS=1,2,1`): CN, gfn0 and
gfn0occ pass; tblite, gfnff and optimization stop with a segmentation fault at their first calculation
in both Windows builds. All of them pass with `OMP_NUM_THREADS=1`, and they also pass with `1,2,1` when the
program runs under gdb (with or without the debug heap), so the crash needs the nested thread-count
list and real timing (run [42262431070](https://github.com/brandonbtm29/crest/actions/runs/42262431070)).
`crest.exe` itself only gets a single thread count (QuantaBench sets one), and its own nested
parallelism works: on NOVA, `--mdopt` of 2- and 3-structure ensembles with `-T 4` (2 jobs x 2 threads,
`omp_set_max_active_levels(2)`, tblite's parallel regions inside) terminated normally 9 times out of 9.
Open, see below.

## Remaining work

| Item | Effort |
|---|---|
| The unit-test crash with `OMP_NUM_THREADS=1,2,1` outside a debugger (above): a libgomp/winpthreads or tblite nested-region problem; reproduce in a small program, then report upstream | 0.5-1 day |
| A release with the static `crest.exe` (zip with the notices), then QuantaBench: download it in `install_windows.ps1`, pin it in `fork-packages.json`, change the CREST install hint ("no Windows build: use WSL") | 0.5 day |
| `OMP_STACKSIZE`: winpthreads commits (not only reserves) a thread's stack, so QuantaBench's default 4G per thread costs commit charge on Windows; fine at 2 threads, check at 12 or lower it for native CREST | 1-2 h |
| Test the other run modes that use external programs through the cmd.exe translation: QCG (xtb, xtbiff), `--legacy` run types other than `-gfn2//gfnff`, the xtb/ORCA subprocess calculators, `--thermo`, property modes | 0.5-1 day |
| Port what is left: `--scratch` (`mktemp`, `scp`), ConfSolv (`nohup ... &`, `lsof`), MSREACT and the deprecated modes (`execute_command_line` directly) | 1 day |
| Paths with spaces: CREST never quotes paths in its command lines (all platforms); a job folder or xtb path with a space breaks the legacy modes | 2-4 h |
| Upstream: the format-string commas and the `gettime` `close` are platform-independent fixes worth a small PR on their own; the Windows port could follow (upstream has declined Windows so far) | 0.5 day |
| macOS: nothing needed (upstream CI and conda-forge build it); a macOS job here only if the Mac should run exactly this branch | 2 h |
