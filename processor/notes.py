# -*- coding: utf-8 -*-
"""Obsidian 笔记与索引生成（幂等，保留手动区）。"""
from __future__ import annotations

import re
import shutil
from pathlib import Path

from . import common

DIVIDER = "<!-- 自动生成结束 · 以下为手动区，脚本不会修改 -->"

_DATE10_RE = re.compile(r"^\d{4}-\d{2}-\d{2}")

NOTE_DIR = "条目"
TRANSCRIBED_DIR = "已深挖"

# 速查稿标记：frontmatter 中 `速查: true`，或带有 `整理自` 字段（向后兼容旧速查稿）
QUICK_FIELD = "速查"


def _is_quick_fm(fm):
    if not fm:
        return False
    flag = fm.get(QUICK_FIELD)
    if flag is True or str(flag).lower() in ("true", "yes", "1"):
        return True
    return bool(fm.get("整理自"))


def find_quick_note(vault, aweme_id):
    """在「条目 / 已深挖」目录按 id8 查找已速查化的笔记，返回 (path, fm) 或 (None, None)。"""
    tail = common.id8(aweme_id)
    for sub in (TRANSCRIBED_DIR, NOTE_DIR):
        base = Path(vault) / sub
        if not base.exists():
            continue
        for p in base.glob("*.md"):
            if tail not in p.stem:
                continue
            try:
                fm, _ = common.parse_frontmatter(p.read_text(encoding="utf-8"))
            except Exception:
                continue
            if _is_quick_fm(fm):
                return p, fm
    return None, None


def date_prefix(published_at):
    """发布日期的排序前缀（`YYYY-MM-DD `）；缺失或格式异常时返回空串。"""
    s = str(published_at or "")
    return f"{s[:10]} " if _DATE10_RE.match(s) else ""


def default_filename(item):
    author = common.sanitize_filename(item.get("author_name") or "未知作者", 24, "未知作者")
    title = common.sanitize_filename(item.get("title") or "无标题", 60, "无标题")
    return f"{date_prefix(item.get('published_at'))}{author}-{title}-{common.id8(item['aweme_id'])}.md"


def build_frontmatter(item):
    return {
        "标题": item.get("title") or "（无标题）",
        "作者": item.get("author_name") or "",
        "链接": item.get("url") or "",
        "来源": item.get("source") or "",
        "分类": item.get("category") or "未分类",
        "标签": list(item.get("tags_list") or []),
        "状态": item.get("status") or "",
        "发布时间": item.get("published_at") or "",
        "首次入库": item.get("first_seen_at") or "",
        "深挖": bool(item.get("starred")),
        "已赞": bool(item.get("liked")),
        "aweme_id": item.get("aweme_id") or "",
    }


def cover_relpath(item):
    cover = item.get("cover_path")
    if not cover:
        return None
    p = Path(cover)
    if not p.exists():
        return None
    return p.name


def build_auto_body(item, cover_rel, transcript_text=None):
    lines = []
    if cover_rel:
        lines.append(f"![[{cover_rel}]]")
        lines.append("")
    lines.append(f"- 作者：{item.get('author_name') or '未知'}")
    duration = common.fmt_duration(item.get("duration_sec"))
    if duration:
        lines.append(f"- 时长：{duration}")
    lines.append(f"- 发布：{item.get('published_at') or '未知'} ｜ 首次入库：{item.get('first_seen_at') or ''}")
    links = f"- 原链接：[在抖音打开]({item.get('url') or ''})"
    if item.get("share_url") and item.get("share_url") != item.get("url"):
        links += f" ｜ [分享链接]({item['share_url']})"
    lines.append(links)
    folders = item.get("folders") or []
    if folders:
        lines.append(f"- 收藏夹：{'、'.join(folders)}")
    lines.append("")
    title_text = (item.get("title") or "").strip()
    if title_text:
        lines.append("> [!note] 视频文案")
        for ln in title_text.splitlines():
            lines.append(f"> {ln}" if ln.strip() else ">")
        lines.append("")
    if transcript_text:
        lines.append("> [!quote]- 逐字稿（自动生成）")
        for line in transcript_text.splitlines():
            lines.append(f"> {line}" if line else ">")
        lines.append("")
    lines.append(DIVIDER)
    return "\n".join(lines) + "\n"


def split_manual(text):
    if not text:
        return ""
    idx = text.find(DIVIDER)
    if idx < 0:
        return ""
    manual = text[idx + len(DIVIDER):]
    if manual.startswith("\n"):
        manual = manual[1:]
    return manual


def note_dir_for(item, vault):
    """已转写的笔记归入「已深挖」文件夹，其余留在「条目」。"""
    return Path(vault) / (TRANSCRIBED_DIR if (item.get("deep_state") or "") == "已转写" else NOTE_DIR)


# 流水线自有目录，不参与「外部笔记」扫描
RESERVED_DIRS = {NOTE_DIR, TRANSCRIBED_DIR, "分类", "attachments", ".obsidian", ".trash"}
AUTO_MARK = "由脚本自动生成"


def _note_title(p):
    """外部笔记标题：优先 frontmatter 的「标题」，否则用文件名。"""
    try:
        text = p.read_text(encoding="utf-8")
        fm = common.parse_frontmatter(text)[0] or {}
        title = str(fm.get("标题") or fm.get("title") or "").strip()
        if title:
            return title
    except Exception:
        pass
    return p.stem


def scan_external_notes(vault):
    """收集流水线之外的笔记，按文件夹名归为分类。

    覆盖两类位置：vault 根目录下除保留目录以外的文件夹（如 `GEO运营/`），
    以及 `分类/` 下的子文件夹（手动归档进分类的笔记）。
    返回 {分类名: [Path, ...]}。
    """
    vault = Path(vault)
    groups = {}
    if not vault.exists():
        return groups
    for d in sorted(vault.iterdir()):
        if not d.is_dir():
            continue
        if d.name == "分类":
            for sub in sorted(d.iterdir()):
                if sub.is_dir():
                    groups.setdefault(sub.name, []).extend(sorted(sub.rglob("*.md")))
            continue
        if d.name in RESERVED_DIRS:
            continue
        groups.setdefault(d.name, []).extend(sorted(d.rglob("*.md")))
    return groups


def _external_line(p, vault):
    rel = p.relative_to(vault).as_posix()
    try:
        import datetime
        mtime = datetime.date.fromtimestamp(p.stat().st_mtime).isoformat()
    except Exception:
        mtime = ""
    tail = f" — {mtime}" if mtime else ""
    return f"- [[{p.stem}|{_note_title(p)}]]{tail}　`{rel}`"


def write_note(item, cfg, transcript_text=None):
    vault = Path(cfg["vault_dir"])
    # 速查稿保护：已速查化的笔记不被自动重建、迁移或覆盖，原样沿用
    quick_path, quick_fm = find_quick_note(vault, item["aweme_id"])
    if quick_path is not None:
        # 旧速查稿可能缺 状态/深挖/已赞 等自动字段，先用默认值补齐再合并，避免下游取键崩溃
        fm = {**build_frontmatter(item), **(quick_fm or {})}
        return "unchanged", quick_path, fm
    target_dir = note_dir_for(item, vault)
    target_path = target_dir / default_filename(item)
    old_path = Path(item["note_path"]) if item.get("note_path") else None
    # 转写状态变化导致目录切换：把旧文件迁到目标目录（文件名不变）
    if old_path and old_path.exists() and old_path.resolve() != target_path.resolve():
        target_dir.mkdir(parents=True, exist_ok=True)
        if target_path.exists():
            target_path.unlink()
        shutil.move(str(old_path), str(target_path))
    path = target_path
    old = path.read_text(encoding="utf-8") if path.exists() else ""
    manual = split_manual(old)
    fm = build_frontmatter(item)
    content = common.dump_frontmatter(fm) + build_auto_body(item, cover_relpath(item), transcript_text) + manual
    if old == content:
        return "unchanged", path, fm
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8", newline="\n")
    return "written", path, fm


def _index_line(item):
    note_path = item.get("note_path") or ""
    target = Path(note_path).with_suffix("").name if note_path else ""
    display = (item.get("title") or "（无标题）").replace("|", "｜")
    if not target:
        return f"- {display} — {item.get('author_name') or '未知'}"
    suffix = f" — {item.get('author_name') or '未知'}"
    status = item.get("status") or ""
    if status and status != "未标记":
        suffix += f" · {status}"
    return f"- [[{target}|{display}]]{suffix}"


def write_transcribed_index(items, cfg):
    """生成「已深挖」清单：只收录已有逐字稿（已转写）的条目。"""
    vault = Path(cfg["vault_dir"])
    done = [it for it in items if it.get("deep_state") == "已转写"]
    done.sort(key=lambda x: (x.get("first_seen_at") or "", x.get("aweme_id") or ""), reverse=True)
    lines = ["# 逐字稿清单", "", f"> 共 {len(done)} 条 · 已有逐字稿 · 笔记在「已深挖」文件夹", ""]
    lines += [_index_line(it) for it in done]
    return common.write_text_if_changed(vault / "逐字稿清单.md", "\n".join(lines) + "\n")


START_HERE = """# 开始这里

这是「抖音收藏本地库」的说明书页，由脚本自动维护。

## 结构

- `条目/`：未转写的普通收藏笔记（自动生成 + 你的手动区），文件名以发布日期开头
- `已深挖/`：转写完成、已有逐字稿的笔记单独归到这个文件夹
- `分类/`：按分类自动聚合的清单
- `待看清单.md`：仍处于「待看」的条目（收藏 + 稍后再看）
- `逐字稿清单.md`：已有逐字稿条目的汇总索引，笔记实体在「已深挖」文件夹
- `未分类.md` / `统计.md`：兜底清单与统计

## 手动区规则

每篇笔记里有一条 `<!-- 自动生成结束 · 以下为手动区，脚本不会修改 -->` 分界线。
分界线**以下**随便写（笔记、摘要、想法），脚本永远不动它。

## 怎么让脚本"听你的"

直接改笔记的 frontmatter（文件顶部 `---` 之间）即可，脚本下次同步会读到：

| 字段 | 作用 |
| --- | --- |
| `分类` | 覆盖自动分类 |
| `标签` | 覆盖自动标签（YAML 列表写法） |
| `状态` | 改成 已看 / 弃看 / 待看 等 |
| `深挖` | 改成 `true` 后，运行「深挖.cmd」会下载原片并转写 |

## 每周流程（约 10 分钟）

1. 双击「同步.cmd」（自动抓取收藏 + 稍后再看，并更新队列状态）；
2. 扫一眼新增条目，给想深挖的标 `深挖: true`；
3. （可选）双击「深挖.cmd」，等它下载 + 转写。
"""


def write_indexes(items, cfg, rules):
    vault = Path(cfg["vault_dir"])
    written = 0
    by_cat = {}
    for item in items:
        by_cat.setdefault(item.get("category") or "未分类", []).append(item)

    # 外部笔记（非抖音流水线产出）：按所在文件夹名归入同名分类，与 DB 分类合并进同一份索引
    external = scan_external_notes(vault)
    for name in external:
        by_cat.setdefault(name, [])

    cat_dir = vault / "分类"
    cat_dir.mkdir(parents=True, exist_ok=True)
    generated = set()
    for name, lst in sorted(by_cat.items()):
        lst.sort(key=lambda x: (x.get("first_seen_at") or "", x.get("aweme_id") or ""), reverse=True)
        ext = external.get(name) or []
        if not lst and not ext:
            continue
        total = len(lst) + len(ext)
        lines = [f"# {name}", "", f"> 共 {total} 条 · 由脚本自动生成", ""]
        if lst:
            if ext:
                lines += ["## 抖音条目", ""]
            lines += [_index_line(it) for it in lst]
            lines.append("")
        if ext:
            lines += ["## 外部笔记（手动或其他模型添加，按文件夹名归入本分类）", ""]
            lines += [_external_line(p, vault) for p in ext]
            lines.append("")
        fname = f"{common.sanitize_filename(name, 40)}.md"
        generated.add(fname)
        if common.write_text_if_changed(cat_dir / fname, "\n".join(lines) + "\n"):
            written += 1
    # 只清理脚本自己生成过的索引；手动维护（无自动标记）的文件保留
    for old in cat_dir.glob("*.md"):
        if old.name in generated:
            continue
        try:
            head = old.read_text(encoding="utf-8")[:300]
        except Exception:
            continue
        if AUTO_MARK in head:
            old.unlink()

    uncat = by_cat.get("未分类", [])
    lines = ["# 未分类", "", f"> 共 {len(uncat)} 条 · 由脚本自动生成", ""]
    lines += [_index_line(it) for it in uncat]
    if common.write_text_if_changed(vault / "未分类.md", "\n".join(lines) + "\n"):
        written += 1

    todo = [it for it in items if (it.get("status") or "") == "待看"]
    todo.sort(key=lambda x: (x.get("first_seen_at") or "", x.get("aweme_id") or ""), reverse=True)
    lines = ["# 待看清单", "", f"> 共 {len(todo)} 条 · 由脚本自动生成；离开「稍后再看」后会自动移出", ""]
    lines += [_index_line(it) for it in todo]
    if common.write_text_if_changed(vault / "待看清单.md", "\n".join(lines) + "\n"):
        written += 1

    if write_transcribed_index(items, cfg):
        written += 1
    # 清理旧版根目录清单（实体笔记已改放「已深挖」文件夹，清单更名「逐字稿清单.md」）
    legacy = vault / "已深挖.md"
    if legacy.is_file():
        legacy.unlink()

    status_count = {}
    source_count = {}
    for it in items:
        status_count[it.get("status") or "（空）"] = status_count.get(it.get("status") or "（空）", 0) + 1
        source_count[it.get("source") or "（空）"] = source_count.get(it.get("source") or "（空）", 0) + 1
    starred = sum(1 for it in items if it.get("starred"))
    transcribed = sum(1 for it in items if it.get("deep_state") == "已转写")
    ext_total = sum(len(v) for v in external.values())
    stat = [
        "# 统计",
        "",
        f"- 总条目：{len(items)}",
        f"- 已标星（深挖）：{starred}",
        f"- 已转写：{transcribed}",
        f"- 外部笔记：{ext_total} 篇 / {len(external)} 个分类",
        "",
        "## 按分类",
        "",
    ]
    counts = {name: len(lst) + len(external.get(name) or []) for name, lst in by_cat.items()}
    for name, count in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])):
        stat.append(f"- {name}：{count}")
    stat += ["", "## 按状态", ""]
    for name, count in sorted(status_count.items(), key=lambda kv: (-kv[1], kv[0])):
        stat.append(f"- {name}：{count}")
    stat += ["", "## 按来源", ""]
    for name, count in sorted(source_count.items(), key=lambda kv: (-kv[1], kv[0]))[:30]:
        stat.append(f"- {name}：{count}")
    if common.write_text_if_changed(vault / "统计.md", "\n".join(stat) + "\n"):
        written += 1

    bridge = cfg["lists"]["watch_later_bridge_folder"]
    if common.write_text_if_changed(vault / "开始这里.md", START_HERE.replace("{bridge}", bridge)):
        written += 1
    return written
