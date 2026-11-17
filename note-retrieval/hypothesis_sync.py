#!/usr/bin/env python3
"""Keep a read-only mirror of one Hypothesis scope and query its latest notes."""

import argparse
from datetime import datetime, timezone
import fcntl
import json
import os
from pathlib import Path
import sys
from tempfile import NamedTemporaryFile

from hypothesis_snapshot import CaptureError, capture, note_from_row


def read_mirror(path):
    with path.open(encoding="utf-8") as stream:
        data = json.load(stream)
    if data.get("format_version") != 1 or data.get("source") != "hypothesis_mirror":
        raise CaptureError("指定路径不是由 sync 创建的批注镜像")
    return data


def replace_mirror(path, data):
    temporary = None
    try:
        with NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                prefix=".hypothesis-", delete=False) as stream:
            temporary = Path(stream.name)
            json.dump(data, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(path)
        directory_fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def timestamp(note):
    value = note.get("updated") or note.get("created")
    if not isinstance(value, str):
        raise CaptureError(f"批注 {note['id']} 缺少有效时间")
    if value.endswith("Z"):
        value = value[:-1] + "+00:00"
    result = datetime.fromisoformat(value)
    if result.tzinfo is None:
        raise CaptureError(f"批注 {note['id']} 的时间缺少时区")
    return result


def sync(args):
    path = args.store
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    lock_path = path.with_name(path.name + ".lock")
    with lock_path.open("a", encoding="utf-8") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        prior = read_mirror(path) if path.exists() else None
        metadata, raw, rows = capture(args)
        scope = {"access": metadata["access"], **metadata["scope"]}
        if prior is not None and prior["scope"] != scope:
            raise CaptureError("镜像的账户、访问方式或筛选范围不同，请另用一个路径")
        # Search-after is ordered by ID, while total describes the entire result set.
        # A changing total or a gap means we cannot safely infer removals.
        totals = [page["response"].get("total") for page in raw["pages"]]
        if any(type(total) is not int or total != len(rows) for total in totals):
            raise CaptureError("分页总数与实际取得的批注不一致，本轮保留上次成功的镜像")
        notes = sorted(
            map(note_from_row, rows),
            key=lambda note: (timestamp(note), note["id"]),
            reverse=True,
        )
        previous = {note["id"]: note for note in prior["notes"]} if prior else {}
        current = {note["id"]: note for note in notes}
        changes = {
            "added": len(current.keys() - previous.keys()),
            "updated": sum(current[note_id] != previous[note_id]
                           for note_id in current.keys() & previous.keys()),
            "removed_from_scope": len(previous.keys() - current.keys()),
        }
        payload = {
            "format_version": 1,
            "source": "hypothesis_mirror",
            "scope": scope,
            "synced_at": datetime.now(timezone.utc).isoformat(),
            "count": len(notes),
            "notes": notes,
        }
        replace_mirror(path, payload)
    return {"synced_at": payload["synced_at"], "count": len(notes), **changes}


def latest(args):
    full = read_mirror(args.store)
    notes = full["notes"]
    if args.id:
        notes = [note for note in notes if note["id"] == args.id]
    notes = notes[:args.limit]
    return {**full, "notes": notes, "count": len(notes), "total": full["count"]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    sync_cmd = commands.add_parser("sync", help="Refresh a mirror from Hypothesis")
    auth = sync_cmd.add_mutually_exclusive_group()
    auth.add_argument("--prompt-token", action="store_true")
    auth.add_argument("--public-user")
    sync_cmd.add_argument("--group")
    sync_cmd.add_argument("--uri")
    sync_cmd.add_argument("--page-size", type=int, default=200)
    sync_cmd.set_defaults(limit=None, run=sync)
    latest_cmd = commands.add_parser("latest", help="Read notes ordered by last update")
    latest_cmd.add_argument("--limit", type=int, default=20)
    latest_cmd.add_argument("--id")
    latest_cmd.set_defaults(run=latest)
    for command in (sync_cmd, latest_cmd):
        command.add_argument("--store", type=Path, required=True, help="Persistent notes.json path")
    args = parser.parse_args()
    if args.command == "sync" and not 1 <= args.page_size <= 200:
        parser.error("--page-size must be 1 to 200")
    if args.command == "latest" and args.limit < 1:
        parser.error("--limit must be positive")
    try:
        print(json.dumps(args.run(args), ensure_ascii=False))
    except (CaptureError, OSError, ValueError, TypeError, AttributeError, KeyError) as exc:
        print(f"Hypothesis {args.command} 失败：{exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

