from __future__ import annotations

import csv
import subprocess
from io import StringIO


def discover_gpus() -> list[dict]:
    try:
        completed = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=index,uuid,name,memory.total,driver_version",
                "--format=csv,noheader",
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (FileNotFoundError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return []

    rows = csv.reader(StringIO(completed.stdout))
    gpus = []
    for row in rows:
        if len(row) < 5:
            continue
        gpus.append(
            {
                "index": row[0].strip(),
                "uuid": row[1].strip(),
                "name": row[2].strip(),
                "memory": row[3].strip(),
                "driver": row[4].strip(),
            }
        )
    return gpus

