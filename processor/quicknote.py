# -*- coding: utf-8 -*-
"""速查稿自动提炼：已转写条目 -> 大模型按锁定规范生成速查稿 + 飞书草稿。

链路位置：深挖（②）之后、飞书推送（④）之前。
- 选条目：deep_state=已转写 且尚无速查稿；
- 调 OpenAI 兼容接口，按规范输出结构化 JSON；
- 校验通过后落本地速查稿（vault/已深挖）与推送草稿（data/feishu_drafts）；
- 判定无可提炼内容则记入跳过台账；失败状态下次重试，不覆盖已有速查稿。

凭据未配置（enabled=false 或缺 base_url/api_key/model）时安全跳过，不影响本地链路。
用法：
  python -m processor.quicknote                # 处理所有待提炼条目
  python -m processor.quicknote --id <aweme_id>
  python -m processor.quicknote --dry-run       # 只列待处理，不调模型、不写稿
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

import requests

from . import common, db, notes, run as runmod

TRANSCRIBED_DIR = "已深挖"
DRAFT_DIR_NAME = "feishu_drafts"
_TS_RE = re.compile(r"^\s*\[\d{2}:\d{2}(?::\d{2})?\]\s*", re.M)

SYSTEM_PROMPT = """你是知识速查稿整理助手。把视频逐字稿提炼成可复用的中文速查稿，严格按输出 JSON 格式，不要输出 JSON 以外的解释。

## 内容取舍
- 只保留可复用的方法、框架、步骤、模型、工具、概念与结论；删除案例复述、铺垫、闲聊、口语连接词、感叹与营销腔。
- 原文数字、模型名、框架缩写、专有名词保留原样；语音识别错词按通行写法改正，无法确定的保留原词并加（？），不要臆造。
- 输出短句，不写导语和总结陈词；篇幅按内容实际需要决定，写清楚即可，不要为凑长度灌水，也不要为压缩丢掉要点。

## 跳过判定
若内容只是观点表态、情绪抒发、个人回忆、个案连麦，或过短且没有可提炼的方法论 / 知识框架，返回 skip=true，并在 skip_reason 写明：观点情绪 / 个人回忆 / 个案连麦 / 过短。

## 输出 JSON 结构（字段名固定）
{
  "skip": false,
  "skip_reason": "",
  "slug": "40 字以内主题词，用于文件名",
  "h1": "速查稿标题",
  "core": "一句话核心",
  "sections": [
    {
      "h": "小节标题",
      "table": {"headers": ["列1", "列2"], "rows": [["单元格", "单元格"]]},
      "bullets": ["条目", "条目"]
    }
  ],
  "steps": ["上手顺序第 1 条", "第 2 条", "第 3 条"],
  "corrections": "纠错清单：原词 → 正确词；存疑项标（？）；无纠错留空字符串"
}

## 硬性要求
- sections 3–5 个；steps 3–5 条。
- 清单、对照、流程类内容必须放进 table，不要写成大段 bullet。
- table 的 headers 必须填写与 rows 同列的表头，禁止省略或留空数组；rows 每一行单元格数量与 headers 一致。
- 没有 table 的小节用 bullets；bullets 与 table 可同时存在。
"""


def yaml_str(s):
    s = str(s if s is not None else "").replace("\n", " ").strip()
    return "'" + s.replace("'", "''") + "'"


def clean_transcript(text: str) -> str:
    return _TS_RE.sub("", text or "").strip()


def load_transcript(row) -> str:
    tpath = Path(row["transcript_path"]) if row["transcript_path"] else None
    if tpath and tpath.exists():
        return clean_transcript(tpath.read_text(encoding="utf-8"))
    return ""


def cfg_ready(qn):
    return bool(qn.get("enabled")) and bool(qn.get("base_url")) and bool(qn.get("api_key")) and bool(qn.get("model"))


def call_llm(qn, meta_text: str, transcript: str) -> dict:
    base = str(qn["base_url"]).rstrip("/")
    url = base if base.endswith("/chat/completions") else base + "/chat/completions"
    body = {
        "model": qn["model"],
        "temperature": 0.3,
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": meta_text + "\n\n逐字稿：\n" + transcript},
        ],
    }
    resp = requests.post(
        url,
        headers={"Authorization": f"Bearer {qn['api_key']}", "Content-Type": "application/json"},
        json=body,
        timeout=int(qn.get("timeout_sec") or 60),
    )
    if resp.status_code >= 400:
        # 保留服务端原文（欠费 / 额度 / 模型名错误都会在这里说明），方便定位
        raise RuntimeError(f"模型接口返回 {resp.status_code}: {resp.text[:300]}")
    resp.raise_for_status()
    data = resp.json()
    content = (data.get("choices") or [{}])[0].get("message", {}).get("content", "")
    return parse_model_json(content)


def parse_model_json(content: str) -> dict:
    text = (content or "").strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if fence:
        text = fence.group(1).strip()
    return json.loads(text)


def normalize(page: dict) -> dict:
    """结构兜底：补全模型偶发遗漏的表头、对齐行列，尽量不丢内容。"""
    if not isinstance(page, dict) or page.get("skip"):
        return page
    for sec in page.get("sections") or []:
        if not isinstance(sec, dict):
            continue
        table = sec.get("table")
        if not table:
            continue
        if not isinstance(table, dict):
            table = {}
            sec["table"] = table
        headers = list(table.get("headers") or [])
        rows = []
        for r in table.get("rows") or []:
            rows.append(list(r) if isinstance(r, list) else [r])
        ncols = max([len(headers)] + [len(r) for r in rows] + [0])
        if ncols <= 0:
            sec.pop("table", None)
            continue
        if len(headers) < ncols:
            headers += [f"列{i + 1}" for i in range(len(headers), ncols)]
        rows = [r + [""] * (ncols - len(r)) for r in rows]
        table["headers"] = headers
        table["rows"] = rows
    return page


def validate(page: dict) -> str:
    """返回空串表示通过，否则为失败原因。"""
    if page.get("skip"):
        return ""
    for key in ("h1", "core", "slug"):
        if not str(page.get(key) or "").strip():
            return f"缺字段 {key}"
    sections = page.get("sections") or []
    if not (1 <= len(sections) <= 5):
        return f"小节数 {len(sections)} 不在 1–5"
    for i, sec in enumerate(sections):
        if not str(sec.get("h") or "").strip():
            return f"第 {i + 1} 节缺标题"
        table = sec.get("table")
        if table:
            headers = table.get("headers") or []
            rows = table.get("rows") or []
            if not headers:
                return f"第 {i + 1} 节表格缺表头"
            for row in rows:
                if len(row) != len(headers):
                    return f"第 {i + 1} 节表格行列数不一致"
    steps = page.get("steps") or []
    if not (3 <= len(steps) <= 5):
        return f"上手顺序 {len(steps)} 条，不在 3–5"
    return ""


def body_chars(page: dict) -> int:
    chunks = [page.get("core") or ""]
    for sec in page.get("sections") or []:
        chunks.append(sec.get("h") or "")
        chunks += sec.get("bullets") or []
        table = sec.get("table") or {}
        for row in table.get("rows") or []:
            chunks += [str(c) for c in row]
    chunks += page.get("steps") or []
    return len("".join(chunks))


def render_table(headers, rows):
    out = ["| " + " | ".join(headers) + " |", "| " + " | ".join(["---"] * len(headers)) + " |"]
    for r in rows:
        out.append("| " + " | ".join(str(c) for c in r) + " |")
    return "\n".join(out)


def render_quick_note(page: dict, meta, cover_name: str) -> str:
    tags = list(meta["tags"]) + ["速查"]
    fm = [
        "标题: " + yaml_str(meta["title"]),
        "作者: " + yaml_str(meta["author"]),
        "链接: " + yaml_str(meta["url"]),
        "来源: " + yaml_str(meta["source"]),
        "分类: " + yaml_str(meta["category"]),
        "标签:",
    ]
    for t in tags:
        fm.append("  - " + t)
    fm += [
        "发布时间: " + yaml_str(meta["published_at"]),
        "整理自: " + yaml_str(meta["orig_name"]),
        "整理时间: " + common.now_iso()[:10],
        "aweme_id: " + meta["aweme_id"],
        "状态: " + yaml_str(meta["status"]),
        "深挖: " + ("true" if meta["starred"] else "false"),
        "已赞: " + ("true" if meta["liked"] else "false"),
        "速查: true",
    ]
    body = [f"# {page['h1']}", ""]
    if cover_name:
        body += [f"![[{cover_name}]]", ""]
    body += ["> **一句话核心**：" + page["core"], ""]
    for sec in page["sections"]:
        body += ["## " + sec["h"], ""]
        if sec.get("table"):
            body.append(render_table(sec["table"]["headers"], sec["table"]["rows"]))
            body.append("")
        for b in sec.get("bullets") or []:
            body.append("- " + b)
        if sec.get("bullets"):
            body.append("")
    body += ["## 上手顺序", ""]
    for i, s in enumerate(page["steps"], 1):
        body.append(f"{i}. " + s)
    body.append("")
    corrections = str(page.get("corrections") or "").strip()
    if corrections:
        body.append("<!-- " + corrections + " -->")
        body.append("")
    return "---\n" + "\n".join(fm) + "\n---\n\n" + "\n".join(body)


def render_draft(page: dict, meta) -> str:
    tags = "、".join(list(meta["tags"]) + ["速查"])
    head = [
        f"# {page['h1']}",
        "",
        f"作者：{meta['author']}　｜　分类：{meta['category']}　｜　发布时间：{meta['published_at']}",
        f"链接：{meta['url']}",
        f"标签：{tags}",
        "",
    ]
    body = ["> **一句话核心**：" + page["core"], ""]
    for sec in page["sections"]:
        body += ["## " + sec["h"], ""]
        if sec.get("table"):
            body.append(render_table(sec["table"]["headers"], sec["table"]["rows"]))
            body.append("")
        for b in sec.get("bullets") or []:
            body.append("- " + b)
        if sec.get("bullets"):
            body.append("")
    body += ["## 上手顺序", ""]
    for i, s in enumerate(page["steps"], 1):
        body.append(f"{i}. " + s)
    body.append("")
    return "\n".join(head) + "\n\n" + "\n".join(body) + "\n"


def build_meta(con, row) -> dict:
    item = runmod.row_to_item(row)
    cover_name = ""
    cp = row["cover_path"] or ""
    if cp and Path(cp).exists():
        cover_name = Path(cp).name
    else:
        covers = Path(common.load_config()["vault_dir"]) / "attachments" / "covers"
        if (covers / f"{common.id8(row['aweme_id'])}.webp").exists():
            cover_name = f"{common.id8(row['aweme_id'])}.webp"
    orig_name = Path(row["note_path"]).stem if row["note_path"] else ""
    return {
        "aweme_id": row["aweme_id"],
        "id8": common.id8(row["aweme_id"]),
        "title": item["title"],
        "author": item["author_name"],
        "url": item["url"],
        "source": item["source"],
        "category": item["category"],
        "tags": item["tags_list"],
        "published_at": item["published_at"],
        "status": item["status"],
        "starred": item["starred"],
        "liked": item["liked"],
        "cover_name": cover_name,
        "orig_name": orig_name,
    }


def targets(con, only_id=None):
    out = []
    for row in db.all_items(con):
        if only_id and row["aweme_id"] != only_id:
            continue
        if (row["deep_state"] or "") != "已转写":
            continue
        if (row["quick_state"] or "") in ("已提炼", "已跳过"):
            continue
        quick_path, _ = notes.find_quick_note(Path(common.load_config()["vault_dir"]), row["aweme_id"])
        if quick_path is not None:
            # 旧库已有速查稿：补记状态，不重新提炼
            db.set_fields(con, row["aweme_id"], quick_state="已提炼", quick_at=common.now_iso())
            continue
        out.append(row)
    return out


def main(argv=None):
    parser = argparse.ArgumentParser(description="把已转写条目提炼成速查稿")
    parser.add_argument("--id", help="只处理指定 aweme_id")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--settings")
    args = parser.parse_args(argv)

    cfg = common.load_config(args.settings)
    qn = cfg.get("quicknote") or {}
    con = db.connect(cfg["paths"]["db"])

    if not cfg_ready(qn):
        print("速查提炼未启用或配置不完整（quicknote.enabled / base_url / api_key / model），跳过。")
        con.close()
        return 0

    rows = targets(con, only_id=args.id)
    if not rows:
        print("没有待提炼的已转写条目。")
        con.close()
        return 0

    print(f"待提炼：{len(rows)} 条")
    vault = Path(cfg["vault_dir"])
    deep_dir = vault / TRANSCRIBED_DIR
    draft_dir = Path(cfg["paths"]["db"]).parent / DRAFT_DIR_NAME
    counts = {"done": 0, "skip": 0, "failed": 0}

    for row in rows:
        label = (row["title"] or row["aweme_id"])[:36]
        transcript = load_transcript(row)
        if len(transcript) < 20:
            db.set_fields(con, row["aweme_id"], quick_state="已跳过", quick_at=common.now_iso(),
                          quick_reason="逐字稿过短或无有效内容")
            counts["skip"] += 1
            print(f"  · 跳过：{label}（逐字稿过短）")
            continue

        meta = build_meta(con, row)
        meta_text = (
            f"标题：{meta['title']}\n作者：{meta['author']}\n分类：{meta['category']}\n"
            f"标签：{'、'.join(meta['tags'])}\n发布时间：{meta['published_at']}\n链接：{meta['url']}"
        )
        if args.dry_run:
            print(f"  · 待提炼：{label}")
            counts["done"] += 1
            continue

        # 结构校验偶尔会被模型漏字段打回（实测长稿会偶发「表格缺表头」），
        # 同一篇重试一次比等第二天定时任务重跑更省 token，也更省人工。
        attempts = 1 + max(0, int(qn.get("retries", 1) or 0))
        page = None
        last_err = None
        for attempt in range(1, attempts + 1):
            try:
                limit = int(qn.get("max_input_chars") or 0)
                # limit <= 0 表示不截断：整篇逐字稿原样送入
                payload = transcript if limit <= 0 else transcript[:limit]
                candidate = call_llm(qn, meta_text, payload)
                candidate = normalize(candidate)
                err = validate(candidate)
                if err:
                    raise ValueError(err)
                page = candidate
                break
            except Exception as exc:  # noqa: BLE001 单条失败不中断整批
                last_err = exc
                if attempt < attempts:
                    print(f"  ↻ 第 {attempt} 次未通过（{exc}），重试")

        if page is None:
            err = last_err if last_err is not None else RuntimeError("模型未返回可用结果")
            counts["failed"] += 1
            db.set_fields(con, row["aweme_id"], quick_state="提炼失败",
                           quick_reason=str(err)[:200])
            print(f"  ✗ 失败：{label} — {err}")
            continue

        if page.get("skip"):
            reason = str(page.get("skip_reason") or "无可提炼内容")[:200]
            db.set_fields(con, row["aweme_id"], quick_state="已跳过", quick_at=common.now_iso(),
                          quick_reason=reason)
            counts["skip"] += 1
            print(f"  · 跳过：{label}（{reason}）")
            continue

        body_len = body_chars(page)
        mx = int(qn.get("max_body_chars") or 0)
        mn = int(qn.get("min_body_chars") or 0)
        # max_body_chars <= 0 表示不设篇幅参考区间，只记录实际字数
        if mx <= 0:
            print(f"  · 篇幅：{label} 正文约 {body_len} 字")
        elif not (mn <= body_len <= mx):
            print(f"  · 篇幅提示：{label} 正文约 {body_len} 字（参考 {mn}–{mx}），仍落盘")

        deep_dir.mkdir(parents=True, exist_ok=True)
        fname = f"{common.sanitize_filename(page['slug'], 40)}·速查-{meta['id8']}.md"
        (deep_dir / fname).write_text(
            render_quick_note(page, meta, meta["cover_name"]), encoding="utf-8", newline="\n"
        )
        draft_dir.mkdir(parents=True, exist_ok=True)
        (draft_dir / f"draft_{meta['id8']}.md").write_text(
            render_draft(page, meta), encoding="utf-8", newline="\n"
        )
        db.set_fields(
            con, row["aweme_id"],
            quick_state="已提炼", quick_at=common.now_iso(), quick_reason="",
        )
        counts["done"] += 1
        print(f"  ✓ 已提炼：{label}（{fname}）")

    con.close()
    print(f"\n【速查提炼】成功 {counts['done']} · 跳过 {counts['skip']} · 失败 {counts['failed']}")
    return 1 if counts["failed"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
