"""Owner-side downloader, decryptor and human-readable COPY_ZEUS archive summary.

Key derivation (exact): SHA-512(
    b"ASTRA/COPY_ZEUS/ARCHIVE/AES-SIV/v1\\\\0" + BINGX_API_SECRET.encode("utf-8")
).digest() produces the 64-byte AES-256-SIV key. AES-SIV is deterministic and
authenticated; the exact plaintext MANIFEST.json bytes are supplied as
associated data. The secret is requested only through getpass and never echoed.
"""
from __future__ import annotations

import argparse
import getpass
import hashlib
import json
import subprocess
import tempfile
import zipfile
from decimal import Decimal, InvalidOperation
from pathlib import Path

from cryptography.hazmat.primitives.ciphers.aead import AESSIV

DOMAIN = b"ASTRA/COPY_ZEUS/ARCHIVE/AES-SIV/v1\\0"
AAD = [b"astra-copyzeus-archive-v1"]
REPOSITORY = "Gamadril34rus/astra_bot"
ARTIFACT_NAME = "copyzeus-archive"


def derive_key(secret: str) -> bytes:
    return hashlib.sha512(DOMAIN + secret.encode("utf-8")).digest()


def _decimal(value) -> Decimal:
    try:
        return Decimal(str(value or "0"))
    except (InvalidOperation, ValueError):
        return Decimal(0)


def _run_gh(args: list[str], *, output: Path | None = None) -> str:
    command = ["gh", "api", *args]
    if output is not None:
        output.parent.mkdir(parents=True, exist_ok=True)
        with output.open("wb") as stream:
            subprocess.run(command, check=True, stdout=stream, stderr=subprocess.PIPE)
        return ""
    result = subprocess.run(command, check=True, capture_output=True, text=True)
    return result.stdout


def _latest_artifact_id() -> int:
    raw = _run_gh([
        f"repos/{REPOSITORY}/actions/artifacts?name={ARTIFACT_NAME}&per_page=100"
    ])
    artifacts = [
        item for item in json.loads(raw).get("artifacts", [])
        if not item.get("expired", False)
    ]
    if not artifacts:
        raise RuntimeError("No unexpired copyzeus-archive artifact was found")
    latest = max(artifacts, key=lambda item: item.get("created_at", ""))
    return int(latest["id"])


def _verify_manifest(state_dir: Path, manifest: dict) -> None:
    if manifest.get("environment") != "real":
        raise RuntimeError("Archive manifest is not marked as real environment")
    for item in manifest.get("rotations", []):
        path = state_dir / item["file"]
        if not path.is_file():
            raise RuntimeError(f"Archive rotation is missing: {path.name}")
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if digest != item.get("sha256"):
            raise RuntimeError(f"SHA-256 mismatch for archive rotation: {path.name}")


def _time(event: dict) -> str:
    for key in ("time", "timestamp", "createTime", "updateTime"):
        value = event.get(key)
        if value is not None:
            return str(value)
    return ""


def _is_true(value) -> bool:
    return value is True or str(value).strip().lower() in {"1", "true", "yes"}


def print_summary(state_dir: Path, manifest: dict) -> None:
    orders = []
    income = []
    for path in sorted(state_dir.glob("copyzeus-*.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line:
                continue
            row = json.loads(line)
            if row.get("source") == "allOrders":
                orders.append(row)
            elif row.get("source") == "income":
                income.append(row)

    print("Environment: real")
    print(f"Last archive tick: {manifest.get('last_tick_at', 'unknown')}")
    print(f"Rows: {manifest.get('total_rows', 0)}; rotations: {len(manifest.get('rotations', []))}")
    print("\nORDERS (entry/exit classification is based on reduceOnly/closePosition flags)")
    for row in sorted(orders, key=lambda item: _time(item.get("event", {}))):
        event = row.get("event", {})
        is_exit = _is_true(event.get("reduceOnly")) or _is_true(event.get("closePosition"))
        direction = "EXIT" if is_exit else "ENTRY/ORDER"
        print(
            f"{row.get('fetched_at', '')} | {direction} | {event.get('symbol', '')} "
            f"| {event.get('side', '')}/{event.get('positionSide', '')} "
            f"| qty={event.get('executedQty', event.get('origQty', ''))} "
            f"| price={event.get('avgPrice') or event.get('price', '')} "
            f"| leverage={event.get('leverage', '')} "
            f"| PnL={event.get('profit', '')} | commission={event.get('commission', '')} "
            f"| status={event.get('status', '')}"
        )

    totals = {"REALIZED_PNL": Decimal(0), "TRADING_FEE": Decimal(0), "FUNDING_FEE": Decimal(0)}
    print("\nINCOME (PnL / fees / funding)")
    for row in sorted(income, key=lambda item: _time(item.get("event", {}))):
        event = row.get("event", {})
        kind = str(row.get("event_kind") or event.get("incomeType", "UNKNOWN")).upper()
        amount = _decimal(event.get("income"))
        if kind in totals:
            totals[kind] += amount
        print(
            f"{row.get('fetched_at', '')} | {kind} | {event.get('symbol', '')} "
            f"| amount={event.get('income', '')} {event.get('asset', '')} "
            f"| tranId={event.get('tranId', '')} tradeId={event.get('tradeId', '')}"
        )
    fees_and_funding = totals["TRADING_FEE"] + totals["FUNDING_FEE"]
    print("\nTOTALS")
    print(f"Realized PnL: {totals['REALIZED_PNL']}")
    print(f"Trading fees: {totals['TRADING_FEE']}")
    print(f"Funding: {totals['FUNDING_FEE']}")
    print(f"Fees + funding (signed): {fees_and_funding}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--artifact-id",
        type=int,
        help="Use this artifact ID instead of the latest",
    )
    args = parser.parse_args()
    secret = getpass.getpass("BINGX_API_SECRET (hidden input): ")
    if not secret:
        raise SystemExit("Empty API secret; nothing downloaded or decrypted")
    artifact_id = args.artifact_id or _latest_artifact_id()

    with tempfile.TemporaryDirectory(prefix="copyzeus-extract-") as temp:
        root = Path(temp)
        artifact_zip = root / "artifact.zip"
        _run_gh([
            f"repos/{REPOSITORY}/actions/artifacts/{artifact_id}/zip"
        ], output=artifact_zip)
        artifact_dir = root / "artifact"
        artifact_dir.mkdir()
        with zipfile.ZipFile(artifact_zip) as archive:
            archive.extractall(artifact_dir)

        manifest_path = artifact_dir / "MANIFEST.json"
        encrypted_path = artifact_dir / "archive.enc"
        manifest_bytes = manifest_path.read_bytes()
        manifest = json.loads(manifest_bytes)
        plaintext = AESSIV(derive_key(secret)).decrypt(
            encrypted_path.read_bytes(), [AAD[0], manifest_bytes]
        )
        state_dir = root / "state"
        state_dir.mkdir()
        inner_zip = root / "state.zip"
        inner_zip.write_bytes(plaintext)
        with zipfile.ZipFile(inner_zip) as archive:
            archive.extractall(state_dir)
        _verify_manifest(state_dir, manifest)
        print(f"Verified artifact ID: {artifact_id}")
        print_summary(state_dir, manifest)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
