import argparse
import json
import statistics
import subprocess
import time
from pathlib import Path


def timed_run(cmd, cwd, repeat, timeout):
    times = []
    last = None
    for _ in range(repeat):
        start = time.perf_counter()
        last = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout)
        times.append((time.perf_counter() - start) * 1000.0)
    return {
        "cmd": " ".join(str(part) for part in cmd),
        "repeat": repeat,
        "status": last.returncode if last else None,
        "avg_ms": round(statistics.mean(times), 3),
        "min_ms": round(min(times), 3),
        "max_ms": round(max(times), 3),
        "stderr": (last.stderr or "")[-800:] if last else "",
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--repeat", type=int, default=5)
    parser.add_argument("--analysis-width", type=int, default=384)
    args = parser.parse_args()

    base_dir = Path(__file__).resolve().parent
    python_path = base_dir / ".venv" / "bin" / "python"
    if not python_path.exists():
        python_path = Path("python3")

    make_result = subprocess.run(["make"], cwd=base_dir, capture_output=True, text=True, timeout=60)
    ppm_dir = base_dir / "cache" / "youtube_only" / "ppm"
    ppms = sorted(ppm_dir.glob("*.ppm"))[:30] if ppm_dir.exists() else []

    result = {
        "make_status": make_result.returncode,
        "ppm_count": len(ppms),
        "benchmarks": {},
    }

    if ppms:
        analyzer = base_dir / "clothing_analyzer"
        result["benchmarks"]["c_single_first_ppm"] = timed_run(
            [analyzer, ppms[0]],
            base_dir,
            args.repeat,
            60,
        )
        result["benchmarks"]["c_batch_10_ppm"] = timed_run(
            [analyzer, *ppms[:10]],
            base_dir,
            args.repeat,
            60,
        )

    result["benchmarks"]["batch_pipeline_cached"] = timed_run(
        [python_path, "batch_clothing_test.py", "--analysis-width", str(args.analysis_width)],
        base_dir,
        args.repeat,
        900,
    )

    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
