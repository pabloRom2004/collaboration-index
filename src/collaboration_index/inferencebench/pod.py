"""Probe GPUs, stop processes, archive submissions and prepare the workspace on InferenceBench pods.

The controller uploads this file beside the pinned ``runtime.py`` and runs it
with the pod's evaluator Python; it also imports the parsing, audit and OOM
helpers. Only the ``workspace`` command imports the evaluator packages.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tarfile
import time
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any

LEASE_VAR = "INFERENCEBENCH_GPU_LEASE"
TASK = Path("/home/agent/task")
DEV = Path("/opt/inferencebench-dev")
# exit status of ``pack`` when the task directory exceeds the size limit
TOO_LARGE = 3
LONGBENCH_SAMPLES = 503
# the development baseline server that runtime.baseline_server starts
BASELINE_URL = "http://127.0.0.1:8000"
OOM_PATTERNS = [
    r"CUDA out of memory",
    r"torch\.(?:cuda\.)?OutOfMemoryError",
    r"CUBLAS_STATUS_ALLOC_FAILED",
    r"cudaErrorMemoryAllocation",
    r"CUDA error: out of memory",
    r"No available memory for the cache blocks",
    r"larger than the available KV cache memory",
    r"Insufficient memory to (?:allocate|load)",
    r"out of memory on device",
]


def detect_oom(text: str) -> str | None:
    """Return the first GPU out-of-memory signature found in command output."""
    for pattern in OOM_PATTERNS:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            return match.group(0)
    return None


def number(value: str) -> float | None:
    """Read one nvidia-smi numeric field, treating "[N/A]" style readings as missing."""
    try:
        return float(value)
    except ValueError:
        return None


GPU_QUERY = [
    "nvidia-smi",
    "--query-gpu=index,pci.bus_id,memory.used,memory.total,utilization.gpu,power.draw",
    "--format=csv,noheader,nounits",
]


def parse_gpus(csv: str) -> list[dict[str, Any]]:
    """Parse the CSV that ``GPU_QUERY`` prints, one row per GPU."""
    gpus = []
    for line in csv.splitlines():
        fields = [field.strip() for field in line.split(",")]
        if len(fields) != 6 or not fields[0].isdigit():
            continue
        gpus.append(
            {
                "index": int(fields[0]),
                "bus_id": fields[1].upper(),
                "memory_used_mib": number(fields[2]),
                "memory_total_mib": number(fields[3]),
                "utilization_percent": number(fields[4]),
                "power_watts": number(fields[5]),
            }
        )
    return gpus


def parse_minors(report: str) -> dict[str, int]:
    """Map PCI bus IDs to ``/dev/nvidiaN`` minor numbers from ``nvidia-smi -q``."""
    minors, bus = {}, None
    for line in report.splitlines():
        header = re.match(r"^GPU\s+([0-9A-Fa-f:.]+)\s*$", line)
        if header:
            bus = header.group(1).upper()
        minor = re.match(r"^\s+Minor Number\s+:\s+(\d+)", line)
        if minor and bus:
            minors[bus] = int(minor.group(1))
    return minors


def parse_visible(value: bytes | None) -> list[int] | None:
    """Read CUDA_VISIBLE_DEVICES as GPU indices, or None when unset or not plain indices."""
    if value is None:
        return None
    items = [item.strip() for item in value.split(b",") if item.strip()]
    if not all(item.isdigit() for item in items):
        return None
    return [int(item) for item in items]


def launched_for(item: dict[str, Any]) -> set[int]:
    """Return the GPUs a process with an open GPU handle was started for."""
    gpus = item.get("gpus", [])
    if not gpus:
        return set()
    # device enumeration (for example NVML) opens every GPU's handle, so the
    # CUDA_VISIBLE_DEVICES that lease commands set decides; its indices match
    # nvidia-smi because lease commands also set CUDA_DEVICE_ORDER=PCI_BUS_ID
    visible = item.get("visible")
    return set(gpus if visible is None else visible)


def scan_processes(proc: str = "/proc") -> list[dict[str, Any]]:
    """List processes that hold an NVIDIA device or carry a GPU lease token."""
    found = []
    for entry in os.scandir(proc):
        if not entry.name.isdigit():
            continue
        try:
            minors = set()
            for handle in os.scandir(os.path.join(entry.path, "fd")):
                device = re.fullmatch(r"/dev/nvidia(\d+)", os.readlink(handle.path))
                if device:
                    minors.add(int(device.group(1)))
            environ = Path(entry.path, "environ").read_bytes().split(b"\0")
            command = Path(entry.path, "cmdline").read_bytes()
        except OSError:
            # processes exit and hide their files while being scanned
            continue
        variables = dict(item.partition(b"=")[::2] for item in environ if item)
        token = variables.get(LEASE_VAR.encode())
        if minors or token:
            found.append(
                {
                    "pid": int(entry.name),
                    "minors": sorted(minors),
                    "token": token.decode(errors="replace") if token else None,
                    "visible": parse_visible(variables.get(b"CUDA_VISIBLE_DEVICES")),
                    "command": command.replace(b"\0", b" ")
                    .decode(errors="replace")[:160]
                    .strip(),
                }
            )
    return found


def attach_indices(
    processes: list[dict[str, Any]], gpus: list[dict[str, Any]], minors: dict[str, int]
) -> None:
    """Translate device minors into nvidia-smi indices; a minor without a known bus keeps its number."""
    index = {minors[g["bus_id"]]: g["index"] for g in gpus if g["bus_id"] in minors}
    for item in processes:
        item["gpus"] = sorted(index.get(minor, minor) for minor in item["minors"])


def run(command: list[str]) -> str:
    """Run a read-only system query and return its standard output."""
    return subprocess.run(
        command, capture_output=True, text=True, check=True, timeout=60
    ).stdout


def probe(
    proc: str = "/proc", query: Callable[[list[str]], str] = run
) -> dict[str, Any]:
    """Report GPU memory, utilization, power and the attributed processes on every GPU."""
    gpus = parse_gpus(query(GPU_QUERY))
    processes = scan_processes(proc)
    attach_indices(processes, gpus, parse_minors(query(["nvidia-smi", "-q"])))
    return {"time": time.time(), "gpus": gpus, "processes": processes}


def audit(sample: dict[str, Any], holders: dict[int, str]) -> list[dict[str, Any]]:
    """List each GPU that a process uses outside its own active lease."""
    slots_of: dict[str, set[int]] = {}
    for slot, token in holders.items():
        slots_of.setdefault(token, set()).add(slot)
    violations = []
    for item in sample["processes"]:
        # nvidia-smi opens every GPU
        if item["command"].split(" ", 1)[0].endswith("nvidia-smi"):
            continue
        gpus = set(item.get("gpus", []))
        own = slots_of.get(item["token"], set()) if item["token"] else set()
        # a process without an active lease uses every GPU it opened; a leased
        # process uses the GPUs it was started for, or every GPU it opened when
        # none of them is its lease's
        outside = gpus
        if own:
            outside = launched_for(item) - own
            if gpus and not gpus & own:
                outside |= gpus
        for gpu in sorted(outside):
            violations.append(
                {
                    "gpu": gpu,
                    "pid": item["pid"],
                    "token": item["token"],
                    "command": item["command"],
                }
            )
    return violations


def ancestors(pid: int, proc: str = "/proc") -> set[int]:
    """Return this command's process chain so a kill never stops its own transport."""
    chain = {pid}
    while pid > 1:
        try:
            status = Path(proc, str(pid), "status").read_text()
        except OSError:
            break
        parent = re.search(r"^PPid:\s+(\d+)", status, flags=re.MULTILINE)
        if not parent:
            break
        pid = int(parent.group(1))
        chain.add(pid)
    return chain


def alive(pid: int) -> bool:
    """Report whether a process still exists."""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        pass
    return True


def listeners(ports: set[int], proc: str = "/proc") -> set[int]:
    """Return PIDs that hold a TCP socket listening on one of the ports."""
    inodes = set()
    for name in ("tcp", "tcp6"):
        try:
            rows = Path(proc, "net", name).read_text().splitlines()[1:]
        except OSError:
            continue
        for line in rows:
            fields = line.split()
            # field 3 is the socket state; 0A means LISTEN
            if len(fields) > 9 and fields[3] == "0A":
                if int(fields[1].rsplit(":", 1)[1], 16) in ports:
                    inodes.add(f"socket:[{fields[9]}]")
    pids = set()
    for entry in os.scandir(proc) if inodes else []:
        if not entry.name.isdigit():
            continue
        try:
            if any(
                os.readlink(handle.path) in inodes
                for handle in os.scandir(os.path.join(entry.path, "fd"))
            ):
                pids.add(int(entry.name))
        except OSError:
            continue
    return pids


def kill(
    tokens: Iterable[str],
    gpus: Iterable[int],
    pids: Iterable[int] = (),
    ports: Iterable[int] = (),
    scoped: Iterable[tuple[str, int]] = (),
    proc: str = "/proc",
    query: Callable[[list[str]], str] = run,
    send: Callable[[int, int], None] = os.kill,
    exists: Callable[[int], bool] = alive,
    grace: float = 10.0,
) -> list[int]:
    """Stop processes by lease token, PID, GPU, listening port or (token, GPU started for), then SIGKILL survivors."""
    tokens, gpus, pids, scoped = set(tokens), set(gpus), set(pids), set(scoped)
    processes = scan_processes(proc)
    if gpus or scoped:
        attach_indices(
            processes,
            parse_gpus(query(GPU_QUERY)),
            parse_minors(query(["nvidia-smi", "-q"])),
        )
    matched = {
        item["pid"]
        for item in processes
        if item["token"] in tokens
        or item["pid"] in pids
        or gpus & set(item.get("gpus", []))
        or any((item["token"], gpu) in scoped for gpu in launched_for(item))
    }
    targets = sorted(
        (matched | listeners(set(ports), proc)) - ancestors(os.getpid(), proc)
    )
    for pid in targets:
        try:
            send(pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
    deadline = time.monotonic() + grace
    while any(exists(pid) for pid in targets) and time.monotonic() < deadline:
        time.sleep(0.2)
    for pid in targets:
        if exists(pid):
            try:
                send(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
    return targets


class SubmissionTooLarge(Exception):
    """Signal that a workspace snapshot exceeds the configured size limit."""


class _Hashing:
    """Hash file contents while tarfile streams them into the archive."""

    def __init__(self, handle: Any) -> None:
        """Wrap an open binary file."""
        self.handle, self.hash = handle, hashlib.sha256()

    def read(self, size: int = -1) -> bytes:
        """Read and hash the next chunk."""
        data: bytes = self.handle.read(size)
        self.hash.update(data)
        return data


def pack(source: str, output: str, max_bytes: int, skip: Path | None = None) -> dict[str, Any]:
    """Archive a directory in sorted order and return a content digest independent of mtimes."""
    root = Path(source)
    Path(output).parent.mkdir(parents=True, exist_ok=True)
    digest, total, files = hashlib.sha256(), 0, 0
    with tarfile.open(output, "w:gz", compresslevel=1) as archive:
        for directory, names, filenames in os.walk(root):
            names[:] = sorted(n for n in names if Path(directory, n) != skip)
            for name in [*names, *sorted(filenames)]:
                path = Path(directory, name)
                relative = path.relative_to(root).as_posix()
                info = archive.gettarinfo(str(path), arcname=relative)
                # gettarinfo returns None for sockets; devices and FIFOs are skipped too
                if info is None or not (
                    info.isfile() or info.isdir() or info.issym() or info.islnk()
                ):
                    continue
                info.uid = info.gid = 0
                info.uname = info.gname = "root"
                total += info.size if info.isfile() else 0
                if total > max_bytes:
                    raise SubmissionTooLarge(
                        f"{source} holds more than {max_bytes} bytes"
                    )
                entry = f"{relative}\0{info.type!r}\0{info.mode:o}\0".encode()
                if info.isfile():
                    with path.open("rb") as handle:
                        reader = _Hashing(handle)
                        archive.addfile(info, reader)  # type: ignore[arg-type]
                    entry += reader.hash.hexdigest().encode()
                else:
                    archive.addfile(info)
                    entry += info.linkname.encode()
                digest.update(hashlib.sha256(entry).digest())
                files += 1
    return {
        "digest": digest.hexdigest(),
        "bytes": Path(output).stat().st_size,
        "files": files,
    }


def unpack(archive: str, destination: str) -> dict[str, Any]:
    """Replace a directory's contents with an archive, refusing members outside it."""
    if not hasattr(tarfile, "tar_filter"):
        raise RuntimeError("Python with tarfile extraction filters is required")
    target = Path(destination)
    target.mkdir(parents=True, exist_ok=True)
    for child in target.iterdir():
        if child.is_dir() and not child.is_symlink():
            shutil.rmtree(child)
        else:
            child.unlink()
    with tarfile.open(archive, "r:gz") as source:
        members = source.getmembers()
        source.extractall(target, members=members, filter="tar")
    return {"files": len(members)}



VENV, LOCK = ".venv", ".venv.lock"
TORCH_INDEXES = ["cu126", "cu128", "cu129", "cu130"]


def snapshot(source: str, output: str, max_bytes: int) -> dict[str, Any]:
    """Archive a workspace's code and its .venv package list, not the environment itself."""
    root = Path(source)
    if (root / VENV / "bin" / "python").exists():
        frozen = subprocess.run(
            ["uv", "pip", "freeze", "--python", str(root / VENV / "bin" / "python")],
            capture_output=True, text=True, check=True,
        )
        (root / LOCK).write_text(frozen.stdout)
    return pack(source, output, max_bytes, skip=root / VENV)


def restore(archive: str, destination: str) -> dict[str, Any]:
    """Unpack a workspace snapshot and rebuild its .venv from the package list."""
    result = unpack(archive, destination)
    root = Path(destination)
    if (root / LOCK).exists():
        subprocess.run(
            ["uv", "venv", "--seed", "--system-site-packages", "--python", "/usr/bin/python3", str(root / VENV)],
            check=True,
        )
        # a frozen CUDA build of torch (for example 2.8.0+cu128) exists only on the PyTorch index
        indexes = [f"--extra-index-url=https://download.pytorch.org/whl/{cuda}" for cuda in TORCH_INDEXES]
        subprocess.run(
            ["uv", "pip", "install", "--python", str(root / VENV / "bin" / "python"),
             *indexes, "--index-strategy", "unsafe-best-match", "-r", str(root / LOCK)],
            check=True,
        )
    return result

def evaluator_wrapper(env: dict[str, str], quick_quality: int) -> str:
    """Write the development evaluate.py, which targets the caller's port and has a smaller quick set."""
    return (
        "#!/opt/evaluator/bin/python\n"
        "import os, sys\n"
        "from pathlib import Path\n"
        "if '--quick' in sys.argv:\n"
        f"    os.environ['INFERENCE_BENCH_QUALITY_MMLUPRO_N'] = '{quick_quality}'\n"
        f"for key, value in {env!r}.items(): os.environ.setdefault(key, value)\n"
        "if os.environ.get('PORT') and not any(a.startswith('--server-url') for a in sys.argv[1:]):\n"
        "    sys.argv[1:1] = ['--server-url', 'http://127.0.0.1:' + os.environ['PORT']]\n"
        "sys.path.insert(0, '/opt/inferencebench/src/eval')\n"
        "from inference.runner import build_parser, run_evaluation\n"
        "run_evaluation(Path(__file__).parent, build_parser().parse_args())\n"
    )


def copy_task_inputs(runtime: Any, options: dict[str, Any]) -> None:
    """Copy the scenario's task files and cache the development LongBench samples."""
    from inference import cache_samples

    TASK.mkdir(parents=True, exist_ok=True)
    for name in ["scenario.json", "mission.txt", "benchmark.txt"]:
        shutil.copy(
            runtime.ROOT / "src/eval/tasks" / options["directory"] / name, TASK / name
        )
    seed = options["dev_seed"]
    longbench = (
        runtime.ROOT
        / f"src/eval/inference/baselines/samples/longbench_v2/{seed}_{LONGBENCH_SAMPLES}/samples.jsonl"
    )
    if not longbench.exists():
        cache_samples.cache_longbench_v2(longbench, seed, LONGBENCH_SAMPLES)


def development_env(runtime: Any, options: dict[str, Any]) -> dict[str, str]:
    """Return the evaluator environment for the development requests and quality references."""
    dev = options["dev_eval"]
    env: dict[str, str] = runtime.environment(options)
    env.update(
        {
            "INFERENCE_BENCH_QUALITY_SEED": str(dev["quality_seed"]),
            "INFERENCE_BENCH_QUALITY_MMLUPRO_N": str(dev["quality_samples"]),
            "INFERENCE_BENCH_QUALITY_MMLUPRO_SAMPLES_FILE": str(
                DEV / "quality-samples.jsonl"
            ),
            "INFERENCE_BENCH_QUALITY_BASELINE_REGISTRY": str(DEV / "quality.json"),
            "INFERENCE_BENCH_QUICK_REQUEST_LIMIT": str(dev["quick_requests"]),
            "INFERENCE_BENCH_REQUESTS_FILE": str(TASK / "requests.jsonl"),
            "INFERENCE_BENCH_SERVER_WAIT_S": str(dev["server_wait_seconds"]),
        }
    )
    return env


def measure_baselines(
    runtime: Any, options: dict[str, Any], requests: list[Any], spec: Any
) -> None:
    """Measure the development speed baseline and the development quality reference."""
    from inference import baseline_eval

    # runtime writes server logs and quality references below ARTIFACTS
    runtime.ARTIFACTS = DEV
    with runtime.baseline_server(options):
        baseline = baseline_eval._run_baseline(
            BASELINE_URL,
            requests,
            options["base_model"],
            DEV / "baseline-generations.jsonl",
            TASK,
            options["request_timeout_seconds"],
            concurrency_override=1,
        )
        if options["quality_reference_backend"] == "transformers":
            runtime.prepare_quality_baseline(options, spec)
    if options["quality_reference_backend"] == "vllm":
        with runtime.reference_server(options):
            runtime.prepare_quality_baseline(options, spec)
    (DEV / "baseline.json").write_text(json.dumps(baseline))


def register_quick_quality(spec: Any, samples: int) -> int:
    """Record the reference accuracy of the quick-mode quality subset and return its size."""
    from inference import precompute_quality_baseline

    rows = [
        json.loads(line)
        for line in (DEV / "quality-baseline" / "resolved_generations.jsonl")
        .read_text()
        .splitlines()
    ]
    # rows arrive in completion order; quick mode scores the first samples in file order
    rows.sort(key=lambda row: row.get("request_index", 0))
    quick = min(samples, len(rows))
    registry = json.loads((DEV / "quality.json").read_text())
    registry["datasets"]["mmlu_pro"].append(
        {
            "seed": spec.seed,
            "n": quick,
            "accuracy": precompute_quality_baseline._accuracy(rows[:quick]),
        }
    )
    (DEV / "quality.json").write_text(json.dumps(registry))
    return quick


def prepare_workspace(options: dict[str, Any]) -> dict[str, Any]:
    """Build the agents' development inputs, reference accuracy and speed baseline on GPU 0."""
    sys.path.insert(0, str(Path(__file__).parent))
    import runtime  # uploaded beside this file
    from huggingface_hub import snapshot_download
    from inference import cache_samples, precompute_baseline, quality_gate, runner

    dev = options["dev_eval"]
    snapshot_download(
        options["base_model"], ignore_patterns=["*.pt", "*.bin", "original/*"]
    )
    DEV.mkdir(parents=True, exist_ok=True)
    copy_task_inputs(runtime, options)
    env = development_env(runtime, options)
    os.environ.update(env)
    spec = quality_gate.get_quality_specs()[0][0]
    cache_samples.cache_mmlu_pro(spec.samples_file, spec.seed, spec.limit)
    config = runner.load_scenario_config(TASK)
    config["dataset_seed"] = options["dev_seed"]
    requests, repairs = runtime.prepare_requests(
        {**options, "request_limit": dev["requests"]}, config, "dev"
    )
    precompute_baseline._write_requests_jsonl(TASK / "requests.jsonl", requests)
    measure_baselines(runtime, options, requests, spec)
    quick = register_quick_quality(spec, dev["quick_quality_samples"])
    runtime.install_workspace(options)
    (TASK / "evaluate.py").write_text(evaluator_wrapper(env, quick))
    (TASK / "evaluate.py").chmod(0o755)
    for path in [DEV, *DEV.rglob("*")]:
        path.chmod(0o755 if path.is_dir() else 0o644)
    return {
        "requests": len(requests),
        "truncation_repairs": repairs,
        "quality_samples": spec.limit,
    }


def main(argv: list[str] | None = None) -> None:
    """Dispatch one pod operation and print its JSON result."""
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="operation", required=True)
    commands.add_parser("probe")
    stop = commands.add_parser("kill")
    stop.add_argument("--token", action="append", default=[])
    stop.add_argument("--gpu", action="append", type=int, default=[])
    stop.add_argument("--pid", action="append", type=int, default=[])
    stop.add_argument("--port", action="append", type=int, default=[])
    stop.add_argument("--scoped", action="append", default=[], help="TOKEN:GPU")
    archive = commands.add_parser("pack")
    archive.add_argument("source")
    archive.add_argument("output")
    archive.add_argument("--max-bytes", type=int, required=True)
    extract = commands.add_parser("unpack")
    extract.add_argument("archive")
    extract.add_argument("destination")
    code = commands.add_parser("snapshot")
    code.add_argument("source")
    code.add_argument("output")
    code.add_argument("--max-bytes", type=int, required=True)
    rebuild = commands.add_parser("restore")
    rebuild.add_argument("archive")
    rebuild.add_argument("destination")
    workspace = commands.add_parser("workspace")
    workspace.add_argument("options")
    args = parser.parse_args(argv)
    if args.operation == "probe":
        result: Any = probe()
    elif args.operation == "kill":
        pairs = [item.rpartition(":")[::2] for item in args.scoped]
        scoped = [(token, int(gpu)) for token, gpu in pairs]
        result = {"killed": kill(args.token, args.gpu, args.pid, args.port, scoped)}
    elif args.operation == "pack":
        try:
            result = pack(args.source, args.output, args.max_bytes)
        except SubmissionTooLarge as error:
            print(error, file=sys.stderr)
            sys.exit(TOO_LARGE)
    elif args.operation == "unpack":
        result = unpack(args.archive, args.destination)
    elif args.operation == "snapshot":
        try:
            result = snapshot(args.source, args.output, args.max_bytes)
        except SubmissionTooLarge as error:
            print(error, file=sys.stderr)
            sys.exit(TOO_LARGE)
    elif args.operation == "restore":
        result = restore(args.archive, args.destination)
    else:
        result = prepare_workspace(json.loads(Path(args.options).read_text()))
    print(json.dumps(result))


if __name__ == "__main__":
    main()
