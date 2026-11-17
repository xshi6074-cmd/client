#!/usr/bin/env python3
"""Read Hypothesis annotations into a reproducible, local experiment snapshot."""

import argparse
from datetime import datetime, timezone
from getpass import GetPassWarning, getpass
import json
import os
from pathlib import Path
from shutil import rmtree
import sys
from tempfile import mkdtemp
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import HTTPRedirectHandler, Request, build_opener
import warnings


API = "https://hypothes.is/api/"


class CaptureError(Exception):
    pass


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def api_get(route, token, params=None):
    url = API + route + ("?" + urlencode(params) if params else "")
    headers = {"Accept": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    try:
        with build_opener(NoRedirect()).open(Request(url, headers=headers), timeout=25) as response:
            result = json.load(response)
    except HTTPError as exc:
        detail = "，请检查 API 凭据和访问权限" if exc.code in {401, 403} else ""
        raise CaptureError(f"Hypothesis /{route} 返回 HTTP {exc.code}{detail}") from exc
    except (URLError, TimeoutError, ValueError) as exc:
        raise CaptureError(f"读取 Hypothesis /{route} 失败：{exc}") from exc
    if not isinstance(result, dict):
        raise CaptureError(f"Hypothesis /{route} 未返回 JSON 对象")
    return result


def nonempty(value, field):
    if not isinstance(value, str) or not value:
        raise CaptureError(f"{field} 缺少有效值")
    return value


def resolve_scope(args):
    if args.public_user:
        token = None
        account = args.public_user
        if not account.startswith("acct:"):
            account = f"acct:{account}@hypothes.is"
    else:
        token = ""
        if args.prompt_token:
            try:
                with warnings.catch_warnings():
                    warnings.simplefilter("error", GetPassWarning)
                    token = getpass("Hypothesis API token: ").strip()
            except (GetPassWarning, EOFError) as exc:
                raise CaptureError("无法安全读取 token，请从交互式终端运行或配置环境变量") from exc
        else:
            token = os.environ.get("HYPOTHESIS_API_TOKEN", "").strip()
        if not token:
            raise CaptureError("请设置 HYPOTHESIS_API_TOKEN，或使用 --prompt-token")
        account = nonempty(api_get("profile", token).get("userid"), "已认证账户 userid")

    scope = {"user": account}
    if args.group:
        scope["group"] = args.group
    if args.uri:
        scope["uri"] = args.uri

    return token, scope


def capture(args):
    token, scope = resolve_scope(args)
    pages, selected = [], {}
    cursor = None
    while args.limit is None or len(selected) < args.limit:
        size = args.page_size
        if args.limit is not None:
            size = min(size, args.limit - len(selected))
        request = {**scope, "sort": "id", "order": "asc", "limit": size}
        if cursor is not None:
            request["search_after"] = cursor
        response = api_get("search", token, request)
        rows = response.get("rows")
        if not isinstance(rows, list) or len(rows) > size:
            raise CaptureError("搜索响应中的批注列表无效")
        pages.append({"request": request, "response": response})
        if not rows:
            break
        previous = cursor
        for row in rows:
            if not isinstance(row, dict):
                raise CaptureError("搜索返回了无效的批注")
            note_id = nonempty(row.get("id"), "批注 ID")
            if row.get("user") != scope["user"] or (args.group and row.get("group") != args.group):
                raise CaptureError(f"批注 {note_id} 超出请求范围")
            if cursor is not None and note_id < cursor:
                raise CaptureError("分页未按 ID 升序返回")
            cursor = note_id
            selected.setdefault(note_id, row)
        if cursor == previous:
            raise CaptureError("分页游标未推进，请重新拉取")

    metadata = {"source": "hypothesis_api", "access": "public" if not token else "authenticated",
                "scope": scope, "limit": args.limit, "first_search_total": pages[0]["response"].get("total")}
    return metadata, {"pages": pages}, list(selected.values())


def read_export(path):
    raw = json.loads(path.read_text(encoding="utf-8-sig"))
    rows = raw.get("annotations", raw.get("rows")) if isinstance(raw, dict) else raw
    if not isinstance(rows, list):
        raise CaptureError("需要 Hypothesis 侧栏导出的 JSON，或 API 返回的 rows 列表")
    return {"source": "hypothesis_json"}, raw, rows


def note_from_row(row):
    if not isinstance(row, dict):
        raise CaptureError("发现无效的批注记录")
    note_id = nonempty(row.get("id"), "批注 ID")
    comment = row.get("text")
    if comment is None:
        comment = ""
    if not isinstance(comment, str):
        raise CaptureError(f"批注 {note_id} 的评论格式无效")

    targets = row.get("target") or []
    quotes, target_urls, selector_types = [], [], []
    for target in targets:
        source = target.get("source")
        if isinstance(source, str) and source and source not in target_urls:
            target_urls.append(source)
        for selector in target.get("selector") or []:
            kind = selector.get("type")
            if kind and kind not in selector_types:
                selector_types.append(kind)
            exact = selector.get("exact")
            if kind == "TextQuoteSelector" and isinstance(exact, str) and exact:
                quotes.append(exact)

    links = row.get("links") or {}
    document = row.get("document") or {}
    titles = document.get("title") or []
    if isinstance(titles, str):
        titles = [titles]
    return {
        "id": note_id,
        "summary": "\n\n".join(quotes) if quotes else None,
        "quotes": quotes,
        "comment": comment,
        "source": {
            "uri": row.get("uri") or next(iter(target_urls), None),
            "title": next(iter(titles), None),
            "in_context": links.get("incontext"),
            "annotation_url": links.get("html"),
            "target_uris": target_urls,
            "selector_types": selector_types,
        },
        "created": row.get("created"),
        "updated": row.get("updated"),
        "user": row.get("user"),
        "group": row.get("group"),
        "references": row.get("references") or [],
        "hypothesis_tags": row.get("tags") or [],
    }


def save_snapshot(output, metadata, raw, rows):
    notes = {}
    for row in rows:
        note = note_from_row(row)
        current = notes.get(note["id"])
        if current is None or (note["updated"] or "") > (current["updated"] or ""):
            notes[note["id"]] = note
    output.parent.mkdir(parents=True, exist_ok=True)
    temp = Path(mkdtemp(prefix=".snapshot-", dir=output.parent))
    try:
        payload = {
            "format_version": 1,
            "captured_at": datetime.now(timezone.utc).isoformat(),
            **metadata,
            "count": len(notes),
            "notes": list(notes.values()),
        }
        for name, value in (("raw.json", raw), ("notes.json", payload)):
            (temp / name).write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        temp.rename(output)
    finally:
        if temp.exists():
            rmtree(temp)
    return len(notes)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    fetch = commands.add_parser("fetch", help="Fetch annotations using the read-only API")
    auth = fetch.add_mutually_exclusive_group()
    auth.add_argument("--prompt-token", action="store_true", help="Read a token without echo")
    auth.add_argument("--public-user", help="Read a specific person's public annotations without a token")
    fetch.add_argument("--group", help="Hypothesis group ID")
    fetch.add_argument("--uri", help="Limit to one source URL")
    fetch.add_argument("--limit", type=int, default=30, help="Maximum annotations to capture (default: 30)")
    fetch.add_argument("--page-size", type=int, default=20, help="API page size (default: 20)")
    import_json = commands.add_parser("import-json", help="Read a Hypothesis JSON export")
    import_json.add_argument("input", type=Path)
    for command in (fetch, import_json):
        command.add_argument("--output", required=True, type=Path, help="New snapshot directory")
    args = parser.parse_args(argv)
    if args.command == "fetch" and (args.limit < 1 or not 1 <= args.page_size <= 200):
        parser.error("--limit must be positive and --page-size must be 1 to 200")

    try:
        if args.output.exists() or args.output.is_symlink():
            raise CaptureError(f"快照路径已存在：{args.output}")
        data = capture(args) if args.command == "fetch" else read_export(args.input)
        count = save_snapshot(args.output, *data)
    except (CaptureError, OSError, ValueError, TypeError, AttributeError) as exc:
        print(f"快照未生成：{exc}", file=sys.stderr)
        return 1
    print(f"已保存 {count} 条批注：{args.output / 'notes.json'}")
    if not count:
        print("结果为空，请核对账户、组 ID 和网址范围。", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())

