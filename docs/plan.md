# TPC-H single-node spill profile (Quent) — reproduction plan

Run TPC-H at increasing scale factors so you can see **when Sirius leaves GPU-only, then spills to host, then to disk**. Paths, SFs, and memory budgets come from `config.env` — do not hard-code a laptop.

## Quick path

```bash
cp config.env.example config.env
./scripts/detect-hardware.sh
# edit SIRIUS_ROOT, DATA_ROOT, SCALE_FACTORS, HOST_CAPACITY, FORCE_DISK_HOST_CAPS
./scripts/run-all.sh
./scripts/run-quent.sh
./scripts/quent-links.sh --landing
```

## Phases

### 0 — machine + build + data

1. `./scripts/detect-hardware.sh` — GPU name, compute cap → `CUDAARCHS`, VRAM, RAM, disk free.
2. `./scripts/build-sirius.sh` — pixi + single-arch `nvcc`. Resets a dirty testcontainers FetchContent clone if a previous configure left the patch applied.
3. `./scripts/generate-tpch.sh` — parquet via Sirius `test/tpch_performance/generate_tpch_data.sh` into `$DATA_ROOT/sf<SF>`. Layout `table/part.N.parquet`. Skips an SF if the directory already exists.
4. `./scripts/render-sirius-yaml.sh` — writes `$DATA_ROOT/sirius.yaml` with GPU/host/disk + Quent postcard.

Rule of thumb: parquet ≈ **0.26 GiB per SF unit** on tpchgen-rs (SF100 ≈ 26G, SF300 ≈ 79G). Budget extra for spill + telemetry.

### 1 — probe (cheap, answers “when does it spill?”)

`./scripts/run-phase1.sh`

- Each `SCALE_FACTORS` × `PROBE_QUERIES` (default `1 6 9`) × `PHASE1_ITERATIONS` (default 1).
- Labels: `spill_sf<SF>_tpch_q<N>_iterK`.
- One SF per DuckDB process so a failure does not wipe earlier telemetry.

Watch: `nvidia-smi -l 2` and `du -sh $DATA_ROOT/spill`.

If `spill/` stays empty and Quent task `DISK` is 0 after the largest SF: host did not fill. Continue to force-disk. Do not regenerate parquet.

If a query OOM-retries then fails: record OOM for that `(SF, query)` and continue.

Pass: one log per SF under `$DATA_ROOT/logs/phase1_sf*.log`; telemetry dir non-empty.

### 1b — force host→disk

`./scripts/run-force-disk.sh`

Walks `FORCE_DISK_HOST_CAPS` (largest first), re-renders YAML, re-runs `FORCE_DISK_SF` × `FORCE_DISK_QUERY` (default SF300 q9) until the newest Quent session's task postcard contains `DISK`. Writes the winning cap to `$DATA_ROOT/logs/host_cap_that_hit_disk.txt`.

On the reference laptop, 40 GiB and 16 GiB host still had `DISK=0`; **8 GiB** was the first hit (`DISK=23`). A box with more RAM will need a different list.

### 2 — read Quent

```bash
./scripts/run-quent.sh
./scripts/quent-links.sh --landing
./scripts/quent-links.sh --markdown full_sf300
```

UI: `http://localhost:$QUENT_PORT` (default 8080). Per query look for:

- MemoryTier GPU / HOST / DISK
- NVTX `sirius::spill::gpu_to_host_chunked`
- NVTX `sirius::downgrade::convert_batch` / `convert_task_batches`
- Task `HOST` / `DISK` counts (`./scripts/summarize-telemetry.sh`)

`print_resource_tree` cannot read postcard (`InvalidData` UTF-8 on `engine/`). Use the UI or `quent-links.sh`.

Combined probe sessions (q1+q6+q9 in one process) are **session-level**: do not claim q1/q6 stayed GPU-only unless you ran them isolated.

### 3 — full 22

`./scripts/run-phase3.sh`

All 22 queries × `PHASE3_ITERATIONS` (default 2, cold+warm), one SF at a time. Labels `full_sf<SF>_tpch_q<N>_iterK`.

Then:

```bash
./scripts/extract-timings.sh "$DATA_ROOT/logs/phase3_sf100.log"
./scripts/summarize-telemetry.sh
./scripts/quent-links.sh --landing full_sf100 full_sf200 full_sf300
```

### 4 — optional nsys

Sirius `performance_test.py --mode nsys-profile`. Separate process from Quent. Not wrapped here.

## Memory ladder (what the YAML means)

| Tier | Knob | When it evicts |
|---|---|---|
| GPU | `GPU_USAGE_LIMIT_FRACTION` (or `GPU_USAGE_LIMIT`) | reserved > `GPU_DOWNGRADE_TRIGGER_FRACTION` → host |
| Host | `HOST_CAPACITY` | reserved > `HOST_DOWNGRADE_TRIGGER_FRACTION` → disk |
| Disk | `DISK_CAPACITY` + `SPILL_DIR` | **off unless `downgrade_root_dirs` is set** (the renderer always sets it) |

Empty `spill/` after a successful query does **not** mean disk was unused: Sirius can write and unlink. Trust Quent task `DISK` and `sys` time.

## Out of scope

- Mounting extra partitions
- DuckDB CPU baseline
- Shipping parquet or telemetry in git
