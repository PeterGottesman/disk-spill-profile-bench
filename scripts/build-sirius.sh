#!/usr/bin/env bash
# Build Sirius for this machine's GPU only (detect CUDAARCHS, optional pixi install).
set -euo pipefail
# shellcheck source=lib.sh
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib.sh"
load_config

SIRIUS_ROOT="${SIRIUS_ROOT:-$HOME/sirius}"
if [[ ! -d "$SIRIUS_ROOT" ]]; then
  echo "SIRIUS_ROOT does not exist: $SIRIUS_ROOT" >&2
  exit 1
fi

PIXI_BIN="${PIXI_HOME:-$HOME/.pixi}/bin"
NO_TESTS="${BUILD_NO_TESTS:-0}"
RECONFIGURE=0
FROZEN=0
INSTALL_PIXI=1

usage() {
  cat <<'EOF'
Usage: scripts/build-sirius.sh [options]

  --no-tests       Skip sirius_unittest (TEST_BUILD_TARGET=)
  --reconfigure    Always drop build/release CMake cache before building
  --frozen         Pass --frozen to pixi (use existing lock/env only)
  --no-install     Do not download pixi if it is missing
  -h, --help       Show this help

Environment (also via config.env):
  SIRIUS_ROOT      Sirius checkout
  CUDAARCHS        Override detected arch, e.g. 89-real
  PIXI_HOME        Pixi install prefix (default: ~/.pixi)
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --no-tests) NO_TESTS=1 ;;
    --reconfigure) RECONFIGURE=1 ;;
    --frozen) FROZEN=1 ;;
    --no-install) INSTALL_PIXI=0 ;;
    -h|--help) usage; exit 0 ;;
    *)
      echo "unknown option: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
  shift
done

cd "$SIRIUS_ROOT"
export PATH="$PIXI_BIN:$PATH"

if ! command -v pixi >/dev/null 2>&1; then
  if [[ "$INSTALL_PIXI" -eq 0 ]]; then
    echo "pixi not found on PATH and --no-install was set" >&2
    exit 1
  fi
  echo "pixi not found; installing to $PIXI_BIN"
  curl -fsSL https://pixi.sh/install.sh | bash
  export PATH="$PIXI_BIN:$PATH"
fi

if [[ -z "${CUDAARCHS:-}" ]]; then
  if ! command -v nvidia-smi >/dev/null 2>&1; then
    echo "nvidia-smi not found; set CUDAARCHS (e.g. CUDAARCHS=89-real)" >&2
    exit 1
  fi
  cap="$(nvidia-smi --query-gpu=compute_cap --format=csv,noheader | head -n1 | tr -d '[:space:]')"
  if [[ ! "$cap" =~ ^[0-9]+\.[0-9]+$ ]]; then
    echo "could not parse compute capability from nvidia-smi: '$cap'" >&2
    exit 1
  fi
  CUDAARCHS="${cap%%.*}${cap#*.}-real"
fi
export CUDAARCHS

cache="$SIRIUS_ROOT/build/release/CMakeCache.txt"
ninja="$SIRIUS_ROOT/build/release/build.ninja"
if [[ "$RECONFIGURE" -eq 1 ]]; then
  echo "dropping CMake cache (--reconfigure)"
  rm -f "$cache" "$ninja"
elif [[ -f "$cache" ]]; then
  cached="$(sed -n 's/^CMAKE_CUDA_ARCHITECTURES:STRING=//p' "$cache" | head -n1 || true)"
  if [[ "$cached" != "$CUDAARCHS" ]]; then
    echo "cached CMAKE_CUDA_ARCHITECTURES='$cached' != CUDAARCHS='$CUDAARCHS'; reconfiguring"
    rm -f "$cache" "$ninja"
  fi
fi

# FetchContent git-apply of third_party/testcontainers-native.patch fails if a
# previous configure left the clone dirty. Reset it so configure can re-apply.
tc_src="$SIRIUS_ROOT/build/release/_deps/testcontainers_native_src-src"
if [[ -d "$tc_src/.git" ]]; then
  git -C "$tc_src" reset --hard HEAD >/dev/null
fi

echo "pixi:     $(command -v pixi) ($(pixi --version))"
echo "CUDAARCHS=$CUDAARCHS"
if command -v nvidia-smi >/dev/null 2>&1; then
  nvidia-smi --query-gpu=name,compute_cap,driver_version --format=csv
fi

pixi_args=(run)
if [[ "$FROZEN" -eq 1 ]]; then
  pixi_args+=(--frozen)
fi
make_args=(make)
if [[ "$NO_TESTS" -eq 1 ]]; then
  make_args+=(TEST_BUILD_TARGET=)
fi

echo "+ pixi ${pixi_args[*]} ${make_args[*]}"
exec pixi "${pixi_args[@]}" "${make_args[@]}"
