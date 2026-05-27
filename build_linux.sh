#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="${VENV_DIR:-"$ROOT_DIR/.venv"}"
ANALYSIS_WIDTH="${ANALYSIS_WIDTH:-384}"
BENCH_REPEAT="${BENCH_REPEAT:-3}"

INSTALL_SYSTEM_DEPS=0
RUN_TESTS=1
RUN_FULL_TEST=0
RUN_BENCHMARK=0
CLEAN_FIRST=0

usage() {
  cat <<'EOF'
Usage: ./build_linux.sh [options]

Builds the C analyzer and prepares the Python/Express API pipeline on Linux.

Options:
  --install-system-deps  Install missing Linux packages with the detected package manager.
  --clean                Run make clean before building.
  --skip-tests           Skip Python compile and cached golden tests.
  --full-test            Also run batch_clothing_test.py. This can download videos.
  --benchmark            Run benchmark_performance.py after build/tests.
  --analysis-width N     Set analysis width for tests/benchmark. Default: 384.
  --venv DIR             Set virtualenv directory. Default: .venv
  -h, --help             Show this help.

Environment:
  VENV_DIR               Virtualenv directory override.
  ANALYSIS_WIDTH         Analysis width override.
  BENCH_REPEAT           Benchmark repeat count. Default: 3.
EOF
}

log() {
  printf '\033[1;34m[build]\033[0m %s\n' "$*"
}

warn() {
  printf '\033[1;33m[warn]\033[0m %s\n' "$*" >&2
}

die() {
  printf '\033[1;31m[error]\033[0m %s\n' "$*" >&2
  exit 1
}

has_cmd() {
  command -v "$1" >/dev/null 2>&1
}

while (($#)); do
  case "$1" in
    --install-system-deps)
      INSTALL_SYSTEM_DEPS=1
      ;;
    --clean)
      CLEAN_FIRST=1
      ;;
    --skip-tests)
      RUN_TESTS=0
      ;;
    --full-test)
      RUN_FULL_TEST=1
      ;;
    --benchmark)
      RUN_BENCHMARK=1
      ;;
    --analysis-width)
      shift
      [[ $# -gt 0 ]] || die "--analysis-width requires a value"
      ANALYSIS_WIDTH="$1"
      ;;
    --venv)
      shift
      [[ $# -gt 0 ]] || die "--venv requires a value"
      VENV_DIR="$1"
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      die "unknown option: $1"
      ;;
  esac
  shift
done

if [[ "$(uname -s)" != "Linux" ]]; then
  die "this script is intended for Linux. Current OS: $(uname -s)"
fi

install_system_deps() {
  local packages=(build-essential make gcc python3 python3-venv python3-pip ffmpeg nodejs npm)

  if has_cmd apt-get; then
    sudo apt-get update
    sudo apt-get install -y "${packages[@]}"
  elif has_cmd dnf; then
    sudo dnf install -y gcc make python3 python3-pip python3-virtualenv ffmpeg nodejs npm
  elif has_cmd yum; then
    sudo yum install -y gcc make python3 python3-pip ffmpeg nodejs npm
  elif has_cmd pacman; then
    sudo pacman -Sy --needed base-devel python python-pip ffmpeg nodejs npm
  elif has_cmd apk; then
    sudo apk add --no-cache build-base make python3 py3-pip ffmpeg nodejs npm
  else
    die "unsupported package manager. Install gcc, make, python3, python3-venv, python3-pip, ffmpeg, nodejs, and npm manually."
  fi
}

check_prereqs() {
  local missing=()
  for cmd in make python3 node npm; do
    has_cmd "$cmd" || missing+=("$cmd")
  done

  if ! has_cmd cc && ! has_cmd gcc && ! has_cmd clang; then
    missing+=("cc/gcc/clang")
  fi

  if ! has_cmd ffmpeg; then
    missing+=("ffmpeg")
  fi

  if ((${#missing[@]})); then
    if ((INSTALL_SYSTEM_DEPS)); then
      log "Installing missing system dependencies: ${missing[*]}"
      install_system_deps
    else
      die "missing dependencies: ${missing[*]}. Re-run with --install-system-deps or install them manually."
    fi
  fi
}

ensure_venv() {
  if [[ ! -x "$VENV_DIR/bin/python" ]]; then
    log "Creating Python virtualenv: $VENV_DIR"
    python3 -m venv "$VENV_DIR"
  fi

  log "Installing Python requirements"
  "$VENV_DIR/bin/python" -m pip install --upgrade pip
  "$VENV_DIR/bin/python" -m pip install -r "$ROOT_DIR/requirements.txt"
}

ensure_node_deps() {
  log "Installing Node dependencies"
  if [[ -f "$ROOT_DIR/package-lock.json" ]]; then
    (cd "$ROOT_DIR" && npm ci)
  else
    (cd "$ROOT_DIR" && npm install)
  fi
}

build_c_analyzer() {
  if ((CLEAN_FIRST)); then
    log "Cleaning previous C build"
    make -C "$ROOT_DIR" clean
  fi

  log "Building C analyzer"
  make -C "$ROOT_DIR"
  [[ -x "$ROOT_DIR/clothing_analyzer" ]] || die "clothing_analyzer binary was not created"
}

run_tests() {
  log "Running Python syntax checks"
  "$VENV_DIR/bin/python" -m py_compile \
    "$ROOT_DIR/youtube_frame_fetcher.py" \
    "$ROOT_DIR/batch_clothing_test.py" \
    "$ROOT_DIR/golden_clothing_eval.py" \
    "$ROOT_DIR/benchmark_performance.py"

  log "Running Node syntax check"
  (cd "$ROOT_DIR" && npm run check)

  if compgen -G "$ROOT_DIR/cache/youtube_only/frames/*.jpg" >/dev/null; then
    log "Running cached golden evaluation"
    (cd "$ROOT_DIR" && "$VENV_DIR/bin/python" golden_clothing_eval.py --analysis-width "$ANALYSIS_WIDTH")
  else
    warn "Skipping golden evaluation because cache/youtube_only/frames/*.jpg was not found"
  fi

  if ((RUN_FULL_TEST)); then
    log "Running full batch test. This may download YouTube videos."
    (cd "$ROOT_DIR" && "$VENV_DIR/bin/python" batch_clothing_test.py --analysis-width "$ANALYSIS_WIDTH")
  fi
}

run_benchmark() {
  log "Running benchmark"
  (cd "$ROOT_DIR" && "$VENV_DIR/bin/python" benchmark_performance.py --repeat "$BENCH_REPEAT" --analysis-width "$ANALYSIS_WIDTH")
}

main() {
  log "Project: $ROOT_DIR"
  check_prereqs
  ensure_venv
  ensure_node_deps
  build_c_analyzer

  if ((RUN_TESTS)); then
    run_tests
  fi

  if ((RUN_BENCHMARK)); then
    run_benchmark
  fi

  log "Done"
  log "Binary: $ROOT_DIR/clothing_analyzer"
  log "Python: $VENV_DIR/bin/python"
  log "API: cd $ROOT_DIR && npm start"
}

main "$@"
