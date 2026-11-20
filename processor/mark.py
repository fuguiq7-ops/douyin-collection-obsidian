# -*- coding: utf-8 -*-
"""批量标记 / 取消深挖队列：直接改数据库并回写笔记 frontmatter，不必逐个打开文件。

用法示例（在工具根目录）：
  python -m processor.mark --folder 学              # 把「学」收藏夹条目标为深挖
  python -m processor.mark --keyword 教程          # 标题/标签含关键词的标为深挖
  python -m processor.mark --all                   # 全部未转写视频标为深挖
  python -m processor.mark --folder 学 --clear     # 取消该收藏夹的标记
  python -m processor.mark --folder 学 --dry-run # 只列出，不写入
"""
from __future__ import annotations

import argparse
from pathlib import Path

from . import common, db


def matches(row, folders, keywords, include_images):
    if row["kind"] == "images" and not include_images:
        return False
    if folders:
        row_folders = db.folders_of(row)
        if not any(f in row_folders for f in folders):
            return False
    if keywords:
        blob = (row["title"] or "").lower() + " " + " ".join(db.tags_of(row)).lower()
        if not any(k.lower() in blob for k in keywords):
            return False
    return True


def sync_note_frontmatter(row, starred):
    """把标记同步回笔记 frontmatter 的「深挖」字段，保留手动区。"""
    note_path = row["note_path"]
    if not note_path:
        return False
    p = Path(note_path)
    if not p.exists():
        return False
    text = p.read_text(encoding="utf-8")
    fm, body = common.parse_frontmatter(text)
    if not fm:
        return False
    fm["深挖"] = bool(starred)
    p.write_text(common.dump_frontmatter(fm) + body, encoding="utf-8", newline="\n")
    return True


def main(argv=None):
    parser = argparse.ArgumentParser(description="批量标记/取消深挖队列")
    parser.add_argument("--folder", action="append", default=[], help="按收藏夹筛选，可重复")
    parser.add_argument("--keyword", action="append", default=[], help="按标题/标签关键词筛选，可重复")
    parser.add_argument("--all", action="store_true", help="选中全部符合条件条目")
    parser.add_argument("--images", action="store_true", help="同时包含图集（默认只选视频）")
    parser.add_argument("--include-done", action="store_true", help="包含已转写条目（默认跳过）")
    parser.add_argument("--clear", action="store_true", help="取消标记，移出深挖队列")
    parser.add_argument("--dry-run", action="store_true", help="只列出候选，不写入")
    parser.add_argument("--settings")
    args = parser.parse_args(argv)

    cfg = common.load_config(args.settings)
    con = db.connect(cfg["paths"]["db"])

    if not (args.folder or args.keyword or args.all):
        total = con.execute("select count(*) from items").fetchone()[0]
        starred = sum(
            1
            for r in db.all_items(con)
            if (r["starred_override"] if r["starred_override"] is not None else r["starred"])
        )
        print(f"当前库 {total} 条，已标星 {starred} 条。未指定筛选条件，未做任何改动。")
        print("用法示例：")
        print("  标星.cmd --folder 学")
        print("  标星.cmd --keyword 教程")
        print("  标星.cmd --all                 （全部未转写视频）")
        print("  标星.cmd --folder 学 --clear    （取消标记）")
        con.close()
        return 0

    targets = []
    for row in db.all_items(con):
        if not args.include_done and row["deep_state"] == "已转写":
            continue
        if matches(row, args.folder, args.keyword, args.images):
            targets.append(row)

    flag = 0 if args.clear else 1
    label = "取消标记" if args.clear else "标星"
    suffix = "（预览 dry-run，未写入）" if args.dry_run else ""
    print(f"{label}候选：{len(targets)} 条{suffix}")
    for row in targets:
        folders = "、".join(db.folders_of(row)) or "—"
        print(f"  [{row['kind']}] {row['title'] or row['aweme_id']}（{folders}）")

    if args.dry_run:
        con.close()
        return 0

    for row in targets:
        db.set_fields(con, row["aweme_id"], starred=flag, starred_override=flag)
        sync_note_frontmatter(row, flag)
    con.close()
    print(f"完成：{label} {len(targets)} 条。接下来运行「深挖.cmd」开始转写。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
