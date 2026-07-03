from __future__ import annotations

import argparse
import json
import math
import os
import re
import sqlite3
import sys
import textwrap
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from html import escape
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo


API_ROOT = "https://api.github.com"
DEFAULT_TIMEZONE = os.environ.get("TZ") if "/" in os.environ.get("TZ", "") else "Asia/Shanghai"
USER_AGENT = "github-new-repo-radar/0.1"


RISK_PATTERNS: list[tuple[str, str, int]] = [
    (r"trainer-archive\.zip|trainer\.exe|setup\.exe|tool\.exe", "外链 Windows 可执行文件", 34),
    (r"password\s*[:：]\s*`?trainer2026|trainer2026", "压缩包密码", 24),
    (r"run as administrator|administrator|管理员", "要求管理员运行", 20),
    (r"undetectab|anti[- ]?detect|avoid detection|bypass", "反检测/规避话术", 28),
    (r"memory manipulation|dll injection|process injection|code injection|memory injection|injected into game|god mode|one-hit|unlimited .*resources", "注入或游戏修改器话术", 24),
    (r"mass dm|user scraper|export.*members|bulk direct messages", "批量私信/抓取用户", 22),
    (r"api key|oauth|access token|api token|auth token|secret token|wallet|payment|p2p transfer|apy|trading|order confirmation", "凭证/资金/交易相关", 16),
    (r"skydock\.netlify\.app|bit\.ly|tinyurl|download now", "非 GitHub 下载入口", 28),
]

DEPTH_HINTS = [
    "architecture",
    "架构",
    "roadmap",
    "quick start",
    "快速开始",
    "usage",
    "how it works",
    "file structure",
    "安全",
    "guardrail",
    "测试",
    "script",
]


@dataclass
class RepoAnalysis:
    rank: int
    repo: str
    name: str
    owner: str
    url: str
    avatar: str
    stars: int
    forks: int
    issues: int
    watchers: int
    language: str
    license: str
    size: int
    created_at: str
    created_local: str
    updated_at: str
    pushed_at: str
    topics: list[str]
    category: str
    lane: str
    decision: str
    risk_label: str
    risk_hits: list[str]
    summary: str
    position: str
    mechanism: str
    evidence: str
    risk: str
    action: str
    scores: dict[str, int]
    matrix: dict[str, int]
    html_url: str


class GitHubClient:
    def __init__(self, token: str | None = None) -> None:
        self.token = token or os.environ.get("GITHUB_TOKEN")

    def _headers(self, accept: str) -> dict[str, str]:
        headers = {
            "Accept": accept,
            "User-Agent": USER_AGENT,
            "X-GitHub-Api-Version": "2022-11-28",
        }
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        return headers

    def request_json(self, url: str) -> dict[str, Any]:
        request = urllib.request.Request(url, headers=self._headers("application/vnd.github+json"))
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"GitHub API error {exc.code}: {body[:500]}") from exc

    def request_text(self, url: str) -> str:
        request = urllib.request.Request(url, headers=self._headers("application/vnd.github.raw"))
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                return response.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                return ""
            body = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"GitHub README error {exc.code}: {body[:500]}") from exc

    def request_plain_text(self, url: str) -> str:
        request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                return response.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as exc:
            if exc.code in {403, 404}:
                return ""
            body = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"raw text fetch error {exc.code}: {body[:300]}") from exc


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="github-new-repo-radar",
        description="Analyze GitHub repositories created on a target day and render reports.",
    )
    subparsers = parser.add_subparsers(dest="command")

    run = subparsers.add_parser("run", help="fetch, analyze, and render reports")
    run.add_argument("--date", default="today", help="local date, YYYY-MM-DD or today")
    run.add_argument("--timezone", default=DEFAULT_TIMEZONE, help="IANA timezone, default: Asia/Shanghai")
    run.add_argument("--limit", type=int, default=12, help="number of repositories to analyze")
    run.add_argument("--search-page-size", type=int, default=100, help="GitHub search page size per UTC day, max 100")
    run.add_argument("--readme-limit", type=int, default=20, help="how many top repositories should have README fetched")
    run.add_argument("--min-stars", type=int, default=0, help="drop repositories below this star count")
    run.add_argument("--format", choices=["html", "json", "md", "all"], default="all", help="output format")
    run.add_argument("--output-dir", default="./radar-output", help="directory for generated files")
    run.add_argument("--output-name", default="", help="file stem, default github-new-repos-{date}")
    run.add_argument("--github-token", default="", help="optional token; defaults to GITHUB_TOKEN env")
    run.add_argument("--db", default="", help="SQLite history path; default: <output-dir>/history.sqlite")
    run.add_argument("--no-db", action="store_true", help="do not write the SQLite history database")
    run.add_argument("--no-index", action="store_true", help="do not generate the reports index.html")
    run.add_argument("--summary-file", default="", help="write a concise Markdown summary to this path")
    run.add_argument("--no-readme", action="store_true", help="skip README fetching")
    run.add_argument("--stdout", action="store_true", help="print the selected format to stdout after writing files")

    history = subparsers.add_parser("history", help="show stored run history from SQLite")
    history.add_argument("--db", default="./radar-output/history.sqlite", help="SQLite history path")
    history.add_argument("--limit", type=int, default=14, help="number of runs to show")
    history.add_argument("--format", choices=["table", "json"], default="table", help="output format")

    args = parser.parse_args(argv)
    if args.command is None:
        args.command = "run"
    return args


def parse_target_date(raw: str, tz: ZoneInfo) -> date:
    if raw == "today":
        return datetime.now(tz).date()
    try:
        return date.fromisoformat(raw)
    except ValueError as exc:
        raise SystemExit(f"Invalid --date value: {raw}. Use YYYY-MM-DD or today.") from exc


def utc_window_for_local_day(target: date, tz: ZoneInfo) -> tuple[datetime, datetime]:
    start = datetime.combine(target, time.min, tzinfo=tz)
    end = start + timedelta(days=1)
    return start.astimezone(timezone.utc), end.astimezone(timezone.utc)


def utc_dates_between(start: datetime, end: datetime) -> list[date]:
    days: list[date] = []
    current = start.date()
    last = (end - timedelta(microseconds=1)).date()
    while current <= last:
        days.append(current)
        current += timedelta(days=1)
    return days


def parse_github_time(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def search_repositories(client: GitHubClient, days: list[date], page_size: int) -> list[dict[str, Any]]:
    page_size = max(1, min(100, page_size))
    repos: dict[str, dict[str, Any]] = {}
    for day in days:
        query = urllib.parse.urlencode(
            {
                "q": f"created:{day.isoformat()}",
                "sort": "stars",
                "order": "desc",
                "per_page": str(page_size),
            }
        )
        payload = client.request_json(f"{API_ROOT}/search/repositories?{query}")
        for item in payload.get("items", []):
            repos[item["full_name"]] = item
    return list(repos.values())


def raw_readme_urls(repo: dict[str, Any]) -> list[str]:
    full_name = repo["full_name"]
    owner, name = full_name.split("/", 1)
    branch = repo.get("default_branch") or "main"
    owner_q = urllib.parse.quote(owner, safe="")
    name_q = urllib.parse.quote(name, safe="")
    branch_q = urllib.parse.quote(branch, safe="/")
    candidates = ["README.md", "README.MD", "Readme.md", "readme.md", "README.rst", "README.txt"]
    return [f"https://raw.githubusercontent.com/{owner_q}/{name_q}/{branch_q}/{filename}" for filename in candidates]


def fetch_readmes(client: GitHubClient, repos: list[dict[str, Any]], limit: int) -> dict[str, str]:
    readmes: dict[str, str] = {}
    api_available = True
    for item in repos[: max(0, limit)]:
        full_name = item["full_name"]
        text = ""
        if api_available:
            try:
                text = client.request_text(f"{API_ROOT}/repos/{full_name}/readme")
            except RuntimeError as exc:
                api_available = False
                print(f"warning: GitHub README API failed for {full_name}, falling back to raw README URLs: {exc}", file=sys.stderr)
        if not text:
            for url in raw_readme_urls(item):
                text = client.request_plain_text(url)
                if text:
                    break
        readmes[full_name] = text
    return readmes


def plain(value: Any, fallback: str = "") -> str:
    if value is None:
        return fallback
    text = str(value).strip()
    return text if text else fallback


def compact(text: str, limit: int = 180) -> str:
    text = re.sub(r"\s+", " ", text or "").strip()
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def first_sentence(text: str, fallback: str) -> str:
    text = compact(text, 260)
    if not text:
        return fallback
    chunks = re.split(r"(?<=[。！？.!?])\s+", text, maxsplit=1)
    return compact(chunks[0], 170)


def detect_risk(text: str) -> tuple[list[str], int]:
    lower = text.lower()
    hits: list[str] = []
    score = 8
    for pattern, label, weight in RISK_PATTERNS:
        if re.search(pattern, lower, flags=re.I):
            hits.append(label)
            score += weight
    return sorted(set(hits)), max(0, min(100, score))


def detect_category(repo: dict[str, Any], readme: str, risk_hits: list[str]) -> tuple[str, str]:
    name = repo.get("full_name", "")
    desc = repo.get("description") or ""
    topics = " ".join(repo.get("topics") or [])
    text = f"{name} {desc} {topics} {readme[:4000]}".lower()

    high_risk = any(hit in risk_hits for hit in ["外链 Windows 可执行文件", "非 GitHub 下载入口", "压缩包密码"])
    name_desc = f"{name} {desc}".lower()
    if high_risk and re.search(r"god mode|unlimited|one-hit|fallout|spider-man|nier|devil may cry|horizon", name_desc):
        return "游戏修改器", "高风险下载模板"
    if high_risk:
        return "高风险下载", "高风险下载模板"
    if re.search(r"chrome|browser extension|extension|x\.com", text):
        return "浏览器扩展", "产品原型"
    promptish = re.search(r"\bprompt\b|final task: build a complete skill library|\.claude/skills", f"{name_desc} {readme[:1400].lower()}")
    if promptish and not repo.get("language"):
        return "Agent Prompt", "AI 工具链与 Agent"
    if re.search(r"invest|stock|option|crypto|cryptocurrency|trading|finance|wallet|payment", text):
        if re.search(r"agent|mcp|robinhood|script|python|tool|automation", text) and repo.get("language"):
            return "金融 Agent", "AI 工具链与 Agent"
        if repo.get("language"):
            return "金融工具", "金融与知识"
        return "知识库", "知识与内容资产"
    if re.search(r"agent|llm|claude|openai|mcp|skill|deepseek|qwen|anthropic|ai ", text):
        if re.search(r"trading|stocks?|etf|robinhood|portfolio", text):
            return "金融 Agent", "AI 工具链与 Agent"
        return "AI 工具链", "AI 工具链与 Agent"
    if re.search(r"youtube|video|creator|shorts|caption", text):
        return "创作者工具", "产品原型"
    if re.search(r"telegram|bot|webhook", text):
        return "Telegram 自动化", "产品原型"
    if re.search(r"diff|compare|folder|file|developer|code", text):
        return "开发效率工具", "产品原型"
    if not repo.get("language") and (repo.get("size") or 0) <= 6:
        return "轻量模板", "需核验项目"
    return "通用项目", "需核验项目"


def score_depth(readme: str) -> int:
    if not readme:
        return 18
    headings = len(re.findall(r"^#{1,4}\s+", readme, flags=re.M))
    tables = readme.count("|")
    fences = readme.count("```")
    hint_count = sum(1 for hint in DEPTH_HINTS if hint in readme.lower())
    score = 22 + min(28, len(readme) // 260) + min(22, headings * 4) + min(12, fences * 3) + min(8, tables // 10) + min(16, hint_count * 3)
    return max(0, min(100, score))


def score_credibility(repo: dict[str, Any], readme: str, risk_score: int, category: str) -> int:
    stars = int(repo.get("stargazers_count") or 0)
    forks = int(repo.get("forks_count") or 0)
    size = int(repo.get("size") or 0)
    score = 38
    score += min(18, int(math.log2(stars + 1) * 3))
    score += min(12, forks * 2)
    score += 8 if repo.get("language") else -4
    score += 5 if repo.get("license") else -3
    score += 8 if size >= 20 else -8 if size <= 5 else 0
    score += min(12, len(readme) // 700)
    if category in {"游戏修改器", "高风险下载"}:
        score -= 28
    score -= max(0, risk_score - 45) // 2
    return max(0, min(100, score))


def score_utility(category: str, risk_score: int, depth: int) -> int:
    base_by_category = {
        "AI 工具链": 78,
        "Agent Prompt": 66,
        "金融 Agent": 68,
        "知识库": 72,
        "浏览器扩展": 58,
        "创作者工具": 54,
        "开发效率工具": 56,
        "Telegram 自动化": 48,
        "金融工具": 60,
        "高风险下载": 30,
        "游戏修改器": 24,
        "轻量模板": 34,
        "通用项目": 44,
    }
    score = base_by_category.get(category, 44) + (depth - 50) // 6
    if risk_score >= 80:
        score -= 12
    return max(0, min(100, score))


def score_novelty(category: str, text: str) -> int:
    lower = text.lower()
    score = {
        "AI 工具链": 70,
        "Agent Prompt": 66,
        "金融 Agent": 58,
        "知识库": 44,
        "浏览器扩展": 58,
        "创作者工具": 42,
        "开发效率工具": 38,
        "Telegram 自动化": 34,
        "高风险下载": 18,
        "游戏修改器": 12,
    }.get(category, 42)
    for token in ["mcp", "agent", "local", "sandbox", "science", "model-agnostic", "zero dependency"]:
        if token in lower:
            score += 4
    return max(0, min(100, score))


def choose_decision(category: str, credibility: int, risk_score: int, risk_hits: list[str]) -> str:
    dangerous_download = any(hit in risk_hits for hit in ["外链 Windows 可执行文件", "非 GitHub 下载入口", "压缩包密码"])
    if dangerous_download or category in {"游戏修改器", "高风险下载"}:
        return "avoid"
    if category in {"金融工具", "浏览器扩展"} and "凭证/资金/交易相关" in risk_hits:
        return "verify"
    if category in {"金融工具", "浏览器扩展"} and risk_score >= 40:
        return "verify"
    if credibility >= 56 and risk_score < 76:
        return "study"
    return "verify"


def risk_label(category: str, risk_score: int, text: str) -> str:
    if category in {"游戏修改器", "高风险下载"} or risk_score >= 82:
        return "高风险"
    if re.search(r"wallet|payment|p2p transfer|apy|order confirmation|\btrading\b", text, flags=re.I):
        return "金融风险"
    if re.search(r"\bstocks?\b|\betf\b|\binvest(ing|ment|or|ors)?\b", text, flags=re.I):
        return "金融风险" if "agent" in text.lower() or "tool" in text.lower() else "内容核验"
    if re.search(r"oauth|api key|proxy|token|browser extension", text, flags=re.I):
        return "中风险"
    if risk_score >= 45:
        return "需验证"
    return "低运行风险"


def conclusion_for(decision: str) -> str:
    if decision == "avoid":
        return "热度不等于可信，优先作为异常样本处理。"
    if decision == "verify":
        return "值得读，但先验证功能边界、安全边界和源码完整度。"
    return "值得打开仓库继续研究。"


def action_for(decision: str, category: str, risk_hits: list[str]) -> str:
    if decision == "avoid":
        return "不建议下载或运行；只阅读仓库文本和元数据，尤其不要执行外链二进制文件。"
    if category in {"金融 Agent", "金融工具"}:
        return "适合学习架构和脚本分工；不要接真实账户或真实资金，先用样例数据复现。"
    if category == "浏览器扩展":
        return "先读 manifest、权限和网络请求；不要连接真实钱包、支付账户或敏感账号。"
    if risk_hits:
        return "先审计 README、源码和依赖，再在隔离环境里用临时凭证验证。"
    return "可以继续读 README、源码结构、issue 和提交记录，判断是否值得收藏或试用。"


def make_position(category: str, repo: dict[str, Any], description: str) -> str:
    if category == "AI 工具链":
        return "AI/Agent 工作流基础设施，面向已经熟悉模型端点、工具调用或本地代理的用户。"
    if category == "Agent Prompt":
        return "提示词/方法论型项目，价值在流程设计，不是传统可安装软件。"
    if category == "金融 Agent":
        return "个人研究型交易工作台，重点是让 Agent 取数和交互，让确定性脚本计算。"
    if category == "知识库":
        return "内容资产型仓库，核心是知识导航、术语体系和外部入口。"
    if category == "浏览器扩展":
        return "浏览器扩展或前端增强原型，通常需要重点检查权限边界。"
    if category in {"高风险下载", "游戏修改器"}:
        return "README 更像下载页而非开源项目，项目宣称与仓库源码体量需要强核验。"
    if category == "开发效率工具":
        return "面向开发或设计协作的效率工具，需要确认是否真的提供可审计源码。"
    return f"项目自述称：{first_sentence(description, '暂无明确描述。')}"


def make_mechanism(category: str, readme: str, description: str) -> str:
    if not readme:
        return "未读取到 README，只能基于 GitHub 描述、语言、体积、star/fork 等元数据判断。"
    lower = readme.lower()
    if category in {"高风险下载", "游戏修改器"}:
        return "README 出现下载按钮、压缩包密码、管理员运行或反检测话术，核心交付物不在仓库源码内。"
    if "architecture" in lower or "架构" in lower:
        return "README 提供了架构或工作流说明，可从模块边界、命令示例和安全边界继续核验。"
    if "quick start" in lower or "快速开始" in lower or "usage" in lower:
        return "README 有快速开始或使用说明，可按命令路径判断项目是否能本地复现。"
    return "README 主要提供项目介绍，机制层信息有限，需要继续看文件结构与提交记录。"


def make_evidence(repo: dict[str, Any], readme: str, depth: int) -> str:
    language = repo.get("language") or "未识别语言"
    license_info = (repo.get("license") or {}).get("spdx_id") or "未声明"
    return (
        f"{language}，{repo.get('stargazers_count', 0)} stars，"
        f"{repo.get('forks_count', 0)} forks，size {repo.get('size', 0)}KB，"
        f"license {license_info}，README 解析深度 {depth}/100。"
    )


def make_risk_text(category: str, risk_score: int, risk_hits: list[str]) -> str:
    if risk_hits:
        return f"命中风险信号：{'、'.join(risk_hits)}。综合运行风险 {risk_score}/100。"
    if category in {"金融 Agent", "金融工具", "知识库"}:
        return f"未命中明显下载风险，但涉及金融判断或交易信息，综合运行/决策风险 {risk_score}/100。"
    return f"未命中强下载风险，综合运行风险 {risk_score}/100；仍需按源码、依赖、权限继续核验。"


def analyze_repositories(
    repos: list[dict[str, Any]],
    readmes: dict[str, str],
    start_utc: datetime,
    end_utc: datetime,
    tz: ZoneInfo,
    limit: int,
    min_stars: int,
) -> list[RepoAnalysis]:
    filtered = []
    for repo in repos:
        created = parse_github_time(repo["created_at"])
        stars = int(repo.get("stargazers_count") or 0)
        if start_utc <= created < end_utc and stars >= min_stars:
            filtered.append(repo)
    filtered.sort(key=lambda item: (-int(item.get("stargazers_count") or 0), item["full_name"].lower()))
    filtered = filtered[:limit]
    max_stars = max([int(item.get("stargazers_count") or 0) for item in filtered] or [1])

    analyses: list[RepoAnalysis] = []
    for rank, repo in enumerate(filtered, start=1):
        full_name = repo["full_name"]
        readme = readmes.get(full_name, "")
        description = plain(repo.get("description"), "暂无描述")
        topics = repo.get("topics") or []
        full_text = f"{full_name} {description} {' '.join(topics)} {readme[:10000]}"
        hits, risk_score = detect_risk(full_text)
        category, lane = detect_category(repo, readme, hits)
        depth = score_depth(readme)
        credibility = score_credibility(repo, readme, risk_score, category)
        utility = score_utility(category, risk_score, depth)
        novelty = score_novelty(category, full_text)
        heat = int((int(repo.get("stargazers_count") or 0) / max_stars) * 100) if max_stars else 0
        decision = choose_decision(category, credibility, risk_score, hits)
        label = risk_label(category, risk_score, full_text)
        created = parse_github_time(repo["created_at"])
        license_info = (repo.get("license") or {}).get("spdx_id") or "NOASSERTION"
        owner = repo.get("owner") or {}

        analyses.append(
            RepoAnalysis(
                rank=rank,
                repo=full_name,
                name=repo.get("name") or full_name.split("/")[-1],
                owner=owner.get("login") or full_name.split("/")[0],
                url=repo.get("html_url") or f"https://github.com/{full_name}",
                avatar=owner.get("avatar_url") or "",
                stars=int(repo.get("stargazers_count") or 0),
                forks=int(repo.get("forks_count") or 0),
                issues=int(repo.get("open_issues_count") or 0),
                watchers=int(repo.get("watchers_count") or 0),
                language=repo.get("language") or "Unknown",
                license=license_info,
                size=int(repo.get("size") or 0),
                created_at=repo.get("created_at") or "",
                created_local=created.astimezone(tz).strftime("%Y-%m-%d %H:%M %Z"),
                updated_at=repo.get("updated_at") or "",
                pushed_at=repo.get("pushed_at") or "",
                topics=list(topics),
                category=category,
                lane=lane,
                decision=decision,
                risk_label=label,
                risk_hits=hits,
                summary=first_sentence(description, "仓库没有提供明确描述。"),
                position=make_position(category, repo, description),
                mechanism=make_mechanism(category, readme, description),
                evidence=make_evidence(repo, readme, depth),
                risk=make_risk_text(category, risk_score, hits),
                action=action_for(decision, category, hits),
                scores={
                    "热度": heat,
                    "可信度": credibility,
                    "解析深度": depth,
                    "实用潜力": utility,
                    "新颖度": novelty,
                    "运行风险": risk_score,
                },
                matrix={"x": max(8, min(92, 8 + int(heat * 0.84))), "y": max(8, min(92, credibility))},
                html_url=repo.get("html_url") or f"https://github.com/{full_name}",
            )
        )
    return analyses


def report_to_dict(
    analyses: list[RepoAnalysis],
    target: date,
    tz_name: str,
    start_utc: datetime,
    end_utc: datetime,
    raw_count: int,
) -> dict[str, Any]:
    items = [item.__dict__ for item in analyses]
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "query": {
            "local_date": target.isoformat(),
            "timezone": tz_name,
            "utc_window": [start_utc.isoformat(), end_utc.isoformat()],
            "raw_candidates": raw_count,
            "included": len(items),
            "sort": "stars desc",
            "old_repositories_excluded": True,
        },
        "metrics": {
            "repositories": len(items),
            "stars": sum(item["stars"] for item in items),
            "study": sum(1 for item in items if item["decision"] == "study"),
            "verify": sum(1 for item in items if item["decision"] == "verify"),
            "avoid": sum(1 for item in items if item["decision"] == "avoid"),
            "high_risk": sum(1 for item in items if item["risk_label"] == "高风险"),
        },
        "items": items,
    }


def render_markdown(report: dict[str, Any]) -> str:
    query = report["query"]
    lines = [
        f"# GitHub 今日新项目解析看板",
        "",
        f"- 日期：{query['local_date']} ({query['timezone']})",
        f"- UTC 窗口：{query['utc_window'][0]} 至 {query['utc_window'][1]}",
        f"- 口径：只保留当天新建仓库，按 star 降序；旧项目更新不计入。",
        "",
        "| 排名 | 项目 | Star | 类别 | 风险 | 决策 | 解析结论 |",
        "|---:|---|---:|---|---|---|---|",
    ]
    for item in report["items"]:
        repo_link = f"[{item['repo']}]({item['html_url']})"
        lines.append(
            f"| {item['rank']} | {repo_link} | {item['stars']} | {item['category']} | "
            f"{item['risk_label']} | {item['decision']} | {conclusion_for(item['decision'])} |"
        )
    lines.append("")
    for item in report["items"]:
        lines.extend(
            [
                f"## {item['rank']}. {item['repo']}",
                "",
                item["summary"],
                "",
                f"- 项目定位：{item['position']}",
                f"- 核心机制：{item['mechanism']}",
                f"- 证据强弱：{item['evidence']}",
                f"- 风险判断：{item['risk']}",
                f"- 下一步：{item['action']}",
                "",
            ]
        )
    return "\n".join(lines)


def render_html(report: dict[str, Any]) -> str:
    data = json.dumps(report, ensure_ascii=False)
    return HTML_TEMPLATE.replace("__REPORT_JSON__", data)


def default_db_path(output_dir: Path, requested: str) -> Path:
    return Path(requested) if requested else output_dir / "history.sqlite"


def init_history_db(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS runs (
            local_date TEXT NOT NULL,
            timezone TEXT NOT NULL,
            generated_at TEXT NOT NULL,
            utc_start TEXT NOT NULL,
            utc_end TEXT NOT NULL,
            raw_candidates INTEGER NOT NULL,
            included INTEGER NOT NULL,
            stars INTEGER NOT NULL,
            study INTEGER NOT NULL,
            verify INTEGER NOT NULL,
            avoid INTEGER NOT NULL,
            high_risk INTEGER NOT NULL,
            report_json TEXT NOT NULL,
            PRIMARY KEY (local_date, timezone)
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS items (
            local_date TEXT NOT NULL,
            timezone TEXT NOT NULL,
            repo TEXT NOT NULL,
            rank INTEGER NOT NULL,
            stars INTEGER NOT NULL,
            forks INTEGER NOT NULL,
            category TEXT NOT NULL,
            decision TEXT NOT NULL,
            risk_label TEXT NOT NULL,
            summary TEXT NOT NULL,
            action TEXT NOT NULL,
            html_url TEXT NOT NULL,
            item_json TEXT NOT NULL,
            PRIMARY KEY (local_date, timezone, repo)
        )
        """
    )
    connection.execute("CREATE INDEX IF NOT EXISTS idx_runs_generated_at ON runs(generated_at)")
    connection.execute("CREATE INDEX IF NOT EXISTS idx_items_repo ON items(repo)")


def store_report(db_path: Path, report: dict[str, Any]) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    query = report["query"]
    metrics = report["metrics"]
    with sqlite3.connect(db_path) as connection:
        init_history_db(connection)
        connection.execute(
            """
            INSERT OR REPLACE INTO runs (
                local_date, timezone, generated_at, utc_start, utc_end, raw_candidates,
                included, stars, study, verify, avoid, high_risk, report_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                query["local_date"],
                query["timezone"],
                report["generated_at"],
                query["utc_window"][0],
                query["utc_window"][1],
                int(query["raw_candidates"]),
                int(query["included"]),
                int(metrics["stars"]),
                int(metrics["study"]),
                int(metrics["verify"]),
                int(metrics["avoid"]),
                int(metrics["high_risk"]),
                json.dumps(report, ensure_ascii=False),
            ),
        )
        connection.execute(
            "DELETE FROM items WHERE local_date = ? AND timezone = ?",
            (query["local_date"], query["timezone"]),
        )
        connection.executemany(
            """
            INSERT OR REPLACE INTO items (
                local_date, timezone, repo, rank, stars, forks, category, decision,
                risk_label, summary, action, html_url, item_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    query["local_date"],
                    query["timezone"],
                    item["repo"],
                    int(item["rank"]),
                    int(item["stars"]),
                    int(item["forks"]),
                    item["category"],
                    item["decision"],
                    item["risk_label"],
                    item["summary"],
                    item["action"],
                    item["html_url"],
                    json.dumps(item, ensure_ascii=False),
                )
                for item in report["items"]
            ],
        )


def load_history(db_path: Path, limit: int) -> list[dict[str, Any]]:
    if not db_path.exists():
        return []
    with sqlite3.connect(db_path) as connection:
        connection.row_factory = sqlite3.Row
        init_history_db(connection)
        rows = connection.execute(
            """
            SELECT local_date, timezone, generated_at, included, stars, study, verify, avoid, high_risk
            FROM runs
            ORDER BY local_date DESC, generated_at DESC
            LIMIT ?
            """,
            (max(1, limit),),
        ).fetchall()
        return [dict(row) for row in rows]


def report_json_files(output_dir: Path) -> list[Path]:
    return sorted(output_dir.glob("github-new-repos-*.json"), reverse=True)


def reports_for_index(output_dir: Path, db_path: Path | None = None, limit: int = 60) -> list[dict[str, Any]]:
    reports: list[dict[str, Any]] = []
    if db_path and db_path.exists():
        with sqlite3.connect(db_path) as connection:
            connection.row_factory = sqlite3.Row
            init_history_db(connection)
            rows = connection.execute(
                "SELECT report_json FROM runs ORDER BY local_date DESC, generated_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
            for row in rows:
                reports.append(json.loads(row["report_json"]))
    if not reports:
        for path in report_json_files(output_dir)[:limit]:
            try:
                reports.append(json.loads(path.read_text(encoding="utf-8")))
            except (OSError, json.JSONDecodeError):
                continue
    return reports


def render_index_html(reports: list[dict[str, Any]]) -> str:
    generated = datetime.now(timezone.utc).isoformat()
    cards = []
    for report in reports:
        query = report["query"]
        metrics = report["metrics"]
        stem = f"github-new-repos-{query['local_date']}"
        top_items = report["items"][:5]
        top_html = "".join(
            f"<li><a href=\"{escape(item['html_url'])}\">{escape(item['repo'])}</a>"
            f" <span>{item['stars']} stars · {escape(item['decision'])} · {escape(item['risk_label'])}</span></li>"
            for item in top_items
        )
        cards.append(
            f"""
            <article class="card">
              <div class="card-head">
                <h2>{escape(query['local_date'])}</h2>
                <a class="open" href="{stem}.html">打开报告</a>
              </div>
              <div class="metrics">
                <span><b>{metrics['repositories']}</b> repos</span>
                <span><b>{metrics['stars']}</b> stars</span>
                <span><b>{metrics['study']}</b> study</span>
                <span><b>{metrics['avoid']}</b> avoid</span>
              </div>
              <ol>{top_html}</ol>
            </article>
            """
        )
    cards_html = "\n".join(cards) if cards else '<p class="empty">暂无报告。运行 CLI 后会自动生成索引。</p>'
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>GitHub New Repo Radar Reports</title>
  <style>
    :root {{ --bg:#f6f2e9; --surface:#fffaf1; --card:#fff; --ink:#17211f; --muted:#68736f; --line:#d8d0c1; --teal:#0f766e; --red:#b42318; }}
    * {{ box-sizing:border-box; }}
    body {{ margin:0; background:var(--bg); color:var(--ink); font-family:Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC", sans-serif; }}
    main {{ width:min(1180px, calc(100% - 28px)); margin:0 auto; padding:28px 0 48px; }}
    header {{ background:var(--surface); border:1px solid var(--line); border-radius:8px; padding:24px; margin-bottom:16px; }}
    h1 {{ margin:0 0 8px; font-size:clamp(28px, 5vw, 48px); line-height:1.05; }}
    p {{ color:var(--muted); margin:0; }}
    .grid {{ display:grid; grid-template-columns:repeat(auto-fit, minmax(320px, 1fr)); gap:14px; }}
    .card {{ background:var(--surface); border:1px solid var(--line); border-radius:8px; padding:18px; }}
    .card-head {{ display:flex; align-items:center; justify-content:space-between; gap:12px; margin-bottom:12px; }}
    h2 {{ margin:0; font-size:22px; }}
    .open {{ background:var(--ink); color:white; border-radius:8px; padding:8px 11px; text-decoration:none; font-weight:800; font-size:13px; }}
    .metrics {{ display:grid; grid-template-columns:repeat(4, 1fr); gap:8px; margin:12px 0; }}
    .metrics span {{ border:1px solid var(--line); background:var(--card); border-radius:8px; padding:10px; color:var(--muted); font-size:12px; }}
    .metrics b {{ display:block; color:var(--ink); font-size:18px; }}
    ol {{ margin:12px 0 0; padding-left:22px; }}
    li {{ margin:8px 0; }}
    li a {{ color:var(--teal); font-weight:800; text-decoration:none; }}
    li span {{ color:var(--muted); font-size:13px; }}
    .empty {{ background:var(--surface); border:1px solid var(--line); border-radius:8px; padding:18px; }}
    footer {{ margin-top:18px; color:var(--muted); font-size:12px; }}
  </style>
</head>
<body>
  <main>
    <header>
      <h1>GitHub New Repo Radar</h1>
      <p>历史报告索引。只统计目标日期新建仓库，旧项目更新不计入。</p>
    </header>
    <section class="grid">{cards_html}</section>
    <footer>Generated at {escape(generated)}</footer>
  </main>
</body>
</html>
"""


def write_index(output_dir: Path, db_path: Path | None = None) -> Path:
    reports = reports_for_index(output_dir, db_path)
    path = output_dir / "index.html"
    path.write_text(render_index_html(reports), encoding="utf-8")
    return path


def render_daily_summary(report: dict[str, Any], paths: dict[str, str]) -> str:
    query = report["query"]
    metrics = report["metrics"]
    lines = [
        f"# GitHub New Repo Radar Summary - {query['local_date']}",
        "",
        f"- Timezone: {query['timezone']}",
        f"- UTC window: {query['utc_window'][0]} to {query['utc_window'][1]}",
        f"- Repositories: {metrics['repositories']}",
        f"- Total stars: {metrics['stars']}",
        f"- Decisions: study {metrics['study']} / verify {metrics['verify']} / avoid {metrics['avoid']}",
        "",
        "## Top Projects",
    ]
    for item in report["items"][:10]:
        lines.append(
            f"{item['rank']}. [{item['repo']}]({item['html_url']}) - {item['stars']} stars - "
            f"{item['category']} - {item['decision']} - {item['risk_label']}"
        )
        lines.append(f"   - {item['summary']}")
        lines.append(f"   - Next: {item['action']}")
    lines.extend(["", "## Report Paths"])
    for key, value in paths.items():
        lines.append(f"- {key}: `{value}`")
    return "\n".join(lines) + "\n"


def write_outputs(report: dict[str, Any], output_dir: Path, output_name: str, fmt: str) -> dict[str, str]:
    output_dir.mkdir(parents=True, exist_ok=True)
    targets = ["html", "json", "md"] if fmt == "all" else [fmt]
    paths: dict[str, str] = {}
    for target in targets:
        path = output_dir / f"{output_name}.{target}"
        if target == "html":
            path.write_text(render_html(report), encoding="utf-8")
        elif target == "json":
            path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        elif target == "md":
            path.write_text(render_markdown(report), encoding="utf-8")
        paths[target] = str(path)
    return paths


def stdout_payload(report: dict[str, Any], fmt: str) -> str:
    if fmt in {"all", "json"}:
        return json.dumps(report, ensure_ascii=False, indent=2)
    if fmt == "md":
        return render_markdown(report)
    return render_html(report)


def render_history_table(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return "No history rows found.\n"
    headers = ["date", "tz", "repos", "stars", "study", "verify", "avoid", "high_risk"]
    table_rows = [
        [
            row["local_date"],
            row["timezone"],
            str(row["included"]),
            str(row["stars"]),
            str(row["study"]),
            str(row["verify"]),
            str(row["avoid"]),
            str(row["high_risk"]),
        ]
        for row in rows
    ]
    widths = [len(header) for header in headers]
    for row in table_rows:
        for index, cell in enumerate(row):
            widths[index] = max(widths[index], len(cell))
    lines = [
        "  ".join(header.ljust(widths[index]) for index, header in enumerate(headers)),
        "  ".join("-" * width for width in widths),
    ]
    for row in table_rows:
        lines.append("  ".join(cell.ljust(widths[index]) for index, cell in enumerate(row)))
    return "\n".join(lines) + "\n"


def run(args: argparse.Namespace) -> int:
    try:
        tz = ZoneInfo(args.timezone)
    except Exception as exc:
        raise SystemExit(f"Invalid --timezone value: {args.timezone}") from exc

    target = parse_target_date(args.date, tz)
    start_utc, end_utc = utc_window_for_local_day(target, tz)
    client = GitHubClient(args.github_token or None)
    days = utc_dates_between(start_utc, end_utc)
    repos = search_repositories(client, days, args.search_page_size)

    candidate_repos = []
    for repo in repos:
        created = parse_github_time(repo["created_at"])
        if start_utc <= created < end_utc:
            candidate_repos.append(repo)
    candidate_repos.sort(key=lambda item: (-int(item.get("stargazers_count") or 0), item["full_name"].lower()))

    readmes: dict[str, str] = {}
    if not args.no_readme:
        readmes = fetch_readmes(client, candidate_repos, min(args.readme_limit, args.limit))

    analyses = analyze_repositories(
        repos,
        readmes,
        start_utc,
        end_utc,
        tz,
        args.limit,
        args.min_stars,
    )
    output_dir = Path(args.output_dir)
    output_name = args.output_name or f"github-new-repos-{target.isoformat()}"
    report = report_to_dict(analyses, target, args.timezone, start_utc, end_utc, len(repos))
    paths = write_outputs(report, output_dir, output_name, args.format)

    db_path: Path | None = None
    if not args.no_db:
        db_path = default_db_path(output_dir, args.db)
        store_report(db_path, report)
        paths["db"] = str(db_path)

    if not args.no_index:
        index_path = write_index(output_dir, db_path)
        paths["index"] = str(index_path)

    summary_path = Path(args.summary_file) if args.summary_file else output_dir / f"{output_name}.summary.md"
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(render_daily_summary(report, paths), encoding="utf-8")
    latest_summary = output_dir / "latest-summary.md"
    latest_summary.write_text(summary_path.read_text(encoding="utf-8"), encoding="utf-8")
    paths["summary"] = str(summary_path)
    paths["latest_summary"] = str(latest_summary)

    summary = {
        "ok": True,
        "date": target.isoformat(),
        "timezone": args.timezone,
        "repositories": len(analyses),
        "outputs": paths,
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2), file=sys.stderr if args.stdout else sys.stdout)
    if args.stdout:
        print(stdout_payload(report, args.format))
    return 0


def history(args: argparse.Namespace) -> int:
    rows = load_history(Path(args.db), args.limit)
    if args.format == "json":
        print(json.dumps({"ok": True, "db": args.db, "history": rows}, ensure_ascii=False, indent=2))
    else:
        print(render_history_table(rows), end="")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.command == "run":
        return run(args)
    if args.command == "history":
        return history(args)
    raise SystemExit(f"Unknown command: {args.command}")


HTML_TEMPLATE = r"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>GitHub 今日新项目解析看板</title>
  <style>
    :root {
      --bg: #f6f2e9;
      --surface: #fffaf1;
      --card: #ffffff;
      --ink: #17211f;
      --muted: #68736f;
      --line: #d8d0c1;
      --teal: #0f766e;
      --blue: #2459a6;
      --gold: #a16207;
      --red: #b42318;
      --green: #287451;
      --orange: #c05621;
      --shadow: 0 14px 34px rgba(35, 31, 23, 0.09);
      --radius: 8px;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      background: var(--bg);
      color: var(--ink);
      font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC", "Microsoft YaHei", sans-serif;
      line-height: 1.45;
    }
    a { color: inherit; text-decoration: none; }
    button, input { font: inherit; }
    .shell { width: min(1480px, calc(100% - 32px)); margin: 0 auto; padding: 24px 0 48px; }
    .topbar { display: grid; grid-template-columns: minmax(280px, 1.2fr) minmax(320px, 1fr); gap: 18px; margin-bottom: 18px; }
    .panel, .title, .metrics { background: var(--surface); border: 1px solid var(--line); border-radius: var(--radius); box-shadow: var(--shadow); }
    .title { padding: 24px; min-height: 188px; display: flex; flex-direction: column; justify-content: space-between; }
    .eyebrow { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; color: var(--muted); font-size: 13px; }
    .dot { width: 7px; height: 7px; border-radius: 999px; background: var(--teal); display: inline-block; }
    h1 { margin: 14px 0 10px; font-size: clamp(28px, 4.6vw, 52px); line-height: 1.03; letter-spacing: 0; }
    .lead { margin: 0; color: #3d4945; max-width: 820px; }
    .metrics { padding: 16px; display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 12px; }
    .metric { min-height: 78px; border: 1px solid var(--line); border-radius: var(--radius); padding: 14px; background: var(--card); }
    .metric b { display: block; font-size: 28px; line-height: 1; margin-bottom: 8px; }
    .metric span { display: block; color: var(--muted); font-size: 13px; }
    .toolbar { display: grid; grid-template-columns: minmax(250px, 1fr) auto; gap: 12px; margin-bottom: 18px; }
    .search { height: 44px; width: 100%; padding: 0 14px; border: 1px solid var(--line); border-radius: var(--radius); background: var(--card); color: var(--ink); outline: none; }
    .filters { display: flex; flex-wrap: wrap; gap: 8px; justify-content: flex-end; }
    .filter { height: 44px; border: 1px solid var(--line); border-radius: var(--radius); background: var(--card); color: var(--ink); padding: 0 13px; cursor: pointer; white-space: nowrap; }
    .filter.active { background: var(--ink); color: #fff; border-color: var(--ink); }
    .grid { display: grid; grid-template-columns: 370px minmax(0, 1fr); gap: 18px; align-items: start; }
    .panel { overflow: hidden; }
    .head { padding: 16px 18px; border-bottom: 1px solid var(--line); display: flex; justify-content: space-between; gap: 12px; align-items: center; }
    .head h2 { margin: 0; font-size: 16px; line-height: 1.2; }
    .note { color: var(--muted); font-size: 12px; white-space: nowrap; }
    .list { max-height: calc(100vh - 214px); overflow: auto; padding: 10px; }
    .project { width: 100%; display: grid; grid-template-columns: 34px 42px minmax(0, 1fr); gap: 10px; align-items: center; border: 1px solid transparent; background: transparent; color: var(--ink); border-radius: var(--radius); padding: 10px 8px; cursor: pointer; text-align: left; }
    .project + .project { margin-top: 4px; }
    .project.active, .project:hover { background: var(--card); border-color: var(--line); }
    .rank { width: 30px; height: 30px; display: grid; place-items: center; border-radius: var(--radius); background: #efe7d8; color: #554a39; font-weight: 800; font-size: 12px; }
    .avatar { width: 42px; height: 42px; border-radius: var(--radius); object-fit: cover; border: 1px solid var(--line); background: #eee6d7; }
    .name { display: block; font-size: 14px; font-weight: 800; margin-bottom: 4px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
    .meta { display: flex; gap: 8px; flex-wrap: wrap; color: var(--muted); font-size: 12px; }
    .detail { display: grid; gap: 18px; }
    .selected { padding: 22px; display: grid; grid-template-columns: minmax(0, 1fr) 260px; gap: 22px; align-items: start; }
    .selected-title { display: flex; gap: 14px; align-items: center; margin-bottom: 14px; }
    .selected-title .avatar { width: 58px; height: 58px; }
    .selected-title h2 { margin: 0 0 4px; font-size: clamp(22px, 3vw, 34px); line-height: 1.05; overflow-wrap: anywhere; }
    .selected-title p { margin: 0; color: var(--muted); overflow-wrap: anywhere; }
    .summary { margin: 0 0 18px; font-size: 17px; color: #2f3b37; max-width: 880px; }
    .analysis { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 14px 18px; }
    .item { border-left: 3px solid var(--line); padding-left: 12px; min-height: 78px; }
    .item b { display: block; font-size: 12px; text-transform: uppercase; color: var(--muted); letter-spacing: 0.08em; margin-bottom: 5px; }
    .item span { display: block; color: #24312d; font-size: 14px; }
    .facts { border: 1px solid var(--line); border-radius: var(--radius); background: var(--card); padding: 14px; }
    .tag { display: inline-flex; align-items: center; min-height: 24px; border-radius: 999px; padding: 2px 9px; font-size: 12px; font-weight: 800; line-height: 1.2; color: var(--ink); background: #efe7d8; margin: 0 6px 10px 0; }
    .row { display: grid; grid-template-columns: 1fr auto; gap: 12px; padding: 9px 0; border-bottom: 1px solid #ece4d6; color: var(--muted); font-size: 13px; }
    .row:last-child { border-bottom: 0; }
    .row strong { color: var(--ink); font-size: 13px; text-align: right; }
    .open { display: flex; align-items: center; justify-content: center; height: 42px; border-radius: var(--radius); background: var(--ink); color: #fff; font-weight: 800; margin-top: 12px; }
    .visuals { display: grid; grid-template-columns: minmax(0, 0.95fr) minmax(380px, 1.05fr); gap: 18px; }
    .visual { padding: 18px; }
    .visual h2 { margin: 0 0 14px; font-size: 16px; }
    .scores { display: grid; gap: 12px; }
    .score { display: grid; grid-template-columns: 92px minmax(0, 1fr) 42px; gap: 10px; align-items: center; font-size: 13px; }
    .track { height: 12px; border-radius: 999px; background: #e9dfcf; overflow: hidden; }
    .fill { display: block; height: 100%; border-radius: inherit; width: var(--w); background: var(--c); }
    .value { color: var(--muted); text-align: right; font-variant-numeric: tabular-nums; }
    .matrix { position: relative; min-height: 320px; border: 1px solid var(--line); border-radius: var(--radius); background: linear-gradient(90deg, transparent calc(50% - 0.5px), rgba(104, 115, 111, 0.35) 50%, transparent calc(50% + 0.5px)), linear-gradient(0deg, transparent calc(50% - 0.5px), rgba(104, 115, 111, 0.35) 50%, transparent calc(50% + 0.5px)), var(--card); overflow: hidden; }
    .axis { position: absolute; color: var(--muted); font-size: 12px; font-weight: 800; pointer-events: none; }
    .top { top: 10px; left: 12px; } .bottom { bottom: 10px; right: 12px; } .left { bottom: 10px; left: 12px; } .right { top: 10px; right: 12px; }
    .point { position: absolute; left: var(--x); bottom: var(--y); transform: translate(-50%, 50%); width: 34px; height: 34px; border-radius: 50%; border: 2px solid var(--card); box-shadow: 0 8px 18px rgba(23, 33, 31, 0.18); background: var(--p); color: #fff; font-size: 12px; font-weight: 900; display: grid; place-items: center; cursor: pointer; }
    .point.active { outline: 3px solid rgba(15, 118, 110, 0.25); transform: translate(-50%, 50%) scale(1.12); }
    .lanes { display: grid; gap: 10px; }
    .lane { display: grid; grid-template-columns: 150px minmax(0, 1fr); gap: 12px; align-items: stretch; }
    .lane-label { border-radius: var(--radius); padding: 10px; min-height: 72px; display: flex; align-items: center; font-weight: 900; font-size: 13px; color: var(--ink); border: 1px solid var(--line); background: var(--card); }
    .nodes { border: 1px solid var(--line); border-radius: var(--radius); min-height: 72px; background: var(--card); padding: 8px; display: flex; flex-wrap: wrap; align-content: center; gap: 8px; }
    .node { min-height: 32px; border-radius: var(--radius); padding: 6px 9px; border: 1px solid var(--line); background: #fbf4e8; color: #2e3835; font-size: 12px; font-weight: 800; cursor: pointer; max-width: 240px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
    .node.active { background: var(--ink); color: #fff; border-color: var(--ink); }
    .patterns { padding: 18px; }
    .pattern-grid { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 12px; }
    .pattern { border: 1px solid var(--line); border-radius: var(--radius); padding: 14px; background: var(--card); min-height: 116px; }
    .pattern b { display: block; font-size: 15px; margin-bottom: 8px; }
    .pattern span { display: block; color: var(--muted); font-size: 13px; }
    .source { margin-top: 18px; color: var(--muted); font-size: 12px; display: flex; flex-wrap: wrap; gap: 12px; justify-content: space-between; }
    @media (max-width: 1120px) { .topbar, .grid, .selected, .visuals { grid-template-columns: 1fr; } .list { max-height: 420px; } }
    @media (max-width: 760px) { .shell { width: min(100% - 20px, 1480px); padding-top: 10px; } .metrics, .analysis, .pattern-grid, .toolbar { grid-template-columns: 1fr; } .filters { justify-content: flex-start; } .selected { padding: 16px; } .lane { grid-template-columns: 1fr; } .score { grid-template-columns: 78px minmax(0, 1fr) 38px; } }
  </style>
</head>
<body>
  <main class="shell">
    <section class="topbar">
      <div class="title">
        <div>
          <div class="eyebrow"><span class="dot"></span><span id="scope"></span></div>
          <h1>GitHub 今日新项目解析看板</h1>
          <p class="lead">只看目标日期新建仓库，旧项目更新不算。排序按 star 降序，但重点是项目定位、证据强弱、风险模式和下一步判断。</p>
        </div>
      </div>
      <div class="metrics" id="metrics"></div>
    </section>
    <section class="toolbar">
      <input id="search" class="search" type="search" placeholder="搜索项目、语言、类型、风险标签" />
      <div class="filters" id="filters"></div>
    </section>
    <section class="grid">
      <aside class="panel">
        <div class="head"><h2>按 star 降序</h2><span class="note" id="count"></span></div>
        <div class="list" id="list"></div>
      </aside>
      <section class="detail">
        <article class="panel selected" id="selected"></article>
        <section class="visuals">
          <article class="panel visual"><h2>项目解析评分</h2><div class="scores" id="scores"></div></article>
          <article class="panel visual">
            <h2>热度可信矩阵</h2>
            <div class="matrix" id="matrix">
              <span class="axis top">可信度高</span><span class="axis bottom">可信度低</span><span class="axis left">热度低</span><span class="axis right">热度高</span>
            </div>
          </article>
        </section>
        <section class="panel visual"><h2>项目类型流向</h2><div class="lanes" id="lanes"></div></section>
        <section class="panel patterns">
          <div class="head" style="padding:0 0 14px;border-bottom:0;"><h2>异常模式解析</h2><span class="note">从 README 与元数据推断</span></div>
          <div class="pattern-grid">
            <div class="pattern"><b>外链可执行文件</b><span>命中 Download、setup.exe、trainer.exe 或非 GitHub 压缩包时，默认把运行风险显著抬高。</span></div>
            <div class="pattern"><b>凭证/资金边界</b><span>涉及 API key、OAuth、钱包、交易或支付的项目，即使源码完整也需要隔离验证。</span></div>
            <div class="pattern"><b>仓库体量不匹配</b><span>仓库只有数 KB 却宣称完整桌面工具、AI 平台或反检测能力时，可信度会被下调。</span></div>
            <div class="pattern"><b>模板化热度</b><span>相同 README 结构、相同下载入口、相近创建时间的项目会被视为高核验样本。</span></div>
          </div>
        </section>
      </section>
    </section>
    <div class="source" id="source"></div>
  </main>
  <script>
    const report = __REPORT_JSON__;
    const projects = report.items;
    const state = { selected: projects[0]?.repo || "", filter: "all", query: "" };
    const colors = { study: "#0f766e", verify: "#a16207", avoid: "#b42318" };
    const filters = [{ id: "all", label: "全部" }, { id: "study", label: "优先研究" }, { id: "verify", label: "需验证" }, { id: "avoid", label: "高风险" }];

    function filtered() {
      const q = state.query.trim().toLowerCase();
      return projects.filter((p) => {
        const f = state.filter === "all" || p.decision === state.filter;
        const text = [p.repo, p.name, p.language, p.category, p.lane, p.risk_label, p.summary, p.position, p.mechanism, p.risk].join(" ").toLowerCase();
        return f && (!q || text.includes(q));
      });
    }
    function pick(repo) { state.selected = repo; render(); }
    function selectedProject() { return projects.find((p) => p.repo === state.selected) || filtered()[0] || projects[0]; }
    function esc(s) { return String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#039;" }[c])); }
    function metric(label, value) { return `<div class="metric"><b>${esc(value)}</b><span>${esc(label)}</span></div>`; }
    function scoreColor(label, value) {
      if (label === "运行风险") return value >= 75 ? "#b42318" : value >= 45 ? "#c05621" : "#287451";
      return value >= 70 ? "#0f766e" : value >= 50 ? "#2459a6" : value >= 30 ? "#a16207" : "#b42318";
    }
    function renderMetrics() {
      const m = report.metrics;
      document.getElementById("metrics").innerHTML = [
        metric("进入解析的新仓库", m.repositories),
        metric("样本累计 star", m.stars),
        metric("建议优先研究", m.study),
        metric("强核验或高风险", m.avoid)
      ].join("");
      document.getElementById("scope").textContent = `${report.query.local_date} ${report.query.timezone} | created_at 严格过滤 | 旧项目更新不算`;
      document.getElementById("source").innerHTML = `<span>UTC 窗口：${esc(report.query.utc_window[0])} 至 ${esc(report.query.utc_window[1])}</span><span>来源：GitHub Search API、README、仓库元数据</span>`;
    }
    function renderFilters() {
      document.getElementById("filters").innerHTML = filters.map((f) => `<button class="filter ${state.filter === f.id ? "active" : ""}" data-filter="${f.id}" type="button">${f.label}</button>`).join("");
      document.querySelectorAll(".filter").forEach((b) => b.addEventListener("click", () => {
        state.filter = b.dataset.filter;
        const list = filtered();
        if (!list.some((p) => p.repo === state.selected) && list[0]) state.selected = list[0].repo;
        render();
      }));
    }
    function renderList() {
      const list = filtered();
      document.getElementById("count").textContent = `${list.length} 个项目`;
      document.getElementById("list").innerHTML = list.map((p) => `
        <button class="project ${p.repo === state.selected ? "active" : ""}" type="button" data-repo="${esc(p.repo)}">
          <span class="rank">${p.rank}</span>
          <img class="avatar" src="${esc(p.avatar)}" alt="" loading="lazy" />
          <span><span class="name">${esc(p.name)}</span><span class="meta"><span>${p.stars} stars</span><span>${esc(p.language)}</span><span>${esc(p.risk_label)}</span></span></span>
        </button>`).join("");
      document.querySelectorAll(".project").forEach((b) => b.addEventListener("click", () => pick(b.dataset.repo)));
    }
    function renderSelected() {
      const p = selectedProject();
      if (!p) return;
      state.selected = p.repo;
      document.getElementById("selected").innerHTML = `
        <div>
          <div class="selected-title"><img class="avatar" src="${esc(p.avatar)}" alt="" /><div><h2>${esc(p.name)}</h2><p>${esc(p.repo)}</p></div></div>
          <p class="summary">${esc(p.summary)}</p>
          <div class="analysis">
            <div class="item"><b>项目定位</b><span>${esc(p.position)}</span></div>
            <div class="item"><b>核心机制</b><span>${esc(p.mechanism)}</span></div>
            <div class="item"><b>证据强弱</b><span>${esc(p.evidence)}</span></div>
            <div class="item"><b>风险判断</b><span>${esc(p.risk)}</span></div>
            <div class="item"><b>下一步</b><span>${esc(p.action)}</span></div>
            <div class="item"><b>解析结论</b><span>${esc(p.decision === "avoid" ? "热度不等于可信，优先作为异常样本处理。" : p.decision === "verify" ? "值得读，但先验证功能边界和安全边界。" : "值得打开仓库继续研究。")}</span></div>
          </div>
        </div>
        <aside class="facts">
          <span class="tag">${esc(p.category)}</span><span class="tag">${esc(p.risk_label)}</span>
          ${[["Star", p.stars], ["Fork", p.forks], ["Issue", p.issues], ["语言", p.language], ["许可", p.license], ["仓库体积", `${p.size} KB`], ["创建时间", p.created_local]].map(([k,v]) => `<div class="row"><span>${esc(k)}</span><strong>${esc(v)}</strong></div>`).join("")}
          <a class="open" href="${esc(p.html_url)}" target="_blank" rel="noreferrer">打开 GitHub</a>
        </aside>`;
    }
    function renderScores() {
      const p = selectedProject();
      document.getElementById("scores").innerHTML = Object.entries(p.scores).map(([label, value]) => `
        <div class="score"><span>${esc(label)}</span><span class="track"><span class="fill" style="--w:${value}%;--c:${scoreColor(label, value)}"></span></span><span class="value">${value}</span></div>`).join("");
    }
    function renderMatrix() {
      const matrix = document.getElementById("matrix");
      matrix.querySelectorAll(".point").forEach((el) => el.remove());
      projects.forEach((p) => {
        const b = document.createElement("button");
        b.type = "button";
        b.className = `point ${p.repo === state.selected ? "active" : ""}`;
        b.style.setProperty("--x", `${p.matrix.x}%`);
        b.style.setProperty("--y", `${p.matrix.y}%`);
        b.style.setProperty("--p", colors[p.decision] || "#2459a6");
        b.textContent = p.rank;
        b.title = `${p.name}: ${p.risk_label}`;
        b.addEventListener("click", () => pick(p.repo));
        matrix.appendChild(b);
      });
    }
    function renderLanes() {
      const lanes = [...new Set(projects.map((p) => p.lane))];
      document.getElementById("lanes").innerHTML = lanes.map((lane) => {
        const nodes = projects.filter((p) => p.lane === lane);
        return `<div class="lane"><div class="lane-label">${esc(lane)}</div><div class="nodes">${nodes.map((p) => `<button class="node ${p.repo === state.selected ? "active" : ""}" type="button" data-repo="${esc(p.repo)}">${p.rank}. ${esc(p.name)}</button>`).join("")}</div></div>`;
      }).join("");
      document.querySelectorAll(".node").forEach((b) => b.addEventListener("click", () => pick(b.dataset.repo)));
    }
    function render() { renderMetrics(); renderFilters(); renderList(); renderSelected(); renderScores(); renderMatrix(); renderLanes(); }
    document.getElementById("search").addEventListener("input", (event) => {
      state.query = event.target.value;
      const list = filtered();
      if (!list.some((p) => p.repo === state.selected) && list[0]) state.selected = list[0].repo;
      render();
      document.getElementById("search").focus();
    });
    render();
  </script>
</body>
</html>
"""
