# AveCove Namer

面向 OpenList、Emby、Infuse 与 SenPlayer 的安全、可审核影视命名工具。

[English](README.md)

> 当前版本：v0.3.0 alpha。请先选择一个小目录试运行，并在执行前逐条审核计划。

## Detective 自动侦测

`avecove-namer detect` 会为一个或多个 OpenList 监控目录中的直属影视目录建立指纹，以后只处理新增或发生变化的作品。已有 `{tmdb=...}` 标识的目录可以直接确认身份；未标识的新目录只有在标题、年份和 TMDB 构成唯一高置信匹配时才会自动执行。模糊匹配和存在冲突的计划只进入待审核清单，不会改名。

附带的低负载服务器脚本默认监控光鸭四个剧集目录，保存状态和回滚记录，只刷新发生变化的 STRM 路径，并通过 CPU、内存、I/O 和进程锁限制每四小时运行一次。启用定时器前先建立一次基线：

```bash
sudo avecove-namer-detective --bootstrap
sudo systemctl enable --now avecove-namer-detective.timer
```

AveCove Namer 是独立实现的云盘影视整理工具。默认剧集规则把稳定识别放在首位：加入剧集首播年份、省略非必要的单集标题，同时保留画质、来源、编码、音轨等有效封装信息。

```text
Modern.Family.2009.S01E01.1080p.BluRay.x265.DTS.mkv
Modern.Family.2009.S01E01.1080p.BluRay.x265.DTS.zh-CN.sup
```

## v0.2 已实现

- 支持本地目录和 OpenList。
- 为剧集和电影补充年份并规范名称。
- 按作品来源选择中英文目录标题，并写入 Emby 可识别的 TMDB ID 标签。
- 提供已确认 TMDB ID 时，默认把选中的作品目录规范成标题、年份和 TMDB ID。
- 递归规范内部媒体文件，但保留 `Season 01`、`Season01`、`S01`、`第一季` 等现有季目录名称和位置。
- 保留画质、片源、视频编码、音轨等发布信息。
- 外挂字幕与视频完整主文件名配对。
- 先生成只读 JSON 计划，可同时导出 CSV 审核表。
- 检查已存在目标与重复目标，存在冲突时禁止执行。
- 执行时必须精确确认根目录和操作数量。
- 检查源文件是否失效，并逐项写入可回滚日志。
- 提供只读 TMDB 搜索，辅助核对片名和年份。
- 无数据库、无后台定时器、无常驻服务，适合小内存服务器。

TMDB 搜索同时支持 API 读取访问令牌和 v3 API Key，两者都必须保存在 `0600` 权限的凭据文件中。

## 快速开始

需要 Python 3.10 或更高版本。

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
avecove-namer --help
```

### 本地小范围试运行

只生成计划，不修改文件：

```bash
avecove-namer plan \
  --backend local \
  --path "/media/Modern Family (2009)" \
  --output work/modern-family.json \
  --csv work/modern-family.csv
```

再次查看计划：

```bash
avecove-namer apply \
  --backend local \
  --plan work/modern-family.json \
  --journal work/modern-family.rollback.jsonl
```

审核通过后，按照预览显示的根目录和操作数量进行精确确认：

```bash
avecove-namer apply \
  --backend local \
  --plan work/modern-family.json \
  --journal work/modern-family.rollback.jsonl \
  --execute \
  --confirm-root "/media/Modern Family (2009)" \
  --confirm-count 24
```

预览和执行回滚：

```bash
avecove-namer rollback --backend local \
  --journal work/modern-family.rollback.jsonl

avecove-namer rollback --backend local \
  --journal work/modern-family.rollback.jsonl --execute
```

### OpenList

先创建受保护的 Token 文件。密码通过交互输入，不会进入命令历史：

```bash
avecove-namer login \
  --openlist-url "https://openlist.example.com" \
  --username admin \
  --token-file "$HOME/.config/avecove-namer/openlist.token"
```

检查连接并生成只读计划：

```bash
avecove-namer check \
  --backend openlist \
  --openlist-url "https://openlist.example.com" \
  --openlist-token-file "$HOME/.config/avecove-namer/openlist.token" \
  --path "/115/TV/Modern Family (2009)"

avecove-namer plan \
  --backend openlist \
  --openlist-url "https://openlist.example.com" \
  --openlist-token-file "$HOME/.config/avecove-namer/openlist.token" \
  --path "/115/TV/Modern Family (2009)" \
  --output work/modern-family.json \
  --csv work/modern-family.csv
```

OpenList 的正式执行也必须经过相同的预览与精确确认。Token 文件权限必须为 `0600`。改名请求默认采用保守的 3 秒冷却时间，以降低包括 115 在内的云盘风控风险。

通过 TMDB 自动选择标题语言。提供已确认的 TMDB ID 后，选中的作品目录会默认加入审核计划：

```bash
avecove-namer plan \
  --backend openlist \
  --openlist-url "https://openlist.example.com" \
  --openlist-token-file "$HOME/.config/avecove-namer/openlist.token" \
  --path "/Baidu/Movies/Kill Bill 1" \
  --tmdb-id 24 \
  --tmdb-token-file "$HOME/.config/avecove-namer/tmdb.token" \
  --media-kind movie \
  --title-style auto \
  --output work/kill-bill.json
```

只有确实需要保留作品目录原名时才使用 `--no-rename-root-folder`。

### 服务器一键整理

部署 `deploy/avecove-namer-run` 后，可用一条命令完成“只读预览 → 人工确认 → 云盘改名 → 对应 STRM 增量同步 → 仅刷新受影响的 Emby 媒体库 → 再次校验”。默认只同步 STRM，保留现有图片和字幕旁挂文件，避免小内存服务器因下载大量旁挂文件而卡住：

```bash
avecove-namer-run "/GuangYa/00剧/01美/Game of Thrones (2011)" 1399 tv
```

最后一个可选参数是标题策略：`auto`（默认）、`english`、`chinese`、`original` 或 `bilingual`。脚本只有在输入大写 `YES` 后才会真正改名，并为每次操作保留独立的回滚日志。

## 默认命名规则

剧集：

```text
{Series.Title}.{Year}.S{season:02}E{episode:02}.{Technical.Metadata}.{ext}
```

字幕：

```text
{完整视频主文件名}.{language}.{字幕扩展名}
```

当程序无法从目录或原文件名确认年份时，会跳过该文件，不会猜测。可以使用 `--title` 和 `--year` 输入已经人工确认的片名与年份。详细规则见[命名规则](docs/naming-rules.zh-CN.md)。

`chs&eng`、`cht&eng` 等双语字幕标记会继续保留。季目录名称不会被修改或移动；规范对象是选中的作品目录及其内部媒体文件。

## 安全机制

生成计划阶段完全只读。存在冲突、目标已存在、源文件失效、操作目录过宽，或确认的目录/数量不匹配时，程序都会拒绝执行。每完成一次重命名就立即记录一条日志，即使任务中途失败，已完成部分仍可回滚。

云盘服务仍可能存在重命名频率限制或临时 API 故障，因此正式使用前必须先拿一个小目录试运行。

## Docker

这是按需执行的命令行程序，并非常驻服务：

```bash
docker build -t avecove-namer:dev .
docker run --rm avecove-namer:dev --help
```

仓库内的 [Compose 示例](compose.example.yml)把容器限制为 0.25 核 CPU 和 192 MB 内存。

## 后续计划

- 增量状态与仅扫描变化内容。
- 成功整理后定向刷新 Emby。
- 多媒体库规则和可复用命名模板。
- 可选的审核界面与定时任务。
- 扩大不同云盘驱动的小范围验证。

## 开发测试

```bash
PYTHONPATH=src python3 -m unittest discover -s tests -v
```

## 开源协议

[MIT](LICENSE)
