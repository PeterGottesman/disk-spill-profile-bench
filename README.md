# Sirius disk-spill profile bench

Reproduce a **single-node GPU → host → disk** spill profile on TPC-H, with Quent telemetry. Every path and memory budget is a knob so a new machine (different GPU, more RAM, more disk) can replay the same phases without editing scripts.

This repo is a **harness**, not Sirius itself. Point `SIRIUS_ROOT` at a built Sirius checkout.

## Quick path

```bash
git clone git@github.com:aocsa/disk-spill-profile-bench.git
cd disk-spill-profile-bench
cp config.env.example config.env
./scripts/detect-hardware.sh          # prints suggested CUDAARCHS / HOST_CAPACITY
$EDITOR config.env                    # SIRIUS_ROOT, DATA_ROOT, SCALE_FACTORS, HOST_CAPACITY
./scripts/run-all.sh                  # build + parquet + phase 1 + force-disk + phase 3
./scripts/run-quent.sh                # http://localhost:8080
./scripts/quent-links.sh --landing full_sf100 full_sf200 full_sf300
./scripts/summarize-telemetry.sh       # run ID, labels, session, spill counts
```

Skip steps you already have:

```bash
./scripts/run-all.sh --skip-build --skip-generate
```

## What you get

| Phase | Script | Question it answers |
|---|---|---|
| 0 detect | `scripts/detect-hardware.sh` | GPU arch, VRAM, RAM, disk free |
| 0 build | `scripts/build-sirius.sh` | Single-arch CUDA build (`CUDAARCHS` auto from `nvidia-smi`) |
| 0 data | `scripts/generate-tpch.sh` | Parquet at `DATA_ROOT/sf<SF>` |
| 0 config | `scripts/render-sirius-yaml.sh` | `sirius.yaml` with GPU/host/disk + Quent |
| 1 probe | `scripts/run-phase1.sh` | At which SF does GPU→host start? (`PROBE_QUERIES`, default `1 6 9`) |
| 1b disk | `scripts/run-force-disk.sh` | Walk `FORCE_DISK_HOST_CAPS` until Quent task records show `DISK` |
| 2 UI | `scripts/run-quent.sh` + `quent-links.sh` | Timeline URLs per labeled query |
| 3 full 22 | `scripts/run-phase3.sh` | All 22 queries × `PHASE3_ITERATIONS` per SF |

Reference results from an RTX 4070 Laptop (8 GiB VRAM, 62 GiB RAM, 2026-09-17): [results/rtx4070-laptop-2026-09-17.md](results/rtx4070-laptop-2026-09-17.md). Full runbook: [docs/plan.md](docs/plan.md).

A GB300 study across seven scale factors, all 22 queries, and three host capacities is in the [comprehensive spill report](results/comprehensive-spill-report-2026-09-28.html), with [query-level CSV](results/comprehensive-spill-matrix-2026-09-28.csv) and [JSON](results/comprehensive-spill-matrix-2026-09-28.json).

## Configure for a bigger box

Copy `config.env.example` → `config.env`. The knobs that almost always change:

| Knob | Laptop default | Bigger box |
|---|---|---|
| `SIRIUS_ROOT` | `$HOME/sirius` | wherever Sirius is cloned |
| `DATA_ROOT` | `$HOME/data/tpch` | fast NVMe with room for parquet (~0.26 GiB × SF) + spill |
| `SCALE_FACTORS` | `100 200 300` | e.g. `100 300 1000` |
| `CUDAARCHS` | auto (`89-real` on 4070) | auto, or `90a-real` / `100f-real` |
| `GPU_USAGE_LIMIT_FRACTION` | `0.9` | leave; or set `GPU_USAGE_LIMIT=40GiB` |
| `HOST_CAPACITY` | `8GiB` | start ~60% of RAM, then shrink until disk shows up |
| `FORCE_DISK_HOST_CAPS` | `40GiB 16GiB 8GiB` | e.g. `256GiB 64GiB 16GiB` |
| `DISK_CAPACITY` | `200GiB` | whatever the NVMe can spare |
| `SCAN_TASK_BATCH_SIZE` (and hash/concat/build) | `768MiB` | raise toward ~VRAM/10 on large GPUs |
| `PIPELINE_THREADS` | `4` | match CPU / GPU count |

**Do not** pass Sirius's bundled `tpch_telemetry_sirius.yaml` as `--config`: it has no memory/disk caps and wipes this ladder. The harness always renders its own YAML.

**Do not** use `--pinning-mode pinned-hot`: pinning hides spill.

## Layout

```
config.env.example     knobs (copy to config.env, gitignored)
scripts/               all runnable steps
docs/plan.md           reproduction plan (machine-agnostic)
docs/notes.md          hardness / traps learned on the laptop run
results/               dated result write-ups (this box, then others)
```

Parquet, spill files, telemetry, and logs stay under `DATA_ROOT` (not in git).

## Keep runs separate

Each `run-all.sh` invocation creates a run ID such as `20260928T143012123456789Z_12345`. The same ID appears in its Quent query labels (`full_sf100_run_<ID>`), the telemetry summary, and logs under `DATA_ROOT/logs/<ID>/`. Separate invocations keep their logs and labels distinct. Old sessions appear as `(legacy)` in the summary.

```bash
./scripts/summarize-telemetry.sh                 # all runs
ID=20260928T143012123456789Z_12345     # copy an ID from the summary
./scripts/summarize-telemetry.sh --run-id "$ID"  # one run
./scripts/extract-timings.sh "$DATA_ROOT/logs/$ID/phase3_sf100.log"
```

For separate phase commands that belong to one run, set `BENCH_RUN_ID` to the same value for each command. Use only letters, digits, underscores, or hyphens. Without it, each phase command creates its own ID.

## Checklist on a new machine

- [ ] Sirius clone at `SIRIUS_ROOT`, `pixi` available (or let `build-sirius.sh` install it)
- [ ] `nvidia-smi` works; `CUDAARCHS` detected or set
- [ ] `DATA_ROOT` is on NVMe with enough free space
- [ ] `config.env` has host/disk budgets you actually want
- [ ] Phase 1 logs exist; Quent `gpu_to_host_chunked` > 0 before you run all 22
- [ ] Force-disk either observed `DISK` or you recorded that host never filled
- [ ] `./scripts/quent-links.sh --landing` prints clickable UI URLs

## Repeat a spill scaling study on another machine

Use the manifest-driven matrix runner when comparing query runtime with HOST and DISK spilling. Set `SIRIUS_ROOT`, `DATA_ROOT`, and any GPU/executor settings in a local `config.env` first. The first HOST cap is the runtime reference; keep the same cap ordering and scale factors when comparing machines.

```bash
./scripts/detect-hardware.sh
./scripts/run-matrix.py --host-caps 196GiB 8GiB 1GiB \
  --scale-factors 1 10 100 250 500 750 1000 \
  --repetitions 3 --iterations 2 --dry-run
./scripts/run-matrix.py --host-caps 196GiB 8GiB 1GiB \
  --scale-factors 1 10 100 250 500 750 1000 \
  --repetitions 3 --iterations 2 --generate --id my_machine_20260928
source config.env
./scripts/build-spill-report.py \
  --manifest "$DATA_ROOT/experiments/my_machine_20260928/manifest.json"
```

Choose HOST caps that fit the machine's RAM; the first should be a practical high-capacity reference. Set `--output` to place the experiment elsewhere. Omit `--generate` if all Parquet datasets exist. The runner validates all eight tables and their Parquet footers before benchmarking; it will not replace an existing invalid dataset. It requires a built Sirius checkout and `pixi` on `PATH`. `--resume` skips completed entries in an existing manifest. An interrupted entry with telemetry must be rerun under a new experiment ID to avoid mixing attempts.

Each experiment has a JSON manifest with machine metadata, revisions, dataset checks, workload settings, and run status. Every host-capacity/repetition run has its own config snapshot, rendered YAML, spill directory, Quent NDJSON, timing logs, and analyzed metrics. The report builder writes `report/report.html`, `measurements.csv`, and `measurements.json` beside the manifest. It requires complete timings and telemetry for every query and iteration. The HTML uses the last iteration of each run, summarizes each 22-query repetition first, then reports medians and runtime ranges across repetitions. HOST/DISK GiB are cumulative logical placement volumes from Quent batch capacity, not measured physical disk I/O.

For a cross-machine comparison, copy each machine's experiment directory and report. Compare the same Sirius revision, dataset generation method, scale factors, query order, and HOST caps where feasible. Record different hardware or executor settings in the manifest rather than hiding them in a shared report. `BENCH_CONFIG_PATH` can point legacy scripts at a particular config snapshot.
