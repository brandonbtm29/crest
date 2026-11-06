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

### CI

| Run | What happened |
|---|---|
| [42292664913](https://github.com/brandonbtm29/crest/actions/runs/42292664913) | Homebrew gcc + OpenBLAS: stopped by the OpenMP check (Homebrew's OpenBLAS links libomp, its gfortran libgomp) |
| [42293824880](https://github.com/brandonbtm29/crest/actions/runs/42293824880) | conda-forge toolchain: build, unit tests and package fine; the dyld check misread macOS 15's "move loaded to delayed" lines |
| [42295799792](https://github.com/brandonbtm29/crest/actions/runs/42295799792) | macOS job green, artifact `crest-macos-arm64` (12.9 MB, sha256 `47ba788333b7d89f63c54565330683fd7f558500ec482e7033ad34d064047a33`, commit `12eb61d`); compare red on one quick search (sampling, below) |

Toolchain of `42295799792`: macos-15 runner (15.8.2, Xcode 16.4 SDK 15.5), conda-forge gfortran 15.3.0
(`gfortran_impl_osx-arm64`) with libgfortran 16.2.0, clang 21.1.8, OpenBLAS 0.3.34 (OpenMP variant), llvm-openmp 23.1.5, Release. `bin/crest` needs macOS 14.0,
the bundled libraries 11.0. Unit tests (`ctest -R crest/`, `OMP_NUM_THREADS=1,2,1`): all 6 pass (on Windows three
of them crash with that thread list, see WINDOWS.md).

Smoke cases, Linux reference (ubuntu-latest, apt gfortran + OpenBLAS) vs macOS arm64, 3 threads:

| Case | Linux | macOS | ΔE / Eh |
|---|---|---|---|
| `--sp --gfn2 --alpb water` (butanol) | -17.7257864996 | -17.7257864996 | 0 |
| `--sp --gfnff` (PFBA) | -1.9343648178 | -1.9343648178 | 0 |
| `--ancopt --gfn2 --alpb water` (PFBA, 14 atoms) | -50.3240491675 | -50.3240491674 | 1e-10 |
| `--ancopt --gfn2` (C6H8F4O2, 20 atoms: Davidson step) | -43.9476042666 | -43.9476042665 | 1e-10 |
| `--ancopt --gfnff --alpb water --chrg -1` (PFOA anion, 25 atoms) | -4.7661487112 | -4.7661485395 | 1.7e-7 |
| iMTD-GC `--gfnff` (butanol): n, lowest | 14, -2.11067924 | 14, -2.11067924 | 0 |
| iMTD-GC `--gfn2 --alpb water --quick` (PFBA) | 20, -50.32404434 | 18, -50.32403662 | 7.7e-6 |
| iMTD-GC `--gfnff --quick` (C6H8F4O2) | 68, -2.96186691 | 73, -2.96172498 | 1.4e-4 (Linux's #2) |
| legacy `--gfn2//gfnff --legacy --alpb water --quick` (butanol, conda-forge xtb 6.7.1) | 17, -17.72843791 | 18, -17.72844397 | 6.1e-6 |

The one difference beyond the search tolerance is sampling: -2.96172 Eh is the second conformer of the Linux
ensemble, and six runs of that case with conda-forge's Linux 3.0.2 (2 threads) end at -2.96172 twice and at
-2.96187 four times. compare.py now marks such a case "sampling" instead of failing it.

### MacBook Pro M3 Max (14 cores, macOS 27.2)

The artifact of run 42295799792, unpacked in a folder with a space in its path, runs with nothing installed
(dyld loads only its own `lib/` and the system), and passes `codesign --verify`. A 20-atom molecule that fails with
conda-forge's build: GFN-FF `--quick` 2 s, GFN2 `--quick` 20 s, `--opt --gfn2` converges in 41 cycles
(-43.9476042665 Eh, as on Linux).

Run modes on PFAS, fork build (12 threads) against conda-forge's linux-64 3.0.2 (the build NOVA's WSL runs; here in
a 2-core x86-64 Linux container). Searches: conformers and lowest energy (Eh); counts vary from run to run.

| Mode | Molecule | Linux conda-forge 3.0.2 | macOS fork | ΔE / Eh | Linux s | Mac s |
|---|---|---|---|---|---|---|
| `--sp --gfn2 --alpb water` | PFHxA | -73.4985313539 | -73.4985313539 | 0 | 0.2 | 0.1 |
| `--opt --gfn2` | PFHxA | exit 2: "Missing comma between descriptors" | -73.506395655 | | 0.2 | 0.5 |
| iMTD-GC `--gfnff` | PFHxA | 64, -2.73543684 | 64, -2.73543683 | 1e-8 | 34 | 6.5 |
| iMTD-GC `--gfnff --alpb water` | PFBA⁻ | 5, -3.17941054 | 3, -3.17940767 | 2.9e-6 | 19 | 3.1 |
| iMTD-GC `--gfn2` | PFBA⁻ | 4, -50.09230783 | 6, -50.09230783 | 0 | 513 | 56 |
| iMTD-GC `--gfn2 --alpb water` | PFBA⁻ | 9, -50.17004086 | 8, -50.17004175 | 8.9e-7 | 361 | 44 |
| `--gfn2 --alpb water --quick` | PFHxA | 71, -73.51771653 | 72, -73.51772116 | 4.6e-6 | 257 | 32 |
| `--gfn2 --alpb water --quick` | PFOA⁻ | (not run) | 122, -96.55837569 | | | 302 |
| `--gfn2//gfnff --legacy --alpb water --quick` (xtb 6.7.1) | PFHxA | 195, -73.50734867 | 188, -73.5073318 | 1.7e-5 | 68 | 182 |
| `--cregen` (own GFN-FF ensemble) | PFHxA | 62, -2.73543684 | 64, -2.73543683 | | 0.0 | 0.0 |
| `--protonate --gfn2 --alpb water` | PFBA⁻ | 2, -50.32323564 | 2, -50.32323564 | 0 | 13 | 17 |
| `--deprotonate --gfn2 --alpb water` | PFHxA | 1, -73.36372852 | 1, -73.36372842 | 1e-7 | 7 | 1.1 |
| `--tautomerize --gfn2 --alpb water` | PFHxA | 2, -73.51769471 | 2, -73.51769471 | 0 | 44 | 113 |
| `--thermo vibspectrum` (xtb GFN2/ALPB Hessian of the Mac's best PFHxA) | PFHxA | G = -73.486727340206 | -73.486727340206 | 0 | 0.0 | 0.0 |
| `--entropy --gfnff` | PFBA⁻ | 8, -3.12002896; S(conf) 3.75 | 6, -3.12002896; S(conf) 4.11 cal/(mol K) | 0 | 11 | 4.4 |
| QCG `--qcg water.xyz --nsolv 3` (grow, xtb 6.7.1) | PFBA⁻ | exit 2: "Missing comma between descriptors" | 3 waters; cluster -65.3518 after optimization | | 4.9 | 16 |

- Energies agree to 1e-8 to 2e-5 Eh where both ran; the conformer counts and S(conf) follow the sampling.
- conda-forge's 3.0.2 runs into the libgfortran 16 format bug on Linux too: `--opt` and QCG stop with exit code 2.
  QuantaBench's iMTD-GC runs never print ANCOPT's progress, so NOVA's WSL runs were not affected.
- Legacy mode and `--tautomerize` took longer on the Mac than on 2 Linux cores. Legacy mode starts one xtb
  process per structure (306 final optimizations here); macOS starts processes more slowly. Worth a closer look
  only if QuantaBench uses `gfn2//gfnff` on the Mac in bulk.
