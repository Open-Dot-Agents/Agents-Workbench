#!/usr/bin/env python3
"""Verify that each pinned stdio MCP package starts and remains available."""

from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parent
SERVERS = (ROOT / "node_modules" / ".bin" / "firecrawl-mcp",)


def probe(command: Path) -> None:
    environment = os.environ.copy()
    environment.pop("FIRECRAWL_API_KEY", None)
    environment.pop("FIRECRAWL_API_URL", None)
    process = subprocess.Popen(
        [str(command)],
        cwd=ROOT,
        env=environment,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        time.sleep(1)
        return_code = process.poll()
        if return_code is not None:
            _, stderr = process.communicate(timeout=1)
            raise RuntimeError(
                f"{command.name} exited during startup with {return_code}: {stderr.strip()}"
            )
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)


for server in SERVERS:
    probe(server)
    print(f"PASS {server.name} startup")
