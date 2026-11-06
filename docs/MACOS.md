# CREST on macOS arm64 (fork `brandonbtm29/crest`, branch `qb/macos`)

This branch is `qb/windows` (upstream `master` `cfdc301` plus the Windows port, see
[WINDOWS.md](WINDOWS.md)) plus a macOS arm64 build in CI: `.github/workflows/qb-macos.yml`. No CREST
source changes beyond `qb/windows`; on macOS only its format-string commas matter.

## Why: conda-forge's osx-arm64 `crest 3.0.2` does not optimize

On an M3 Max MacBook Pro (macOS 27.2), conda-forge's `crest 3.0.2` (build `hb2118c8_1`) stops with
"Initial geometry optimization failed!" for every molecule of 19 atoms or more, in the gas phase and in
ALPB(water), with GFN2-xTB and GFN-FF. Smaller molecules pass the first optimization. Single points are
right. Two defects add up:

1. **It is linked to Apple's Accelerate.** The binary loads
   `/System/Library/Frameworks/Accelerate.framework` directly, not conda's `libblas`/`liblapack`
   (OpenBLAS): with no `BLA_VENDOR`, CMake's FindBLAS/FindLAPACK take Accelerate before a generic
   `libblas`. Accelerate's classic BLAS/LAPACK interface keeps the f2c calling convention: functions of
   type REAL return a double. CREST declares `real(sp), external :: sdot`, so every `sdot` it calls reads
   garbage (Accelerate's `sdot_` read as a float: 3.7e19 for a dot product of 569.8). ANCOPT uses
   single-precision `sdot` in its rational-function step through the Davidson solver, which it takes
   when `nvar + 1 >= 50` (3N - 6 + 1, i.e. 19 atoms or more); below that it calls `sspevx`, which works.
   That is why trifluoroacetic acid (7 atoms) and C5HF11O (18 atoms) got through and everything larger
   failed. The same binary with its Accelerate load command changed to conda's `libopenblas.0.dylib`
   (`install_name_tool -change`, ad-hoc re-signed) optimizes and finishes iMTD-GC for a 20-atom molecule
   (GFN-FF 2 s, GFN2 29 s). Interposing only `sdot_` was not enough, so at least one more routine of
   Accelerate's classic interface disagrees with gfortran's conventions; relinking all of BLAS/LAPACK to
   OpenBLAS removes the question.
2. **libgfortran 16 rejects formats that lack a comma.** The environment's `libgfortran 16.2.0` stops at
   ANCOPT's first printout (`ancopt.f90:187`, `'(1x,"("f7.2"%)")'`: "Fortran runtime error: Missing
   comma between descriptors"), so `crest --opt`/`--ancopt` exits with code 2 after cycle 0 on any
   platform with gfortran >= 15's runtime. `qb/windows` already adds the commas (11 formats).

Upstream issue crest-lab/crest#412 (Mac mini M4, conda, 3.0.2: NaN geometries, "0 of 826 structures
successfully optimized") looks like the same thing; it has no diagnosis yet.

## The build

`qb-macos.yml` (push to `qb/macos`, or run it by hand):

- `macos-15` runner (arm64) with conda-forge's compilers (clang, gfortran), OpenBLAS (OpenMP variant) and
  LLVM OpenMP from micromamba: the toolchain conda-forge's own `crest` package is built with, plus
  `-DBLA_VENDOR=OpenBLAS`, so Accelerate is never considered (the job fails if `crest` links it). OpenMP is
  LLVM's libomp throughout (conda-forge's gfortran uses it for `-fopenmp`, and so does its OpenBLAS): one
  OpenMP runtime per process; the job fails if libgomp shows up. Homebrew does not fit: its gcc brings
  libgomp, while its OpenBLAS links libomp (two runtimes in one process).
- `.github/qb/macos_bundle.py` turns the build into a folder that runs from anywhere:
  `crest-macos-arm64/bin/crest` and `lib/` with the non-system dylibs it loads (libgfortran, libquadmath,
  libgcc_s, libomp, OpenBLAS), install names `@rpath/...`, rpath `@loader_path/../lib`, ad-hoc
  signatures. The job unpacks the tarball elsewhere and fails if dyld loads any library from outside
  the folder (`DYLD_PRINT_LIBRARIES`).
- Unit tests (`ctest -R crest/`), then the smoke cases of `.github/qb/smoke.py` from the unpacked
  folder, including three new ones that take the Davidson path (20 and 25 atoms) and the legacy-mode
  case with conda-forge's xtb; a Linux build of the same commit runs the same cases and `compare.py`
  puts them side by side (job summary).
- Artifact `crest-macos-arm64`: `crest-macos-arm64.tar.gz` (+ `.sha256`), with COPYING and
  THIRD-PARTY-NOTICES.txt (component licenses, compiler, bundled libraries and their minimum macOS).

Use: `tar -xzf crest-macos-arm64.tar.gz -C <folder>` and run `<folder>/crest-macos-arm64/bin/crest`
(QuantaBench: Settings > Engines > CREST, or `QUANTABENCH_CREST`). Keep `bin/` and `lib/` together.
Files from a browser download get the quarantine attribute: `xattr -dr com.apple.quarantine <folder>`.

## Results

(filled in from the CI run and the MacBook tests)
