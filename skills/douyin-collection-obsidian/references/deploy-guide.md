# 引导式部署手册（从零带到跑通）

给「什么都没准备」的用户装这套工具时按本手册走：**先问诊、再动手，每步给验证命令，缺什么补什么，代不了的事明确说代不了**。不要一次性把十步全抛给用户。

## 0. 先问诊，别急着装

在工具根目录依次跑，把结果对号入座：

```bat
python --version
tools\node\node.exe --version
tools\ffmpeg\bin\ffmpeg.exe -version
```

| 结果 | 判断 | 下一步 |
| --- | --- | --- |
| 三条都出版本号 | 环境齐了 | 直接跳到 §4 准备.cmd |
| python 报找不到 | 缺 Python | §2 |
| 后两条报找不到 | 缺运行时包 | §3 |
| 仓库目录本身不存在 | 还没拉代码 | §1 |

## 1. 拉仓库

```bat
git clone https://github.com/fuguiq7-ops/douyin-collection-obsidian.git
cd douyin-collection-obsidian
```

- 用户机器没有 git：先装 Git for Windows。`winget install --id Git.Git` 在国内常报 `InternetOpenUrl() failed. 0x80072efd`，改用镜像 <https://registry.npmmirror.com/binary.html?path=git-for-windows/>，或直接让用户用 GitHub Desktop（图形界面里 `Add an Existing Repository` / `Publish` 都不需要命令行）。
- 装完在**旧终端**里仍报 `git 不是内部或外部命令`：PATH 未刷新，关掉重开窗口即可，不用重装。

## 2. 装 Python

- 官网 <https://www.python.org/downloads/>，3.12 或 3.13；安装时**必须勾 Add python.exe to PATH**。
- 验证：`python --version`；只装了 Store 版可能要用 `py -3 --version`。
- 这一步 AI 只能给链接和注意事项，UAC 提权与勾选项要用户自己点。

## 3. 补运行时（node + ffmpeg）

1. 打开 <https://github.com/fuguiq7-ops/douyin-collection-obsidian/releases/latest>；
2. 下载 `node-tools.zip`、`ffmpeg-tools.zip`；
3. **两个包都解压到仓库根的 `tools\`**（包内已带 `node\` / `ffmpeg\bin\` 层级，不用手动建子目录）；
4. 回到 §0 的三条命令复验。

## 4. 装依赖

```bat
准备.cmd
```

要看到结尾的「准备完成」。脚本会优先用 `tools\node` 里的 node/npm，所以机器没装 Node 也能跑。

| 报错 | 含义 |
| --- | --- |
| 没找到 Python | §2 没做或 PATH 没生效 |
| 没找到 npm | `tools\node` 没解压好（§3） |

## 5. 改配置

`config\settings.yml` 至少确认两处：

- `vault_dir`：**必须是已存在且可写的目录**；不存在会中止并报「库目录不存在」，脚本不会另建空库；
- `media_dir`：原片/图集存放处，别放进 vault，更别放进仓库。

`sync.include_folders` 留空 `[]` = 同步全部收藏夹；填名称或 id = 白名单。

## 6. 扫码登录（不可代做）

```bat
登录抖音.cmd
```

会弹一个**专用 Edge 窗口**（独立配置目录，不碰日常浏览器），用户用抖音 App 扫码一次即可。验证：

```bat
.\.venv\Scripts\python.exe -m processor.sync --status
```

输出 `CDP 就绪` 才算通。二维码必须本人扫，AI 不要试图代登录。

## 7. 首轮同步

```bat
同步.cmd
```

退出码 `0` 且 vault 里出现 `条目\`、`分类\`、`开始这里.md`。退出码 `2` = 需要登录（回 §6）。

## 8. 可选：接大模型（速查提炼）

1. 百炼控制台 <https://bailian.console.aliyun.com/> 右上角「API-KEY」新建，`sk-` 开头，只显示一次；
2. 填 `quicknote`：`enabled: true`、`base_url: https://dashscope.aliyuncs.com/compatible-mode/v1`、`model: qwen3.7-plus`、`retries: 1`；
3. 三个坑：模型名填 `qwen-plus` 不抵扣免费额度、走按量计费；余额为 0 时接口返回 `{"code":"Arrearage"}`，所有模型一律拒绝；免费额度仅华北2（北京）地域、每模型 100 万 token / 90 天；
4. 联调：`提炼.cmd --dry-run` 看队列 → `提炼.cmd --id <aweme_id>` 试一条。长稿（2 万字）单次约 100–110 秒，不是卡死。

## 9. 可选：接飞书知识库

1. <https://open.feishu.cn/app> 创建企业自建应用，拿 `app_id` / `app_secret`；
2. 开权限 `wiki:wiki`、`drive:drive`、`docx:document`，一键链接（把 `<app_id>` 换成自己的）：
   `https://open.feishu.cn/app/<app_id>/auth?q=wiki:wiki,drive:drive,docx:document&op_from=openapi&token_type=tenant`
3. **必须去「版本管理与发布」建版本并发布**，否则接口一直报无权限；
4. 打开目标知识库页面 → 右上「⋯」→「更多」→「**添加文档应用**」→ 选该应用。走「成员设置」加人不生效；
5. 填 `feishu`：`app_id`、`app_secret`、`space_id`（知识库地址栏数字）、`parent_wiki_token`（父节点，可留空挂一级）、`include_covers: false`、`apply_when_no_perm: false`；
6. 联调：`推送速查.cmd --dry-run` → `推送速查.cmd --id <aweme_id> --force`。`--force` 对已推送条目是**原地覆盖正文**，wiki 链接不变，不会多出一篇。

## 10. 可选：无人值守

```bat
powershell -NoProfile -ExecutionPolicy Bypass -File "skills\douyin-collection-obsidian\scripts\register-chain-task.ps1" -ToolRoot "<仓库路径>" -At 04:00
```

装完先手动双击 `定时全链路.cmd` 跑一遍，再查：

```powershell
Get-ScheduledTaskInfo -TaskName 'DouyinToObsidian-Chain'
```

`LastTaskResult=0` 即上次跑通。链路语义：同步失败立即中止，深挖/提炼/推送失败只记 `FAIL` 不阻塞。

## 11. 不可代做的清单

遇到下列动作，直接请用户本人操作，不要反复尝试合成点击：

- 抖音扫码登录（§6）
- 云控制台建 API Key、创建飞书应用、发布版本、给知识库授权（§8 §9）
- 安装 Python 等需要 UAC 提权的系统软件（§2）
- 网页上某些卡片式按钮对合成点击无响应时，一律转人工

## 12. 报错对照表

| 现象 | 原因 / 解法 |
| --- | --- |
| `winget ... 0x80072efd` | 连不上 GitHub，改用 npmmirror 镜像或 GitHub Desktop |
| 旧窗口 `git --version` 报找不到 | PATH 未刷新，重开终端 |
| GitHub 拒收某文件 | 单文件超 100 MB；二进制走 Releases |
| 飞书 `131006` | 应用未发布 / 未授权知识库 |
| 飞书 `99992402` | 参数位置错，如删除文件 `type=docx` 必须走查询串 |
| 飞书 `3380004` | 当前用户对文档无权限（应用导入的文档常如此） |
| 飞书 `1063002` | 应用对文档无 manage 权，无法自行加协作者 |
| `{"code":"Arrearage"}` | 百炼欠费停服，结清后再调 |
| `第 N 节表格缺表头` | 模型偶发漏字段；归一化会先按列数补占位表头，`quicknote.retries: 1` 再自动重试一次，仍不过才记失败 |
| `.cmd` 报「无法识别 xxx」 | 脚本被存成 LF / 无 BOM；Windows 批处理要 CRLF，PowerShell 脚本要 UTF-8 BOM |
| 提示相关权限申请被拒 | `apply_when_no_perm` 保持 `false`，改手动授权 |
