# CREST on native Windows (fork `brandonbtm29/crest`, branch `qb/windows`)

This branch builds CREST as a native Windows program (`crest.exe`, x86-64) with the MSYS2 UCRT64
MinGW-w64 toolchain (gcc/gfortran) and OpenBLAS. Linux and macOS builds are unchanged: every change
below is behind `_WIN32` (C, preprocessed Fortran) or `if(WIN32)` / `host_machine.system() == 'windows'`
(CMake, meson).

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
- anything else goes to `cmd.exe` with `/dev/null` -> `NUL`, `cd DIR &&` -> `cd /d DIR &&`, and the
  program path written with backslashes (cmd.exe reads `/x` as a switch).

Where the commands come from:

| Source | Command lines | Run modes | State on this branch |
|---|---|---|---|
| `cleanup.f90:rmrf` (69 calls in cleanup.f90, used everywhere) | `rm -rf NAMES 2>/dev/null` | all, incl. the default iMTD-GC | native |
| `iomod:checkprog`, `getpath` | `command -v PROG [> pathout.tmp]` | `--legacy`, QCG, turbomole | native |
| legacy xtb runs (`legacy_algos/*`, `--legacy`, `-gfn2//gfnff`, `-gfn2@...`) | `cd DIR && xtb coord --md ... > xtb.out 2>/dev/null` | `--legacy` and the methods that need it | via cmd.exe translation; needs `xtb.exe` (untested here) |
| subprocess calculators (`calculator/xtb_sc.f90`, `orca_sc`, `turbom_sc`, `generic_sc`) | `cd calcspace && BINARY ... > out 2>/dev/null` | TOML input with `type = "xtb"`, `"orca"`, ... | via cmd.exe translation (untested) |
| QCG (`qcg/solvtool*.f90`) | xtb/xtbiff runs, `cd DIR && ...`, `> out 2> /dev/null` | `--qcg` | via cmd.exe translation (untested; needs xtb, optionally xtbiff) |
| `scratch.f90` | `mktemp -d`, `scp -r ./* DIR/` | `--scratch` | not ported |
| `algos/ConfSolv.F90` | `nohup ... &`, `lsof`, `kill -9` | ConfSolv | not ported |
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
| `--gfn2//gfnff`, `--gfn2@gfnff`, ... | `--legacy`, so the `xtb` program ("option not yet available with new calculator") | translation in place, untested |
| `--legacy` (any) | `xtb` program | translation in place, untested |
| QCG (`--qcg`) | `xtb`, optionally `xtbiff` | translation in place, untested |
| ORCA/Turbomole/generic calculators | those programs | translation in place, untested |
| `--scratch`, ConfSolv, MSREACT | POSIX tools | not ported |

## Changes on this branch

- `src/win_compat.c` (new): the Windows replacements above; compiles to nothing elsewhere.
- `src/iomod.F90`: on `_WIN32`, the C bindings point at `crest_win_*` and `command()` calls
  `crest_win_command()`.
- `CMakeLists.txt`: `_WIN32` for CREST's Fortran sources with gfortran on Windows; link options
  `-static-libgfortran -Wl,--wrap=rename -Wl,--stack,<CREST_WINDOWS_STACK>` for `crest.exe` (and
  `test/CMakeLists.txt`: the unit-test program).
- `src/CMakeLists.txt`, `src/meson.build`, `config/meson.build`: the new source, and the same flags
  for meson.
- `.github/workflows/qb-windows.yml`, `.github/qb/`: CI (Windows static + shared, Linux reference,
  comparison) and the smoke cases.

## Results

(filled in from CI runs)

## Remaining work

(see below)
