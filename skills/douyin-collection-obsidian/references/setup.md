# 安装与配置

## 前置条件

| 依赖 | 要求 | 检查命令 |
| --- | --- | --- |
| Windows | 10/11（工具为 Windows 批处理 + PowerShell 环境设计） | — |
| Edge | 已安装（抓取复用其登录态） | `Get-Command msedge` 或看默认安装路径 |
| Python | 3.12+（3.13 亦可），需在 PATH | `python --version` |
| Node.js | 不要求系统安装。用仓库自带的 `tools\node`（Releases 提供），`准备.cmd` 会自动加进 PATH | `tools\node\node.exe --version` |
| ffmpeg | 不要求系统安装。用 `tools\ffmpeg\bin\ffmpeg.exe` | `tools\ffmpeg\bin\ffmpeg.exe -version` |
| NVIDIA 显卡（可选） | 约 6GB 显存跑 large-v3；无卡自动回落 CPU + small | `nvidia-smi` |

## 安装步骤

1. 克隆仓库并进入根目录（根目录应有 `同步.cmd`、`fetcher\`、`processor\`）：

   ```bat
   git clone https://github.com/fuguiq7-ops/douyin-collection-obsidian.git
   cd douyin-collection-obsidian
   ```

2. 从 Releases（`/releases/latest`）下载 `node-tools.zip`、`ffmpeg-tools.zip`，**两个包都解压到仓库根的 `tools\`**，解压后应存在 `tools\node\node.exe` 与 `tools\ffmpeg\bin\ffmpeg.exe`。这两个二进制体积大（83 / 100.5 MB，后者超过 GitHub 单文件硬限），所以不入库。

3. 双击 `准备.cmd`（或命令行运行）。它会：
   - 创建 Python 虚拟环境 `.venv\`；
   - 安装核心依赖（PyYAML / Pillow / requests）与深挖依赖（yt-dlp / faster-whisper / PyAV<19 / CUDA 运行库 `nvidia-cublas-cu12`、`nvidia-cudnn-cu12`）；
   - 安装 `fetcher\` 的 Node 依赖（puppeteer-core / yaml）；
   - 首次自动把 `config\settings.example.yml` 复制为 `config\settings.yml`；
   - pip 默认走清华镜像（设置环境变量 `PIP_INDEX_URL` 可覆盖）。

3. 按需修改 `config\settings.yml`（见下节）。至少确认 `vault_dir` 与 `media_dir`。

4. 双击 `登录抖音.cmd`：会打开一个**专用 Edge 窗口**（独立配置目录，与日常浏览器互不影响），用抖音 App 扫码登录一次。登录态保存在 `%LOCALAPPDATA%\douyin-library\edge-profile`，后续无需重复登录。

5. 双击 `同步.cmd` 首次全量同步。建议同时跑一次 `同步.cmd --spike` 生成 `data\spike\M0-report.md`，记录收藏 / 收藏夹 / 稍后再看三个接口的捕获情况与页面结构探测结果。

## 配置项（config\settings.yml）

| 键 | 说明 | 默认 |
| --- | --- | --- |
| `vault_dir` | Obsidian 库输出目录（vault 内的独立子目录） | 示例值需修改 |
| `media_dir` | 深挖原片 / 图集存放目录 | 示例值需修改 |
| `edge.executable` | Edge 路径，留空自动探测 | `""` |
| `edge.port` | 专用调试端口 | `9333` |
| `edge.profile_dir` | 专用浏览器配置目录（保存登录态） | `%LOCALAPPDATA%\douyin-library\edge-profile` |
| `capture.delay_min_sec` / `delay_max_sec` | 每页随机等待区间（秒） | `2` / `5` |
| `capture.max_idle_scrolls` | 连续无新数据的滚动次数（判到底） | `5` |
| `capture.max_scrolls` | 单次抓取滚动硬上限 | `400` |
| `covers.width` / `max_bytes` | 封面压缩规格（webp） | `480` / `61440` |
| `deepdive.keep_video` | `true` 保留原片；`false` 转写后自动删片只留逐字稿 | `true` |
| `lists.watch_later_bridge_folder` | 备用桥接收藏夹名（仅当稍后再看抓取失败时启用） | `稍后再看·留存` |
| `quicknote.enabled` | 速查提炼总开关；`false` 或缺 `base_url/api_key/model` 时安全跳过 | `false` |
| `quicknote.base_url / api_key / model` | OpenAI 兼容接口地址、密钥、模型名（如火山方舟 `https://ark.cn-beijing.volces.com/api/v3`） | `""` |
| `quicknote.timeout_sec / max_input_chars / min_body_chars / max_body_chars` | 单次调用超时；送模型的逐字稿上限（`0` = 不截断）；正文篇幅上下限（都为 `0` = 不校验、不灌水） | `120 / 0 / 0 / 0` |
| `feishu.enabled` | 飞书推送总开关；凭据 / 空间 / 权限未就绪前保持 `false` | `false` |
| `feishu.app_id / app_secret` | 自建应用凭证（tenant 身份，不走 user 登录态） | `""` |
| `feishu.space_id / parent_wiki_token` | 目标知识库空间 ID 与挂载父节点 token | `""` |
| `feishu.include_covers` | 推送时是否带封面插图（首版默认不带） | `false` |
| `paths.*` | 分类规则、SQLite、原始数据、日志、探测报告的位置 | 见示例文件 |

## 验证清单

- `登录抖音.cmd` 后：`.\.venv\Scripts\python.exe -m processor.sync --status` 输出 `CDP 就绪`；
- 首次同步输出类似：`[完成] 收藏 N 页 / 收藏夹 M 个 / 稍后再看: K 条`，随后是处理摘要（新增 / 更新 / 未分类 / 封面成败）;
- vault 中出现 `条目\`、`分类\`、`开始这里.md`、`待看清单.md`、`统计.md`；
- 离线自检：`cd fetcher; node test-capture.mjs`（5 项）与 `.\.venv\Scripts\python.exe -m processor.selfcheck`（29 项）全绿。

## 可选环节前提：大模型提炼与飞书推送

速查提炼（③）与飞书推送（④）默认关闭，凭据未就绪时入口直接跳过。需要启用时分别准备：

- **大模型接口**：任一 OpenAI 兼容服务，在 `quicknote` 段填 `base_url / api_key / model` 并把 `enabled` 置 `true`；脚本走 `/chat/completions`，要求返回 JSON 对象。阿里百炼可直接用 `base_url: https://dashscope.aliyuncs.com/compatible-mode/v1`；模型名要用免费额度覆盖的（如 `qwen3.7-plus`），填 `qwen-plus` 会走按量计费——余额为 0 时接口返回 `{"code":"Arrearage"}`，全部模型一律拒绝。免费额度只在华北2（北京）地域、每模型 100 万 token / 90 天有效，专属 key（`sk-sp-` 前缀）不抵扣。
- **飞书自建应用**：在飞书开放平台创建应用，开通 `wiki:wiki`、`drive:drive`、`docx:document`，**发布版本**后，到目标知识库页面右上「⋯ → 更多 → 添加文档应用」把应用加进去（走「成员设置」加人不生效）；在 `feishu` 段填 `app_id / app_secret / space_id / parent_wiki_token`，`enabled` 置 `true`。推送用 tenant 应用身份，不依赖会话内的 user 登录态。`apply_when_no_perm: true` 的自助申请会被 `131006` 拒，保持 `false` 手动授权。
- 联调顺序：先 `提炼.cmd --dry-run` 看选条，再 `推送速查.cmd --dry-run` 看草稿清单，最后去掉预演实跑；单条可用 `--id`。

## 登录机制与安全

- 抓取不保存账号密码；只复用专用 Edge 配置里的登录态（Cookie）。
- `node fetcher\export-cookies.mjs` 可导出 `data\edge-cookies.txt`（Netscape 格式，含登录态）供 `深挖` 下载受限视频使用；**该文件绝不外传、绝不提交**。
- 登录过期时同步会以退出码 2 中止并提示重新扫码，不会静默失败。
- 专用浏览器窗口可以保留常开；关闭也不影响，下次抓取会自动拉起。

## 更新与卸载

- 更新工具：`git pull` 后重跑 `准备.cmd`（会补齐新依赖）。
- 卸载：删除 `.venv\`、`node_modules\` 即可停用；`data\`、媒体目录、vault 是用户数据，按需保留或自行清理。
