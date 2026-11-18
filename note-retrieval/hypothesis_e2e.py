"""Run a bounded live smoke test without logging personal note contents."""

import argparse
import json
import os
import re
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.parse import urlsplit

from hypothesis_snapshot import CaptureError
from hypothesis_sync import latest, sync
from hypothesis_tags import TagError, build, inspect, load_notes, note_text


def verify(run_dir, output_token_field):
    job = argparse.Namespace(
        store=run_dir / "mirror.json", index=run_dir / "tags.json",
        prompt_token=False, public_user=None, group=None,
        uri=os.environ.get("HYPOTHESIS_E2E_URI") or None,
        limit=None, page_size=200, max_calls=3, max_input_chars=2000,
        max_output_tokens=1000, vocabulary_limit=30,
        output_token_field=output_token_field,
    )
    mirrored = sync(job)
    print(json.dumps({"hypothesis_synced": mirrored["count"]}), flush=True)
    job.id = None
    job.limit = 10
    notes_and_scope = latest(job)
    examples = []
    for note in notes_and_scope["notes"]:
        fields = note_text(note).values()
        if any(value.strip() for value in fields) and sum(map(len, fields)) <= job.max_input_chars:
            examples.append(note)
        if len(examples) == job.max_calls:
            break
    if not examples:
        raise TagError("最近十条批注里没有符合测试长度的正文")
    job.notes = run_dir / "examples.json"
    job.notes.write_text(json.dumps({"scope": notes_and_scope["scope"], "notes": examples}), encoding="utf-8")
    notes, scope = load_notes(job.notes)
    report = build(job, notes, scope)
    print(json.dumps({
        "calls": report["api_calls"],
        "tagged_notes": report["tagged"],
        "failed_notes": report["failed"],
        "usage": {k: v for k, v in report["usage"].items()
                  if k in {"prompt_tokens", "completion_tokens", "total_tokens"}},
    }), flush=True)
    if report["failed"] or report["tagged"] != len(examples) or report["api_calls"] != len(examples):
        errors = [item["error"] for item in report["failures"]]
        status = next((error for error in errors if re.fullmatch(r"模型 HTTP [0-9]{3}", error)), None)
        raise TagError(status or "模型没有成功为所有样本返回可校验的结果")

    job.command, job.limit, job.offset = "vocab", 20, 0
    vocabulary = inspect(job, notes, scope)["tags"]
    if not vocabulary:
        raise TagError("模型已响应，但没有产生可回读的标签")
    job.command, job.id = "read-tag", vocabulary[0]["id"]
    hits = inspect(job, notes, scope)["notes"]
    if not hits or any(hit != notes.get(hit["id"]) for hit in hits):
        raise TagError("按标签回读与 Hypothesis 原文不一致")
    if build(job, notes, scope)["api_calls"] != 0:
        raise TagError("第二轮对未改动的批注再次调用了模型")
    print(json.dumps({"distinct_tags": len(vocabulary), "readback_hits": len(hits), "rerun_api_calls": 0}))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-token-field", choices=["max_tokens", "max_completion_tokens"],
                        default="max_tokens")
    args = parser.parse_args()
    required = ("HYPOTHESIS_API_TOKEN", "NOTE_TAG_API_KEY", "NOTE_TAG_API_URL", "NOTE_TAG_MODEL")
    missing = [name for name in required if not os.environ.get(name, "").strip()]
    if missing:
        print("Missing configuration: " + ", ".join(missing), file=sys.stderr)
        return 2
    if urlsplit(os.environ["NOTE_TAG_API_URL"]).scheme != "https":
        print("Live verification requires an HTTPS model API endpoint.", file=sys.stderr)
        return 2
    try:
        with TemporaryDirectory(prefix="hypothesis-e2e-") as directory:
            verify(Path(directory), args.output_token_field)
    except CaptureError as exc:
        status = re.search(r"返回 HTTP [0-9]{3}", str(exc))
        print(f"Hypothesis 同步失败{f'（{status.group()}）' if status else ''}", file=sys.stderr)
        return 1
    except TagError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
