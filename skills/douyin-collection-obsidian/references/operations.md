# 日常操作

## 每周流程（用户视角，约 10 分钟）

1. 双击 `同步.cmd`：抓取收藏 + 稍后再看 → 更新笔记 / 封面 / 索引；
2. 浏览新增笔记，把想深挖的条目 frontmatter 改成 `深挖: true`（或本人点赞 `liked=1` 进深挖队列）；
3. 双击 `深挖.cmd`：下载原片 + GPU 转写逐字稿写回笔记；
4. 可选：双击 `提炼.cmd`：已转写条目交大模型生成速查稿与飞书草稿；
5. 可选：双击 `推送速查.cmd`：把速查草稿推送到飞书知识库并回写映射。

后两步为可选环节，未配置大模型 / 飞书应用凭据时对应入口安全跳过，不影响本地同步与深挖。

## 定时链路（Windows 计划任务）

整条无人值守链路按时间先后跑四个任务（均可 `-Daily` 每日或 `-Days` 指定星期）：

| 顺序 | 任务名 | 包装脚本 | 默认时间 | 日志 | 职责 |
| --- | --- | --- | --- | --- | --- |
| ① | `DouyinToObsidian-Sync` | `定时同步.cmd` | 每日 04:00 | `data\logs\scheduled.log` | 抓取收藏 / 稍后再看到本地库 |
| ② | `DouyinToObsidian-Deepdive` | `定时深挖.cmd` | 每日 04:30 | `data\logs\deepdive.log` | 下载原片并转写逐字稿 |
| ③ | `DouyinToObsidian-Quicknote` | `定时提炼.cmd` | 每日 05:00 | `data\logs\quicknote.log` | 已转写条目提炼速查稿 + 推送草稿 |
| ④ | `DouyinToObsidian-Feishu` | `定时推送.cmd` | 每日 05:30 | `data\logs\feishu.log` | 把速查草稿推送到飞书知识库 |

也可以只注册一个任务 `DouyinToObsidian-Chain` 跑 `定时全链路.cmd`：四步顺序执行，同步失败即中止，其余步骤失败只记 FAIL 不阻塞。这条链路的日志统一进 `data\logs\chain.log`，**超过 2MB 会自动归档为 `chain.log.1`**（只保留一份历史），不会无限膨胀。

注册命令（`-Kind` 取 `sync / deepdive / quicknote / feishu`）：

```powershell
# 注册某一类（每日 05:00 为例）
powershell -NoProfile -ExecutionPolicy Bypass -File "skills\douyin-collection-obsidian\scripts\register-scheduled-task.ps1" -Kind quicknote -Daily -At 05:00
# 每周指定星期（例：周三、周五 21:00）
powershell -NoProfile -ExecutionPolicy Bypass -File "skills\douyin-collection-obsidian\scripts\register-scheduled-task.ps1" -Kind sync -Days Wednesday,Friday -At 21:00
# 移除某一类
powershell -NoProfile -ExecutionPolicy Bypass -File "skills\douyin-collection-obsidian\scripts\register-scheduled-task.ps1" -Kind feishu -Remove
```

要点：

- 各任务执行对应 `定时*.cmd`（无 pause）；登录 Windows 后才会运行（`Interactive`），勾选「错过触发后尽快补跑」（`StartWhenAvailable`），关机 / 休眠错过会在开机后补上；
- ① 同步运行时会自动拉起专用 Edge 窗口；登录过期时退出码为 2，记在日志里；
- ③④ 凭据未配置时对应入口直接打印跳过，不报错、不中断整链路；
- 计划任务不设人工复核闸口：提炼通过校验后直接落稿，推送成功后回写飞书映射；需要人工把关时先不填凭据，手动跑 `提炼.cmd` / `推送速查.cmd`。

## 同步命令与输出解读

```bat
同步.cmd                :: 抓取 + 处理（全量扫描）
同步.cmd --dry-run      :: 只抓取和计算，不写入任何文件
同步.cmd --skip-fetch   :: 跳过抓取，用最近一次原始数据重新处理
同步.cmd --spike        :: M0 探测，输出 data\spike\M0-report.md
同步.cmd --status       :: 检查专用 Edge 调试端口
同步.cmd --login        :: 等同 登录抖音.cmd
```

摘要字段含义：

- **新增 / 更新 / 未变**：相对数据库的去重统计；「更新」只统计稳定字段（标题 / 作者 / 链接 / 发布时间 / 时长）的变化，签名 URL 的轮换不计入。
- **离开待看**：从「稍后再看」（或备用桥接收藏夹）消失的条目，状态自动改为 `已离开待看队列`；**仅当本次抓取完整时才会判定**，不完整时跳过。
- **未分类**：没有命中 `config\taxonomy.yml` 规则的条目数。
- **封面 成功/失败**、**笔记 写入/未变**：文件层面的落盘统计；第二次同样输入应为「未变」全量、写入 0。

退出码：`0` 成功；`1` 出错（细节在 `data\logs\run-*.md` 与控制台）；`2` 需要登录；`3` 调试端口未就绪。

## 分类体系

规则文件 `config\taxonomy.yml`：

```yaml
default: "未分类"
categories:
  - name: "AI技术与工具"
    priority: 100
    keywords: ["codex", "github", "教程"]
    authors: ["某作者"]
```

匹配逻辑：按 `priority` 从高到低逐条检查；标题或标签包含任一 `keyword`（不区分大小写），或作者命中 `authors`，即归入该类；全部未命中 → `default`。

推荐的分类调优流程（数据驱动，避免拍脑袋）：

1. 导出样本：`.\.venv\Scripts\python.exe -m processor.run --export-samples` → `data\samples\samples.jsonl`（最多 300 条，含标题 / 作者 / 标签 / 分类）；
2. 让 Codex 依据样本聚类出 8–12 个一级分类草案（附样例）；
3. 把定稿规则写入 `config\taxonomy.yml`，重跑 `同步.cmd --skip-fetch` 即可生效。

**用户覆盖优先**：单篇笔记的 frontmatter 被用户改过（分类 / 标签 / 状态 / 深挖）时，脚本读回并保持，不再自动覆盖；想恢复自动化就把该字段改回与脚本生成值一致或直接删除对应键。

## 深挖与转写

```bat
深挖.cmd                 :: 处理所有 深挖: true 且未转写的条目
深挖.cmd --id <aweme_id> :: 只处理某一条（无论是否已转写）
深挖.cmd --refresh       :: 已转写的也重新处理
```

流程：读回标星 → 下载原片（优先用最近抓取的签名直链；失效时用已登录 Edge 打开视频页刷新直链）→ **校验下载结果含视频流**（只有音频轨就换下一个地址，全都不行才退化成音频转写）→ ffmpeg 抽音频 → faster-whisper 转写（优先 `large-v3/cuda`，显存不足自动降级 `medium/cuda` → `medium/cpu`）→ 逐字稿写入 `data\transcripts\<id8>.md` 并回填到笔记的「逐字稿」折叠区块 → `deep_state` 更新为 `已转写`。

要点：

- 终态有三种：`已转写`、`已存图`、`无语音内容`，都是处理到底，之后每轮自动跳过（要重做加 `--refresh`）；只有 `已下载`（转写报错）会每轮重试；
- 判定「无语音」做了两道：先放宽 VAD（`threshold=0.2`、`min_silence_duration_ms=600`）跑一遍，仍为空就**关闭 VAD** 再跑一遍；再要求结果同时满足「总字数 ≥ 40」和「中文字符占比 ≥ 55%」——纯 BGM 会让 whisper 硬凑曲名或中英歌词（如 `Body set the clock`），这类结果会被挡掉，不写逐字稿；
- 真有口播但被 VAD 吞掉的（唱歌式讲述、轻声、远景），现在能被第二遍捞回来；想看更激进的召回可以调小 `WHISPER_VAD_THRESHOLD`（默认 0.2），换模型用 `WHISPER_MODEL`；
- `deepdive.keep_video: false` 时，转写成功后自动删除临时原片，只留逐字稿；
- 首次运行会下载模型（约 3GB，默认走 hf-mirror），之后离线可用；模型缓存在 `%USERPROFILE%\.cache\huggingface`，不要清理；
- 转写速度参考：RTX 4050 上约为实时的 6–7 倍（16 分钟视频约 3 分钟）；纯 CPU + `small` 约为实时的 1–1.5 倍；
- 拿到只有音频轨的条目（部分作品只发音频），会对音频转写并在日志里标注「拿不到画面」；
- `data\待摘要清单.md` 会列出已转写条目——让 Codex 读 `data\transcripts\*.md` 生成摘要，写入对应笔记的手动区即可（脚本不会覆盖手动区）。

## 速查提炼（③）与飞书推送（④）

```bat
提炼.cmd                 :: 处理所有 deep_state=已转写 且待提炼的条目
提炼.cmd --id <aweme_id> :: 只处理某一条
提炼.cmd --dry-run       :: 只列出待提炼条目，不调模型、不写稿
推送速查.cmd             :: 推送速查草稿（默认 --quick 通道）
推送速查.cmd --id <id> --force --dry-run
同步飞书.cmd             :: 旧通道：推送原始笔记（显式 --raw）
```

提炼链路（`processor\quicknote.py`）：选 `deep_state=已转写`、`quick_state` 非已提炼/已跳过、且尚无速查稿的条目 → 读逐字稿去时间戳 → 调 OpenAI 兼容接口按锁定规范输出结构化 JSON → 校验（小节 1–5、上手顺序 3–5、表格行列对齐）→ 通过则落 `vault\已深挖\<主题>·速查-<id8>.md` 与草稿 `data\feishu_drafts\draft_<id8>.md`；模型判定无可提炼内容记「已跳过」并附原因；调用 / 校验失败记「提炼失败」，下次重试，不覆盖已有速查稿。

推送链路（`processor\feishu.py --quick`）：选 `quick_state=已提炼`、无飞书映射、草稿存在的条目 → tenant 自建应用身份上传 Markdown → 导入为 docx → 挂到知识库 `parent_wiki_token` → 回写 `feishu_doc_token / feishu_node_token / feishu_synced_at`。飞书稿不带封面插图；本地速查稿保留 `![[<id8>.webp]]` 封面引用。

速查规范（提炼 prompt 内锁定）：保留可复用方法 / 框架 / 步骤 / 模型 / 工具；删案例复述、铺垫、闲聊、口语连接词与营销腔；保留原文数字 / 模型名 / 框架缩写；语音错词按通行写法改正、存疑加（？）不臆造；正文 300–1200 字；3–5 小节、上手顺序 3–5 条；清单类用 Markdown 表格；标签加「速查」。跳过情形：观点情绪 / 个人回忆 / 个案连麦 / 过短无可提炼方法论。

## 状态语义

| 状态 | 含义 |
| --- | --- |
| 待看 | 在「稍后再看」或备用桥接收藏夹中 |
| 已离开待看队列 | 已从队列消失（抓取完整时自动标记）；可在笔记里手改为 已看 / 弃看 |
| 未标记 | 普通收藏条目 |

提炼状态（`quick_state`，提炼环节维护，不写进笔记 frontmatter）：

| quick_state | 含义 |
| --- | --- |
| （空） | 尚未进入提炼队列 |
| 已提炼 | 速查稿已落本地库；草稿在 `data\feishu_drafts\` |
| 已跳过 | 模型或长度判定无可提炼方法论，`quick_reason` 记原因 |
| 提炼失败 | 调用 / 校验失败，下次定时任务重试 |

飞书映射：`feishu_doc_token / feishu_node_token` 非空即已推送，增量跳过；`--force` 对已推送条目**原地覆盖正文**（调 `docs_ai` 的 overwrite，节点与 wiki 链接不变，不产生第二篇文档）。

## 笔记契约

frontmatter 字段（固定）：`标题 / 作者 / 链接 / 来源 / 分类 / 标签 / 状态 / 发布时间 / 首次入库 / 深挖 / aweme_id`。

- 文件名：`YYYY-MM-DD 作者-标题-<id8>.md`（按发布日期排序），自动剔除 `# ^ [ ]` 等 Obsidian 链接特殊字符（标题原文保留在 frontmatter）；首次生成后不随标题变化（可自行改名，脚本按 `aweme_id` 认领）；旧式命名可用 `python -m processor.reorder --apply` 一次性对齐。
- 索引页与封面用最短路径链接（`[[文件名|标题]]`、`![[<id8>.webp]]`）：把 `抖音收藏` 放在 vault 任意层级，点击都能正确跳转、不会新建空笔记。
- 一键改 `深挖` / `状态` / `分类` / `标签` 即可，脚本下次运行读回；
- `<!-- 自动生成结束 · 以下为手动区，脚本不会修改 -->` 以下为手动区；分界线以上由脚本维护。

## 备份与维护

- 需要备份的是：vault（或至少 `抖音收藏\` 子目录）、`config\settings.yml`、`config\taxonomy.yml`；`data\` 里也有数据库与逐字稿，可按需保留。
- 想重新整理：`同步.cmd --skip-fetch` 可基于最近一次抓取重算分类 / 索引，不会重新抓取。
