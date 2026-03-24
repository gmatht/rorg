from __future__ import annotations

import argparse
import json
import os
import secrets
import shutil
import statistics
import subprocess
import tempfile
import time
from pathlib import Path


COMPRESSIONS = ("none", "zstd,3", "lzma,6")

# Payload modes for codec/dedup sensitivity (default `zero` matches legacy script behavior).
PAYLOAD_MODES = ("zero", "random", "mixed", "text")


def run_cmd(cmd: list[str]) -> tuple[float, float]:
    """Run `cmd`; return (wall-clock seconds, process CPU seconds)."""
    wall0 = time.perf_counter()
    cpu0 = time.process_time()
    subprocess.run(cmd, check=True)
    wall = time.perf_counter() - wall0
    cpu = time.process_time() - cpu0
    return wall, cpu


def create_file(path: Path, size_bytes: int, mode: str = "zero") -> None:
    """Write `size_bytes` to `path`. Modes: zero (all NUL), random, mixed (non-trivial pattern), text (compressible)."""
    chunk_size = 1024 * 1024
    if mode == "zero":
        chunk = b"\0" * chunk_size
        with path.open("wb") as fd:
            remaining = size_bytes
            while remaining > 0:
                n = min(remaining, len(chunk))
                fd.write(chunk[:n])
                remaining -= n
        return
    if mode == "random":
        with path.open("wb") as fd:
            remaining = size_bytes
            while remaining > 0:
                n = min(remaining, chunk_size)
                fd.write(secrets.token_bytes(n))
                remaining -= n
        return
    if mode == "mixed":
        # Non-zero repeating pattern (still highly compressible; differs from all-zero dedup behavior).
        pattern = bytes(range(256)) * (chunk_size // 256)
        with path.open("wb") as fd:
            remaining = size_bytes
            while remaining > 0:
                n = min(remaining, len(pattern))
                fd.write(pattern[:n])
                remaining -= n
        return
    if mode == "text":
        line = b"The quick brown fox jumps over the lazy dog.\n" * 500
        with path.open("wb") as fd:
            remaining = size_bytes
            while remaining > 0:
                n = min(remaining, len(line))
                fd.write(line[:n])
                remaining -= n
        return
    raise ValueError(f"unknown payload mode: {mode!r}, expected one of {PAYLOAD_MODES}")


def benchmark(
    borg_cmd: list[str],
    runs: int,
    size_mib: int,
    create_extra_args: list[str] | None = None,
    *,
    payload_mode: str = "zero",
    num_files: int = 1,
):
    results: dict[str, list[tuple[float, float]]] = {c: [] for c in COMPRESSIONS}
    create_extra_args = create_extra_args or []
    if num_files < 1:
        raise ValueError("num_files must be >= 1")
    size_bytes = size_mib * 1024 * 1024
    with tempfile.TemporaryDirectory(prefix="borg-rust-bench-") as tmp:
        root = Path(tmp)
        data_paths: list[str] = []
        for fi in range(num_files):
            data_file = root / (f"payload-{fi}.bin" if num_files > 1 else "payload.bin")
            create_file(data_file, size_bytes, mode=payload_mode)
            data_paths.append(str(data_file))
        for compression in COMPRESSIONS:
            for idx in range(runs):
                repo = root / f"repo-{compression.replace(',', '-')}-{idx}"
                if repo.exists():
                    shutil.rmtree(repo)
                run_cmd([*borg_cmd, "--repo", str(repo), "repo-create", "--encryption", "none"])
                wall_cpu = run_cmd(
                    [
                        *borg_cmd,
                        "--repo",
                        str(repo),
                        "create",
                        "--compression",
                        compression,
                        *create_extra_args,
                        f"bench-{idx}",
                        *data_paths,
                    ]
                )
                results[compression].append(wall_cpu)
    return results


def append_markdown(
    path: Path,
    step_name: str,
    results: dict[str, list[tuple[float, float]]],
    *,
    payload_mode: str,
    num_files: int,
) -> None:
    with path.open("a", encoding="utf-8") as fd:
        fd.write(f"\n## {step_name}\n\n")
        fd.write(f"- payload: `{payload_mode}`")
        if num_files > 1:
            fd.write(f", {num_files} files × same size")
        fd.write("\n")
        for compression, runs in results.items():
            walls = [w for w, _ in runs]
            cpus = [c for _, c in runs]
            med_wall = statistics.median(walls)
            med_cpu = statistics.median(cpus)
            fd.write(f"### {compression}\n")
            fd.write(
                f"- wall (s): {', '.join(f'{x:.3f}' for x in walls)}\n"
                f"- CPU (s): {', '.join(f'{x:.3f}' for x in cpus)}\n"
                f"- median wall: {med_wall:.3f}s\n"
                f"- median CPU: {med_cpu:.3f}s\n\n"
            )


def benchmark_pool_setup(
    python_cmd: list[str],
    borg_pythonpath: str,
    *,
    iterations: int,
    jobs_list: list[int],
) -> dict[int, list[tuple[float, float]]]:
    cmd_base = [*python_cmd, "-c"]
    results: dict[int, list[tuple[float, float]]] = {}
    for jobs in jobs_list:
        py_code = (
            "import json; "
            "from borg import rust_bridge; "
            f"runs = rust_bridge.benchmark_pool_setup(iterations={iterations}, jobs={jobs}); "
            "print(json.dumps(runs))"
        )
        env = os.environ.copy()
        env["PYTHONPATH"] = borg_pythonpath
        proc = subprocess.run([*cmd_base, py_code], check=True, capture_output=True, text=True, env=env)
        runs = proc.stdout.strip()
        pairs = json.loads(runs)
        results[jobs] = [(float(w), float(c)) for w, c in pairs]
    return results


def append_pool_markdown(path: Path, step_name: str, results_ms: dict[int, list[tuple[float, float]]]) -> None:
    with path.open("a", encoding="utf-8") as fd:
        fd.write(f"\n## {step_name}\n\n")
        fd.write("| jobs | wall runs (ms) | wall median (ms) | CPU runs (ms) | CPU median (ms) |\n")
        fd.write("|---:|---|---:|---|---:|\n")
        for jobs in sorted(results_ms):
            runs = results_ms[jobs]
            walls = [w for w, _ in runs]
            cpus = [c for _, c in runs]
            med_wall = statistics.median(walls) if walls else float("nan")
            med_cpu = statistics.median(cpus) if cpus else float("nan")
            fd.write(
                f"| {jobs} | {', '.join(f'{v:.3f}' for v in walls)} | {med_wall:.3f} | "
                f"{', '.join(f'{v:.3f}' for v in cpus)} | {med_cpu:.3f} |\n"
            )
        fd.write("\n")


def main() -> None:
    parser = argparse.ArgumentParser(description="Benchmark borg rust pipeline checkpoints.")
    parser.add_argument(
        "--borg-cmd",
        nargs="+",
        default=["python", "-m", "borg"],
        help="Borg command tokens, e.g. --borg-cmd borg or --borg-cmd python -m borg",
    )
    parser.add_argument("--runs", type=int, default=5, help="Runs per compression")
    parser.add_argument("--size-mib", type=int, default=64, help="Payload size in MiB")
    parser.add_argument("--step", required=True, help="Checkpoint name to append")
    parser.add_argument("--out", default="rust_benchmark.md", help="Markdown output file")
    parser.add_argument(
        "--create-extra-args",
        nargs="*",
        default=[],
        help="Extra args inserted into `borg create` before archive name/path",
    )
    parser.add_argument("--jobs", type=int, default=None, help="If set, pass -j JOBS to borg create")
    parser.add_argument("--pool-setup-bench", action="store_true", help="Run Rayon pool setup microbenchmark")
    parser.add_argument("--pool-iterations", type=int, default=25, help="Iterations per jobs value for pool bench")
    parser.add_argument(
        "--pool-jobs",
        nargs="*",
        type=int,
        default=[1, 2, 4],
        help="Jobs values used for pool setup microbenchmark",
    )
    parser.add_argument(
        "--python-cmd",
        nargs="+",
        default=["python"],
        help="Python command for pool setup benchmark, e.g. --python-cmd python3",
    )
    parser.add_argument(
        "--borg-pythonpath",
        default="src",
        help="PYTHONPATH used to import borg for pool setup microbenchmark",
    )
    parser.add_argument(
        "--payload-mode",
        choices=PAYLOAD_MODES,
        default="zero",
        help="Payload content: zero (legacy), random, mixed pattern, or compressible text",
    )
    parser.add_argument(
        "--num-files",
        type=int,
        default=1,
        help="Number of payload files to archive (each --size-mib); paths are passed to one create",
    )
    args = parser.parse_args()

    out_path = Path(args.out)
    if args.pool_setup_bench:
        pool_results = benchmark_pool_setup(
            args.python_cmd,
            args.borg_pythonpath,
            iterations=args.pool_iterations,
            jobs_list=args.pool_jobs,
        )
        append_pool_markdown(out_path, args.step, pool_results)
    else:
        create_extra_args = list(args.create_extra_args)
        if args.jobs is not None:
            create_extra_args.extend(["-j", str(args.jobs)])
        results = benchmark(
            args.borg_cmd,
            args.runs,
            args.size_mib,
            create_extra_args=create_extra_args,
            payload_mode=args.payload_mode,
            num_files=args.num_files,
        )
        append_markdown(
            out_path,
            args.step,
            results,
            payload_mode=args.payload_mode,
            num_files=args.num_files,
        )


if __name__ == "__main__":
    main()
