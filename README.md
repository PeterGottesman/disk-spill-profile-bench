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

## Checklist on a new machine

- [ ] Sirius clone at `SIRIUS_ROOT`, `pixi` available (or let `build-sirius.sh` install it)
- [ ] `nvidia-smi` works; `CUDAARCHS` detected or set
- [ ] `DATA_ROOT` is on NVMe with enough free space
- [ ] `config.env` has host/disk budgets you actually want
- [ ] Phase 1 logs exist; Quent `gpu_to_host_chunked` > 0 before you run all 22
- [ ] Force-disk either observed `DISK` or you recorded that host never filled
- [ ] `./scripts/quent-links.sh --landing` prints clickable UI URLs
