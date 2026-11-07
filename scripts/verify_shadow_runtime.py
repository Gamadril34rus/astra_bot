#!/usr/bin/env python3
"""Runtime attestation for the offline shadow Docker profile; run inside the container."""
from __future__ import annotations

import json
import os
import time
from pathlib import Path


def mount_record(target: str) -> tuple[str, set[str], str]:
    matches = []
    for line in Path("/proc/self/mountinfo").read_text(encoding="utf-8").splitlines():
        fields = line.split()
        if len(fields) < 10 or "-" not in fields:
            continue
        separator = fields.index("-")
        mountpoint = fields[4].replace("\\040", " ")
        if target == mountpoint or target.startswith(mountpoint.rstrip("/") + "/"):
            fs_type = fields[separator + 1]
            options = set(fields[5].split(","))
            matches.append((len(mountpoint), mountpoint, options, fs_type))
    if not matches:
        raise RuntimeError(f"mount not found for {target}")
    _, point, options, fs_type = max(matches, key=lambda item: item[0])
    return point, options, fs_type


def require(condition: bool, message: str):
    if not condition:
        raise RuntimeError(message)


def main():
    status = {}
    for line in Path("/proc/self/status").read_text(encoding="utf-8").splitlines():
        if ":" in line:
            key, value = line.split(":", 1)
            status[key] = value.strip()

    uid = os.getuid()
    require(uid != 0, "process is root")
    require(status.get("CapEff") == "0000000000000000", "effective capabilities are not zero")
    require(status.get("NoNewPrivs") == "1", "NoNewPrivs is not enabled")

    root_mount, root_options, _ = mount_record("/")
    workspace_mount, workspace_options, _ = mount_record("/workspace")
    data_mount, data_options, _ = mount_record("/data")
    tmp_mount, _, tmp_fs = mount_record("/tmp")
    require("ro" in root_options, "root filesystem is not read-only")
    require(workspace_mount == "/workspace" and "ro" in workspace_options,
            "repository mount is not a dedicated read-only mount")
    require(data_mount == "/data" and "rw" in data_options,
            "external data directory is not the dedicated writable mount")
    require(tmp_mount == "/tmp" and tmp_fs == "tmpfs", "/tmp is not tmpfs")

    interfaces = sorted(path.name for path in Path("/sys/class/net").iterdir())
    require(interfaces == ["lo"], f"non-loopback network interface present: {interfaces}")
    routes = Path("/proc/net/route").read_text(encoding="ascii").splitlines()[1:]
    default_routes = [
        line for line in routes
        if len(line.split()) > 1 and line.split()[1] == "00000000"
    ]
    require(not default_routes, "default network route is present")

    probe = Path("/workspace") / f".shadow-ro-probe-{os.getpid()}-{time.time_ns()}"
    try:
        with probe.open("x", encoding="utf-8") as handle:
            handle.write("must not happen")
    except OSError:
        repo_write_blocked = True
    else:
        repo_write_blocked = False
        probe.unlink(missing_ok=True)
    require(repo_write_blocked, "repository write unexpectedly succeeded")

    marker = Path("/data") / f".shadow-rw-probe-{os.getpid()}-{time.time_ns()}"
    marker.write_text("sandbox write test\n", encoding="utf-8")
    marker.unlink()
    print(json.dumps({
        "uid": uid,
        "cap_eff": status.get("CapEff"),
        "no_new_privs": status.get("NoNewPrivs"),
        "root_mount": root_mount,
        "root_read_only": "ro" in root_options,
        "workspace_read_only": "ro" in workspace_options,
        "data_writable": "rw" in data_options,
        "tmpfs": tmp_fs,
        "interfaces": interfaces,
        "default_route_present": bool(default_routes),
        "repository_write_blocked": repo_write_blocked,
        "data_write_succeeded_and_removed": True,
        "network_mode_attestation": "no non-loopback interface and no default route",
    }, sort_keys=True))


if __name__ == "__main__":
    main()
