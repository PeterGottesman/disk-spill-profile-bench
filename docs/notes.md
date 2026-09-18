# Hardness / traps (laptop run, 2026-09-17)

Things that wasted time or will bite a new machine. Not product docs.

## Build

- **testcontainers FetchContent patch is not idempotent.** `git apply` of `third_party/testcontainers-native.patch` fails if `build/release/_deps/testcontainers_native_src-src` is already patched. Symptom: CMake configure dies before any CUDA compile. Fix (also in `build-sirius.sh`): `git -C …/testcontainers_native_src-src reset --hard HEAD` then `--reconfigure`.
- **Do not treat that as a CUDAARCHS failure.** The first local `scripts/build-local-gpu.sh` run died exactly this way on a dirty clone.
- **Single-arch build.** Default nvcc matrix is ~8 arches and will cook a laptop. Always export `CUDAARCHS` (auto: `compute_cap` 8.9 → `89-real`).

## Config

- **`tpch_telemetry_sirius.yaml` as `--config` drops memory/disk limits.** The TPC-H telemetry helper uses the file as-is. The harness renders its own YAML with Quent + the ladder. Never point `--config` at the bundled telemetry-only file.
- **Disk spilling is off until `memory.disk.downgrade_root_dirs` is set.** Empty string → host fills and the query stalls/OOM instead of going to disk.
- **`enable_batch_events` is required** to see MemoryTier GPU/HOST/DISK on batches. Code default is on; keep it explicit in the rendered YAML.
- **Do not mix `sirius.memory.*` and non-empty `sirius.space.*`.**
- **`--pinning-mode pinned-hot` hides spill** (and OOMed SF500/SF1000 nightlies). Leave pinning at `none`.

## Data

- **Parquet generator skips if `$OUTPUT` exists** (exit 0). Delete the directory to regenerate. Not incremental.
- **Layout is `table/part.N.parquet`**, which the Sirius TPC-H runner accepts (`table.parquet`, `table_*.parquet`, or `table/*.parquet`).
- **tpchgen-rs first run** clones `test_datasets/tpchgen-rs` and `cargo build --release -p tpchgen-cli`. Needs network + rust.
- **Do not use DuckDB `CALL dbgen()`** for these benches: different comment pool, q13/q16 mismatch vs reference.

## Telemetry / Quent

- **Postcard, not ndjson.** `print_resource_tree` panics (`InvalidData` UTF-8 on `engine/`). Use the UI (`run-quent.sh`) or `summarize-telemetry.sh` / `quent-links.sh`.
- **UI route:** `/profile/engine/<engineId>/query/<queryId>`. Engine id ≠ telemetry session directory id. `quent-links.sh` maps them via `/api/engines/:id/contexts`.
- **Combined q1+q6+q9 is one session.** HOST/DISK counts are session-level. Isolate a query (force-disk does this) before claiming per-query GPU-only.
- **Rust TLS panic on DuckDB shutdown** (`cannot access a Thread Local Storage value during or after destruction`) is noise after results printed. `exit 0` is what matters.
- **Spill files often do not persist.** Directory mtime can jump while `du` stays at 4.0K. Sys time going up (e.g. 3.4s → 8.1s) plus task `DISK` > 0 is the signal.

## Workload

- **q9 is the documented partition-spill cliff.** Join-heavy follow-ups: q3, q5, q7, q8, q10, q18, q21. q1/q6 stay scan/agg.
- **q11 may return 0 rows** if TPC-H substitution parameters are not applied. Timing is still valid; do not treat empty result as a crash.
- **Warm is not always faster.** On the laptop, SF300 q18 warm was slower than cold (sys 11.6s) once disk was in play.
- **Host cap is the disk knob, not SF alone.** On 8 GiB VRAM / 62 GiB RAM, SF300 q9 GPU→hosted at 40 GiB and 16 GiB host, and only hit disk at **8 GiB host**. A 128 GiB RAM box with `HOST_CAPACITY=40GiB` may never leave host. Use `FORCE_DISK_HOST_CAPS`.

## Reference laptop numbers (see results/)

- GPU→host already at SF100 (40 GiB host): NVTX `gpu_to_host_chunked` 45 / 139 / 329 for SF100/200/300 probe.
- Isolated SF300 q9: 40 GiB and 16 GiB → GPU+host; 8 GiB → GPU+host+disk (`DISK=23`).
- Full 22 at 8 GiB host: task `DISK` 0 / 10 / 191 across SF100/200/300. SF300 q3 is the time outlier (18.5s / 16.1s).
