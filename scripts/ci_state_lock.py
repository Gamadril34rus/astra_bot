#!/usr/bin/env python3
"""Atomic Git-ref lock for ASTRA state-writing GitHub Actions jobs."""
from __future__ import annotations
import argparse, os, subprocess, sys, time
REMOTE_REF = os.environ.get("ASTRA_CI_LOCK_REF", "refs/heads/astra-ci-state-lock")
LOCAL_REF = "refs/remotes/astra-ci-state-lock"

def git(*args: str, check: bool = True, env: dict[str, str] | None = None):
    merged = os.environ.copy()
    if env: merged.update(env)
    return subprocess.run(["git", *args], text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=check, env=merged)

def remote_sha(remote: str) -> str | None:
    out = git("ls-remote", "--refs", remote, REMOTE_REF)
    return out.stdout.split()[0] if out.stdout.strip() else None

def lock_message(remote: str, sha: str) -> str | None:
    fetched = git("fetch", remote, f"+{REMOTE_REF}:{LOCAL_REF}", check=False)
    if fetched.returncode: return None
    return git("show", "-s", "--format=%B", sha).stdout.strip()

def fields(message: str) -> dict[str, str]:
    result = {}
    for token in message.split():
        if "=" in token:
            key, value = token.split("=", 1); result[key] = value
    return result

def delete_if_unchanged(remote: str, sha: str) -> bool:
    out = git("push", f"--force-with-lease={REMOTE_REF}:{sha}", remote, f":{REMOTE_REF}", check=False)
    return out.returncode == 0

def acquire(args) -> int:
    run_id = os.environ.get("GITHUB_RUN_ID", "").strip()
    attempt = os.environ.get("GITHUB_RUN_ATTEMPT", "1").strip()
    if not run_id:
        print("GITHUB_RUN_ID is required; refusing an ownerless lock", file=sys.stderr); return 2
    started = time.monotonic()
    while True:
        current = remote_sha(args.remote)
        if current is None:
            acquired_at = int(time.time())
            message = f"ASTRA CI state lock run_id={run_id} attempt={attempt} acquired_at={acquired_at}"
            lock_sha = git("commit-tree", "HEAD^{tree}", "-p", "HEAD", "-m", message, env={
                "GIT_AUTHOR_NAME":"astra-ci-lock", "GIT_AUTHOR_EMAIL":"bot@users.noreply.github.com",
                "GIT_COMMITTER_NAME":"astra-ci-lock", "GIT_COMMITTER_EMAIL":"bot@users.noreply.github.com"}).stdout.strip()
            pushed = git("push", args.remote, f"{lock_sha}:{REMOTE_REF}", check=False)
            if pushed.returncode == 0:
                print(f"Acquired ASTRA state lock (run={run_id}, attempt={attempt})"); return 0
        else:
            message = lock_message(args.remote, current) or ""
            data = fields(message)
            try: age = int(time.time()) - int(data.get("acquired_at", "0"))
            except ValueError: age = args.stale_after_seconds + 1
            if age > args.stale_after_seconds:
                if delete_if_unchanged(args.remote, current):
                    print(f"Reclaimed stale ASTRA state lock (age={age}s)", file=sys.stderr); continue
            elif data.get("run_id") == run_id and data.get("attempt") == attempt:
                print(f"ASTRA state lock already owned by this run ({run_id}/{attempt})"); return 0
        elapsed = time.monotonic() - started
        if elapsed >= args.wait_seconds:
            print(f"Timed out waiting for ASTRA state lock after {int(elapsed)}s", file=sys.stderr); return 1
        time.sleep(min(args.poll_seconds, max(0.1, args.wait_seconds - elapsed)))

def release(args) -> int:
    run_id = os.environ.get("GITHUB_RUN_ID", "").strip()
    attempt = os.environ.get("GITHUB_RUN_ATTEMPT", "1").strip()
    current = remote_sha(args.remote)
    if current is None: print("ASTRA state lock is already free"); return 0
    data = fields(lock_message(args.remote, current) or "")
    if data.get("run_id") != run_id or data.get("attempt") != attempt:
        print("ASTRA state lock belongs to another run; leaving it untouched"); return 0
    if delete_if_unchanged(args.remote, current):
        print(f"Released ASTRA state lock (run={run_id}, attempt={attempt})"); return 0
    print("Could not release ASTRA state lock; lease changed before delete", file=sys.stderr); return 1

def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("acquire", "release"))
    parser.add_argument("--remote", default="origin")
    parser.add_argument("--wait-seconds", type=int, default=5400)
    parser.add_argument("--stale-after-seconds", type=int, default=2700)
    parser.add_argument("--poll-seconds", type=float, default=5)
    args = parser.parse_args()
    return acquire(args) if args.action == "acquire" else release(args)

if __name__ == "__main__": raise SystemExit(main())
