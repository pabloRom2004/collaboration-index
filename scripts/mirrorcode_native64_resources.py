"""Sample trusted per-container cgroup counters during authored remote target QA."""

import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path


def main():
    """Run the specified authored QA and retain numeric resource evidence even on failure."""
    assert os.environ.get("GITHUB_ACTIONS") == "true"
    output = Path(os.environ["MIRRORCODE_QA_RESOURCE_OUTPUT"])
    output.parent.mkdir(parents=True, exist_ok=True)
    pins = json.loads(Path(os.environ["MIRRORCODE_QA_IMAGE_PINS"]).read_text())
    configs = {
        json.loads(
            subprocess.run(
                ["docker", "image", "inspect", ref],
                capture_output=True,
                text=True,
                check=True,
                timeout=15,
            ).stdout
        )[0]["Id"]
        for ref in pins.values()
    }
    stop = threading.Event()
    snapshots = []
    host = []

    def sample():
        """Read only resource counters from disposable Inspect containers owned by this job."""
        while not stop.is_set():
            try:
                memory = {
                    line.split(":", 1)[0]: int(line.split()[1]) * 1024
                    for line in Path("/proc/meminfo").read_text().splitlines()
                    if line.startswith(("MemTotal:", "MemAvailable:", "SwapFree:"))
                }
                memory["at_monotonic"] = time.monotonic()
                host.append(memory)
                rows = subprocess.run(
                    ["docker", "ps", "--quiet"],
                    capture_output=True,
                    text=True,
                    check=True,
                    timeout=10,
                ).stdout.splitlines()
                for name in rows:
                    item = json.loads(
                        subprocess.run(
                            ["docker", "inspect", name],
                            capture_output=True,
                            text=True,
                            check=True,
                            timeout=10,
                        ).stdout
                    )[0]
                    if (
                        item["Config"]["Image"] not in pins.values()
                        and item["Image"] not in configs
                    ):
                        continue
                    result = subprocess.run(
                        [
                            "docker",
                            "exec",
                            name,
                            "sh",
                            "-c",
                            "cat /sys/fs/cgroup/memory.peak /sys/fs/cgroup/cpu.stat 2>/dev/null; du -sk /workdir 2>/dev/null || true",
                        ],
                        capture_output=True,
                        text=True,
                        timeout=10,
                    )
                    snapshots.append(
                        {
                            "at_monotonic": time.monotonic(),
                            "container": name,
                            "name": item["Name"],
                            "image_ref": item["Config"]["Image"],
                            "image_config_id": item["Image"],
                            "returncode": result.returncode,
                            "counters": result.stdout,
                        }
                    )
            except (subprocess.SubprocessError, OSError) as error:
                snapshots.append({"unavailable": type(error).__name__})
            output.write_text(json.dumps(snapshots, indent=2) + "\n")
            stop.wait(2)

    thread = threading.Thread(target=sample, daemon=True)
    thread.start()
    try:
        subprocess.run([sys.executable, *sys.argv[1:]], check=True, timeout=1800)
    finally:
        stop.set()
        thread.join(timeout=60)
        output.write_text(json.dumps(snapshots, indent=2) + "\n")
    output.with_suffix(".host.json").write_text(json.dumps(host, indent=2) + "\n")


if __name__ == "__main__":
    main()
