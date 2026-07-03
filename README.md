# GitHub New Repo Radar

抓取 GitHub 指定日期新建仓库，按 star 降序筛选，并生成“项目解析”报告。

它不是简单的 star 榜，而是会结合仓库元数据和 README 做启发式判断：

- 项目定位：这个项目到底是什么，不只是标题翻译。
- 核心机制：README 是否说明架构、安装、运行链路。
- 证据强弱：语言、体积、fork、license、README 深度。
- 风险判断：外链二进制、密码压缩包、管理员运行、反检测、交易/钱包/API key 等。
- 下一步：适合研究、需要核验，还是不建议运行。

## 快速使用

在本目录运行：

```bash
python3 -m github_new_repo_radar run \
  --date today \
  --timezone Asia/Shanghai \
  --limit 12 \
  --format all \
  --output-dir ./reports
```

输出文件：

- `reports/github-new-repos-YYYY-MM-DD.html`：可视化项目解析看板
- `reports/github-new-repos-YYYY-MM-DD.json`：给 LLM/agent 调用的结构化数据
- `reports/github-new-repos-YYYY-MM-DD.md`：适合发给人的 Markdown 报告

桌面本机也可以直接双击：

```bash
./run_today.command
```

## 服务器/LLM 调用方式

机器可读 JSON：

```bash
python3 -m github_new_repo_radar run \
  --date today \
  --timezone Asia/Shanghai \
  --limit 20 \
  --format json \
  --stdout \
  --output-dir /tmp/github-radar \
  2>/tmp/github-radar-summary.log
```

如果要让 LLM 只消费 JSON，把 stdout 传给模型即可。stderr 里只会放本次输出文件路径摘要。

指定日期：

```bash
python3 -m github_new_repo_radar run \
  --date 2026-07-03 \
  --timezone Asia/Shanghai \
  --limit 15 \
  --format all \
  --output-dir ./reports
```

安装成系统命令：

```bash
python3 -m pip install -e .
github-new-repo-radar run --date today --timezone Asia/Shanghai --format json --stdout
```

## GitHub Token

不配置 token 也能用，但 GitHub 匿名 API 有较低限额。服务器长期调用建议设置：

```bash
export GITHUB_TOKEN="ghp_xxx"
```

也可以用参数传入：

```bash
github-new-repo-radar run --github-token "$GITHUB_TOKEN"
```

## 重要口径

本工具严格用仓库 `created_at` 过滤目标日期。

例如 `--date 2026-07-03 --timezone Asia/Shanghai` 会转换为：

```text
2026-07-02T16:00:00Z <= created_at < 2026-07-03T16:00:00Z
```

所以旧项目今天更新、今天 push、今天涨 star，都不会混进来。

## 常用参数

```text
--date              YYYY-MM-DD 或 today
--timezone          IANA 时区名，例如 Asia/Shanghai、America/Los_Angeles
--limit             输出多少个项目
--min-stars         过滤低于指定 star 的仓库
--format            html、json、md、all
--output-dir        输出目录
--readme-limit      读取前多少个项目的 README
--no-readme         跳过 README，只用元数据快速生成
--stdout            把指定格式同时打印到 stdout
```

## 风险评分说明

评分是启发式，不是安全审计结论。命中以下模式会显著提高风险：

- 非 GitHub 下载链接
- `setup.exe`、`trainer.exe`、`tool.exe`
- 压缩包密码
- 管理员运行
- 反检测、注入、内存修改
- 批量私信、用户抓取
- 钱包、交易、API key、OAuth、支付

建议把它当作“第一轮筛查器”：它帮你挑出值得读的项目，也帮你快速避开不该直接运行的项目。
