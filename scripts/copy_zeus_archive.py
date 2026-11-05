"""One real-account COPY_ZEUS archive tick and authenticated archive sealing.

Encryption is deterministic AES-256-SIV. The 64-byte key is SHA-512 of the
domain separator b"ASTRA/COPY_ZEUS/ARCHIVE/AES-SIV/v1\\0" concatenated with
the UTF-8 BINGX_API_SECRET. The same existing BingX secret therefore lets the
owner reproduce the key locally; no new credential is created or stored.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import sys
import zipfile
from datetime import UTC, datetime
from pathlib import Path

from cryptography.hazmat.primitives.ciphers.aead import AESSIV

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from astra_bot.copy_zeus.archive import CopyZeusArchive  # noqa: E402
from astra_bot.copy_zeus.client import CopyZeusClient  # noqa: E402

DOMAIN = b"ASTRA/COPY_ZEUS/ARCHIVE/AES-SIV/v1\\0"
AAD = [b"astra-copyzeus-archive-v1"]
ROTATION_LIMIT = 10 * 1024 * 1024
BUNDLE_LIMIT = 64 * 1024 * 1024
MANIFEST_NAME = "MANIFEST.json"


def _secret() -> str:
    value = os.environ.get("BINGX_API_SECRET", "")
    if not value:
        raise RuntimeError("BINGX_API_SECRET is required for archive encryption")
    return value


def derive_key(secret: str) -> bytes:
    return hashlib.sha512(DOMAIN + secret.encode("utf-8")).digest()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _rotation_paths(state_dir: Path) -> list[Path]:
    def sort_key(path: Path):
        parts = path.stem.split("-")
        month = "-".join(parts[1:3])
        sequence = int(parts[3]) if len(parts) > 3 and parts[3].isdigit() else 1
        return month, sequence

    return sorted(state_dir.glob("copyzeus-*.jsonl"), key=sort_key)


def rotate_pending(state_dir: Path) -> None:
    pending = state_dir / "archive.jsonl"
    if not pending.exists() or not pending.stat().st_size:
        pending.unlink(missing_ok=True)
        return
    for raw_line in pending.read_text(encoding="utf-8").splitlines():
        if not raw_line.strip():
            continue
        row = json.loads(raw_line)
        fetched = str(row.get("fetched_at") or datetime.now(UTC).isoformat())
        month = fetched[:7] if len(fetched) >= 7 else datetime.now(UTC).strftime("%Y-%m")
        base = f"copyzeus-{month}"
        suffix = 1
        encoded = (raw_line + "\n").encode("utf-8")
        if len(encoded) > ROTATION_LIMIT:
            raise RuntimeError("A single archive row exceeds the 10 MiB rotation cap")
        while True:
            name = f"{base}.jsonl" if suffix == 1 else f"{base}-{suffix:03d}.jsonl"
            target = state_dir / name
            current_size = target.stat().st_size if target.exists() else 0
            if current_size + len(encoded) <= ROTATION_LIMIT:
                with target.open("ab") as stream:
                    stream.write(encoded)
                break
            suffix += 1
    pending.unlink()


def _manifest(state_dir: Path, previous: dict | None = None) -> dict:
    old = previous or {}
    rotations = []
    for path in _rotation_paths(state_dir):
        rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]
        times = sorted(str(row.get("fetched_at", "")) for row in rows)
        rotations.append({
            "file": path.name,
            "start_time": times[0] if times else None,
            "end_time": times[-1] if times else None,
            "rows": len(rows),
            "size_bytes": path.stat().st_size,
            "sha256": _sha256(path),
        })
    tick_path = state_dir / "last_tick.json"
    if tick_path.exists():
        last_tick = json.loads(tick_path.read_text(encoding="utf-8")).get("last_tick_at")
    else:
        last_tick = max(
            (item["end_time"] for item in rotations if item["end_time"]),
            default=old.get("last_tick_at"),
        )
    return {
        "format": "copyzeus-archive-v1",
        "environment": "real",
        "last_tick_at": last_tick,
        "rotations": rotations,
        "pruned": list(old.get("pruned", [])),
        "total_rows": sum(item["rows"] for item in rotations),
    }


def _write_inner_zip(state_dir: Path, destination: Path) -> None:
    with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for path in sorted(state_dir.iterdir()):
            if path.is_file() and (path.name.endswith(".jsonl") or path.name in {"seen.json", "coverage.json", "last_tick.json"}):
                info = zipfile.ZipInfo(path.name, date_time=(1980, 1, 1, 0, 0, 0))
                info.compress_type = zipfile.ZIP_DEFLATED
                info.external_attr = 0o600 << 16
                archive.writestr(
                    info,
                    path.read_bytes(),
                    compress_type=zipfile.ZIP_DEFLATED,
                    compresslevel=6,
                )


def seal_state(state_dir: Path, output_dir: Path, secret: str) -> dict:
    state_dir.mkdir(parents=True, exist_ok=True)
    output_dir.mkdir(parents=True, exist_ok=True)
    rotate_pending(state_dir)
    previous_path = state_dir / MANIFEST_NAME
    previous = json.loads(previous_path.read_text(encoding="utf-8")) if previous_path.exists() else {}
    for name, default in (
        ("seen.json", []),
        ("coverage.json", {"intervals": [], "gaps": []}),
        ("last_tick.json", {"last_tick_at": None}),
    ):
        path = state_dir / name
        if not path.exists():
            path.write_text(json.dumps(default, indent=2) + "\n", encoding="utf-8")

    while True:
        manifest = _manifest(state_dir, previous)
        manifest_bytes = (json.dumps(manifest, ensure_ascii=False, indent=2) + "\n").encode()
        inner_zip = output_dir / "archive-state.zip"
        _write_inner_zip(state_dir, inner_zip)
        encrypted = AESSIV(derive_key(secret)).encrypt(
            inner_zip.read_bytes(), [AAD[0], manifest_bytes]
        )
        if len(encrypted) + len(manifest_bytes) <= BUNDLE_LIMIT:
            break
        rotations = _rotation_paths(state_dir)
        if not rotations:
            raise RuntimeError("Non-rotatable archive state exceeds the 64 MiB bundle cap")
        oldest = rotations[0]
        removed = {
            "file": oldest.name,
            "sha256": _sha256(oldest),
            "size_bytes": oldest.stat().st_size,
            "pruned_at": datetime.now(UTC).isoformat(),
            "reason": "64 MiB bundle cap",
        }
        manifest["pruned"].append(removed)
        oldest.unlink()
        previous = manifest

    (output_dir / "archive.enc").write_bytes(encrypted)
    (output_dir / MANIFEST_NAME).write_bytes(manifest_bytes)
    inner_zip.unlink(missing_ok=True)
    return manifest


def restore_state(artifact_dir: Path | None, state_dir: Path, secret: str) -> None:
    state_dir.mkdir(parents=True, exist_ok=True)
    if artifact_dir is not None:
        encrypted_path = artifact_dir / "archive.enc"
        manifest_path = artifact_dir / MANIFEST_NAME
        if encrypted_path.exists():
            if not manifest_path.exists():
                raise RuntimeError("Encrypted archive is missing its authenticated MANIFEST.json")
            manifest_bytes = manifest_path.read_bytes()
            plaintext = AESSIV(derive_key(secret)).decrypt(
                encrypted_path.read_bytes(), [AAD[0], manifest_bytes]
            )
            temp_zip = state_dir / ".restore.zip"
            temp_zip.write_bytes(plaintext)
            with zipfile.ZipFile(temp_zip) as archive:
                archive.extractall(state_dir)
            temp_zip.unlink(missing_ok=True)
        if manifest_path.exists():
            (state_dir / MANIFEST_NAME).write_bytes(manifest_path.read_bytes())
    for name, default in (
        ("seen.json", []),
        ("coverage.json", {"intervals": [], "gaps": []}),
        ("last_tick.json", {"last_tick_at": None}),
    ):
        path = state_dir / name
        if not path.exists():
            path.write_text(json.dumps(default, indent=2) + "\n", encoding="utf-8")


async def collect_tick(state_dir: Path) -> dict[str, int]:
    if os.environ.get("COPYZEUS_ENV", "").strip().lower() != "real":
        raise RuntimeError("COPYZEUS_ENV must be exactly 'real'; VST archive runs are forbidden")
    archive = CopyZeusArchive(root=state_dir.parent.parent, env="real")
    async with CopyZeusClient(env="real", retries=4) as client:
        counts = await archive.run_once(client=client, limit=50, max_pages=10)
    (state_dir / "last_tick.json").write_text(
        json.dumps({"last_tick_at": datetime.now(UTC).isoformat()}) + "\n",
        encoding="utf-8",
    )
    rotate_pending(state_dir)
    return counts


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("restore", "collect", "seal"))
    parser.add_argument("--state-dir", type=Path, default=Path("models/copy_zeus/real"))
    parser.add_argument("--artifact-dir", type=Path)
    parser.add_argument("--output-dir", type=Path, default=Path("archive-output"))
    args = parser.parse_args()
    secret = _secret()
    if args.command == "restore":
        restore_state(args.artifact_dir, args.state_dir, secret)
        print("Previous encrypted archive restored (or empty state initialized).")
    elif args.command == "collect":
        counts = asyncio.run(collect_tick(args.state_dir))
        print("COPY_ZEUS read-only tick complete: " + json.dumps(counts, sort_keys=True))
    else:
        manifest = seal_state(args.state_dir, args.output_dir, secret)
        print(
            "Encrypted archive sealed: "
            f"{manifest['total_rows']} rows, {len(manifest['rotations'])} rotations."
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
