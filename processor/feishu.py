# -*- coding: utf-8 -*-
"""飞书知识库同步：把「已深挖（有逐字稿）」的笔记单向推送到指定知识库节点。

链路（自建应用身份 tenant_access_token）：
  1. 上传 Markdown 素材（parent_type=ccm_import_open）拿 file_token；
  2. 创建导入任务把 md 导入为新版文档（docx），轮询 ticket 拿文档 token；
  3. move_docs_to_wiki 把云文档挂到知识库指定节点（异步任务再轮询）；
  4. 文档 token / 节点 token 回写 SQLite，后续增量跳过。

凭据、空间、权限未就绪时，入口安全跳过，不影响本地同步与深挖。
接口字段以飞书开放平台文档为准；解析处做了多路径兜底，联调时按真实报错微调。
"""
from __future__ import annotations

import argparse
import json
import re
import time
from pathlib import Path

import requests

from . import common, db
from . import run as runmod

BASE = "https://open.feishu.cn"

# 导入任务 / 挂载任务轮询参数
IMPORT_TIMEOUT_SEC = 120
IMPORT_POLL_SEC = 2
WIKI_TASK_TIMEOUT_SEC = 120
WIKI_TASK_POLL_SEC = 2

# 模块级 tenant_access_token 缓存（应用进程内复用，到期前 5 分钟换新）
_token_cache = {"token": "", "expire_at": 0.0}


class FeishuError(Exception):
    """飞书接口返回非 0 状态码或调用失败。"""

    def __init__(self, code, msg, action=""):
        self.code = code
        self.msg = msg
        self.action = action
        super().__init__(f"[{code}] {msg} ({action})")


# ---------- 底层 HTTP ----------


def _http(method, path, token=None, *, json_body=None, files=None, data=None, params=None):
    url = path if path.startswith("http") else BASE + path
    headers = {}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    try:
        resp = requests.request(
            method,
            url,
            headers=headers,
            json=json_body,
            files=files,
            data=data,
            params=params,
            timeout=30,
        )
        resp.raise_for_status()
        payload = resp.json()
    except requests.HTTPError as exc:
        body = ""
        try:
            body = resp.text[:500]
        except Exception:
            pass
        raise FeishuError("http", f"{exc} | {body}", url) from exc
    except requests.RequestException as exc:
        raise FeishuError("network", str(exc), url) from exc
    except ValueError as exc:
        raise FeishuError("parse", "响应不是合法 JSON", url) from exc
    if payload.get("code") not in (0, None):
        raise FeishuError(payload.get("code"), payload.get("msg", ""), url)
    return payload.get("data") or {}


def tenant_token(cfg):
    fs = cfg.get("feishu") or {}
    now = time.time()
    if _token_cache["token"] and _token_cache["expire_at"] - 300 > now:
        return _token_cache["token"]
    resp = requests.post(
        BASE + "/open-apis/auth/v3/tenant_access_token/internal",
        json={"app_id": fs.get("app_id", ""), "app_secret": fs.get("app_secret", "")},
        timeout=30,
    )
    try:
        payload = resp.json()
    except ValueError as exc:
        raise FeishuError("parse", "令牌响应不是合法 JSON", "获取应用令牌") from exc
    if payload.get("code") not in (0, None):
        raise FeishuError(payload.get("code"), payload.get("msg", ""), "获取应用令牌")
    # 飞书令牌接口把 tenant_access_token 放在响应顶层，不在 data 里
    token = payload.get("tenant_access_token")
    if not token:
        raise FeishuError("auth", "未返回 tenant_access_token", "获取应用令牌")
    _token_cache["token"] = token
    _token_cache["expire_at"] = now + int(payload.get("expire", 7200))
    return token


# ---------- 素材上传 / 导入 ----------


def upload_markdown(token, md_bytes, file_name):
    files = {"file": (file_name, md_bytes, "text/markdown")}
    form = {
        "file_name": file_name,
        "parent_type": "ccm_import_open",
        "size": str(len(md_bytes)),
        "extra": json.dumps({"obj_type": "docx", "file_extension": "md"}, ensure_ascii=False),
    }
    data = _http(
        "POST",
        "/open-apis/drive/v1/medias/upload_all",
        token,
        files=files,
        data=form,
    )
    file_token = data.get("file_token")
    if not file_token:
        raise FeishuError("upload", "上传素材未返回 file_token", file_name)
    return file_token


def create_import_task(token, file_token, file_name):
    body = {
        "file_name": file_name,
        "file_extension": "md",
        "file_token": file_token,
        "type": "docx",
        "point": {"mount_type": 1, "mount_key": ""},
    }
    data = _http("POST", "/open-apis/drive/v1/import_tasks", token, json_body=body)
    ticket = data.get("ticket")
    if not ticket:
        raise FeishuError("import", "创建导入任务未返回 ticket", file_name)
    return ticket


def poll_import_task(token, ticket):
    deadline = time.time() + IMPORT_TIMEOUT_SEC
    while True:
        data = _http("GET", f"/open-apis/drive/v1/import_tasks/{ticket}", token)
        result = data.get("result") or {}
        # 结果里文档 token 可能叫 token / doc_token，做多路径兜底
        doc_token = result.get("token") or result.get("doc_token") or result.get("file_token")
        if doc_token:
            return doc_token, result.get("url", "")
        err_msg = data.get("job_error_msg") or result.get("job_error_msg")
        job_status = data.get("job_status")
        if err_msg:
            raise FeishuError("import", err_msg, f"导入任务 {ticket}")
        if time.time() >= deadline:
            raise FeishuError("timeout", f"导入任务轮询超时（status={job_status}）", ticket)
        time.sleep(IMPORT_POLL_SEC)


# ---------- 挂载到知识库 ----------


def move_to_wiki(token, cfg, doc_token):
    fs = cfg.get("feishu") or {}
    body = {
        "obj_type": "docx",
        "obj_token": doc_token,
        "parent_wiki_token": fs.get("parent_wiki_token") or "",
        "apply": bool(fs.get("apply_when_no_perm", False)),
    }
    data = _http(
        "POST",
        f"/open-apis/wiki/v2/spaces/{fs['space_id']}/nodes/move_docs_to_wiki",
        token,
        json_body=body,
    )
    # 已完成：直接返回 wiki_token；未完成：返回 task_id 异步轮询；无权限申请中：applied=true
    if data.get("applied"):
        raise FeishuError("no_perm", "已发起权限申请，待知识库管理员同意后再挂载", doc_token)
    wiki_token = data.get("wiki_token")
    if wiki_token:
        return wiki_token, doc_token
    task_id = data.get("task_id")
    if not task_id:
        raise FeishuError("wiki", "挂载知识库未返回 wiki_token 或 task_id", doc_token)
    return poll_wiki_task(token, task_id)


def poll_wiki_task(token, task_id):
    deadline = time.time() + WIKI_TASK_TIMEOUT_SEC
    while True:
        data = _http(
            "GET",
            f"/open-apis/wiki/v2/tasks/{task_id}",
            token,
            params={"task_type": "move"},
        )
        task = data.get("task") or {}
        move_result = task.get("move_result") or []
        if move_result:
            node = move_result[0].get("node") or {}
            node_token = node.get("node_token")
            obj_token = node.get("obj_token") or task_id
            if node_token:
                return node_token, obj_token
        if time.time() >= deadline:
            raise FeishuError("timeout", f"挂载知识库任务轮询超时 {task_id}", task_id)
        time.sleep(WIKI_TASK_POLL_SEC)


# ---------- Markdown 清洗（Obsidian → 飞书可识别） ----------

_COVER_RE = re.compile(r"^\s*!\[\[[^\]]+\]\]\s*$")
_CALLOUT_RE = re.compile(r"^>\s*\[\!([^\]\-]+)\]\-?\s*(.*)$")
_WIKI_LINK_ALIAS_RE = re.compile(r"\[\[([^\]|]+)\|([^\]]+)\]\]")
_WIKI_LINK_RE = re.compile(r"\[\[([^\]]+)\]\]")
_DIVIDER_RE = re.compile(r"<!--\s*自动生成结束")

_CALLOUT_TITLE = {"note": "视频文案", "quote": "逐字稿"}


def _convert_line(line, include_covers: bool):
    if _DIVIDER_RE.search(line):
        return None  # 分界线本身不推到飞书
    if _COVER_RE.match(line):
        return None if not include_covers else line  # 首版不上传封面插图
    m = _CALLOUT_RE.match(line)
    if m:
        kind = m.group(1).strip().lower()
        tail = m.group(2).strip()
        heading = tail or _CALLOUT_TITLE.get(kind, kind)
        return f"## {heading}"
    line = _WIKI_LINK_ALIAS_RE.sub(r"\2", line)
    line = _WIKI_LINK_RE.sub(r"\1", line)
    return line


def build_feishu_markdown(item, note_text: str, include_covers: bool) -> str:
    fm, body = common.parse_frontmatter(note_text)
    title = (fm or {}).get("标题") or item.get("title") or "无标题"
    out = [f"# {title}", ""]
    in_manual = False
    for raw in body.splitlines():
        if _DIVIDER_RE.search(raw):
            in_manual = True
            continue
        if in_manual:
            out.append(raw)
            continue
        converted = _convert_line(raw, include_covers)
        if converted is not None:
            out.append(converted)
    # 去掉连续多余空行
    cleaned = "\n".join(out)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip() + "\n"
    return cleaned


# ---------- 选条目 / 推送 ----------


def collect_targets(con, only_id=None, force=False):
    targets = []
    for row in db.all_items(con):
        if only_id and row["aweme_id"] != only_id:
            continue
        if (row["deep_state"] or "") != "已转写":
            continue
        if not force and (row["feishu_node_token"] or row["feishu_doc_token"]):
            continue
        targets.append(row)
    return targets


def _note_text(row):
    if row["note_path"] and Path(row["note_path"]).exists():
        return Path(row["note_path"]).read_text(encoding="utf-8")
    return ""


def push_one(con, cfg, row, dry_run=False, force=False):
    item = runmod.row_to_item(row)
    note_text = _note_text(row)
    if not note_text.strip():
        return "skip", "本地笔记不存在或为空"
    fs = cfg.get("feishu") or {}
    md = build_feishu_markdown(item, note_text, bool(fs.get("include_covers", False)))
    file_name = f"{common.sanitize_filename(item.get('title') or item['aweme_id'], 80)}.md"

    if dry_run:
        return "dry", f"待推送：{file_name}（{len(md)} 字符）"

    token = tenant_token(cfg)
    file_token = upload_markdown(token, md.encode("utf-8"), file_name)
    ticket = create_import_task(token, file_token, file_name)
    doc_token, _url = poll_import_task(token, ticket)
    node_token, obj_token = move_to_wiki(token, cfg, doc_token)
    db.set_fields(
        con,
        row["aweme_id"],
        feishu_doc_token=obj_token or doc_token,
        feishu_node_token=node_token,
        feishu_synced_at=common.now_iso(),
    )
    return "pushed", node_token


def credentials_ready(cfg):
    fs = cfg.get("feishu") or {}
    return bool(fs.get("enabled")) and bool(fs.get("app_id")) and bool(fs.get("app_secret")) and bool(fs.get("space_id"))


# ---------- 速查草稿推送（tenant 应用身份） ----------

def _draft_dir(cfg):
    return Path(cfg["paths"]["db"]).parent / "feishu_drafts"


def draft_path_for(cfg, row):
    return _draft_dir(cfg) / f"draft_{common.id8(row['aweme_id'])}.md"


def _h1_of(md_text: str) -> str:
    for line in md_text.splitlines():
        line = line.strip()
        if line.startswith("# "):
            return line[2:].strip()
    return ""


def collect_quick_targets(con, cfg, only_id=None, force=False):
    targets = []
    for row in db.all_items(con):
        if only_id and row["aweme_id"] != only_id:
            continue
        if (row["quick_state"] or "") != "已提炼":
            continue
        if not force and (row["feishu_node_token"] or row["feishu_doc_token"]):
            continue
        if not draft_path_for(cfg, row).exists():
            continue
        targets.append(row)
    return targets


def update_doc_content(token, doc_token, md_text):
    """整篇覆盖已推送文档的正文（保留原 node 链接，不新建文档）。"""
    body = {
        "command": "overwrite",
        "format": "markdown",
        "content": md_text,
        "revision_id": -1,
        "scene": "DoubaoCLI",
    }
    return _http("PUT", f"/open-apis/docs_ai/v1/documents/{doc_token}", token, json_body=body)


def push_quick_one(con, cfg, row, dry_run=False, force=False):
    md_path = draft_path_for(cfg, row)
    md = md_path.read_text(encoding="utf-8")
    title = _h1_of(md) or (row["title"] or row["aweme_id"])
    file_name = f"{common.sanitize_filename(title, 80)}.md"

    if dry_run:
        return "dry", f"待推送：{file_name}（{len(md)} 字符）"

    token = tenant_token(cfg)
    # 已推过又要求强制推送：原地覆盖正文，保住 wiki 链接，不产生第二篇文档
    if force and (row["feishu_doc_token"] or "").strip():
        update_doc_content(token, row["feishu_doc_token"], md)
        db.set_fields(con, row["aweme_id"], feishu_synced_at=common.now_iso())
        return "updated", row["feishu_node_token"] or ""

    file_token = upload_markdown(token, md.encode("utf-8"), file_name)
    ticket = create_import_task(token, file_token, file_name)
    doc_token, _url = poll_import_task(token, ticket)
    node_token, obj_token = move_to_wiki(token, cfg, doc_token)
    db.set_fields(
        con,
        row["aweme_id"],
        feishu_doc_token=obj_token or doc_token,
        feishu_node_token=node_token,
        feishu_synced_at=common.now_iso(),
    )
    return "pushed", node_token


def main(argv=None):
    parser = argparse.ArgumentParser(description="把笔记/速查稿同步到飞书知识库")
    parser.add_argument("--id", help="只推送指定 aweme_id")
    parser.add_argument("--force", action="store_true", help="已推送过的也重新推送")
    parser.add_argument("--dry-run", action="store_true", help="只列出待推送条目，不发请求")
    parser.add_argument("--quick", action="store_true", help="推送速查草稿（data/feishu_drafts），默认通道")
    parser.add_argument("--raw", action="store_true", help="推送原始笔记（旧通道）")
    parser.add_argument("--settings")
    args = parser.parse_args(argv)

    cfg = common.load_config(args.settings)
    fs = cfg.get("feishu") or {}

    if not fs.get("enabled", False):
        print("飞书同步未启用（feishu.enabled=false），跳过。")
        return 0
    if not credentials_ready(cfg):
        missing = [k for k in ("app_id", "app_secret", "space_id") if not fs.get(k)]
        print(f"飞书同步配置不完整，跳过。缺少：{', '.join(missing)}")
        print("请在 config/settings.yml 的 feishu 段填写应用凭据与目标知识库 space_id。")
        return 0

    con = db.connect(cfg["paths"]["db"])
    runmod.adopt_note_edits(con, cfg)

    quick_mode = not args.raw
    if quick_mode:
        targets = collect_quick_targets(con, cfg, only_id=args.id, force=args.force)
        push_one_fn = push_quick_one
    else:
        targets = collect_targets(con, only_id=args.id, force=args.force)
        push_one_fn = push_one

    if not targets:
        print("没有待推送的条目（已推送过的默认跳过，可用 --force 重推）。")
        con.close()
        return 0

    print(f"待推送：{len(targets)} 条（{'速查草稿' if quick_mode else '原始笔记'}）")
    counts = {"pushed": 0, "dry": 0, "skip": 0, "failed": 0}
    for row in targets:
        label = (row["title"] or row["aweme_id"])[:40]
        try:
            status, detail = push_one_fn(con, cfg, row, dry_run=args.dry_run, force=args.force)
            counts[status] = counts.get(status, 0) + 1
            if status == "pushed":
                print(f"  ✓ 已推送：{label}（节点 {detail}）")
            elif status == "updated":
                print(f"  ↻ 已原地更新：{label}（节点 {detail}）")
            elif status == "dry":
                print(f"  · {detail}")
            else:
                print(f"  - 跳过：{label}（{detail}）")
        except FeishuError as exc:
            counts["failed"] += 1
            print(f"  ✗ 失败：{label} — {exc}")
        except Exception as exc:  # noqa: BLE001 单条失败不中断整批
            counts["failed"] += 1
            print(f"  ✗ 异常：{label} — {exc}")

    con.close()
    print("")
    print(f"【飞书同步】新增 {counts['pushed']} · 更新 {counts.get('updated', 0)} · 预演 {counts['dry']} · 跳过 {counts['skip']} · 失败 {counts['failed']}")
    return 1 if counts["failed"] and not args.dry_run else 0


if __name__ == "__main__":
    raise SystemExit(main())
