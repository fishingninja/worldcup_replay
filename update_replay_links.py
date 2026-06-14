#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
使用 Playwright 从央视频搜索页面抓取比赛回放链接
- 搜索："{teamA} {teamB} 世界杯 全场回放"
- 筛选时长 > 90 分钟的视频
- 提取 sports.cctv.com 回放链接
"""

import asyncio
import json
import re
import sys
import urllib.parse
from pathlib import Path

try:
    from playwright.async_api import async_playwright
except ImportError:
    print("ERROR: playwright not installed. pip install playwright")
    sys.exit(1)

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

CCTV_SEARCH = "https://search.cctv.com/search.php"
DURATION_MIN = 90


def strip_emoji(text: str) -> str:
    return re.sub(
        r"[\U0001F000-\U0001FFFF"
        r"\U00002600-\U000027BF"
        r"\U0001F300-\U0001F9FF"
        r"\U0001FA00-\U0001FAFF]+",
        "",
        text,
        flags=re.UNICODE,
    ).strip()


def parse_duration(text: str) -> int:
    if not text:
        return 0
    text = text.replace(" ", "").replace("\u00a0", "")
    m = re.search(r"(\d+)\s*分钟", text)
    if m:
        return int(m.group(1))
    m = re.search(r"(\d+)\s*小时\s*(\d+)\s*分", text)
    if m:
        return int(m.group(1)) * 60 + int(m.group(2))
    m = re.search(r"(\d+):(\d+):(\d+)", text)
    if m:
        return int(m.group(1)) * 60 + int(m.group(2))
    return 0


async def search_replay(team_a: str, team_b: str) -> str | None:
    """返回时长 > 90 分钟的回放链接，或 None。"""
    a = strip_emoji(team_a)
    b = strip_emoji(team_b)
    # 多组搜索关键词，提高命中率
    keywords = [
        f"{a} {b} 世界杯 全场回放",
        f"{a}vs{b}世界杯",
        f"{a} {b} 世界杯 完整版",
    ]

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-setuid-sandbox", "--disable-dev-shm-usage"],
        )
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )
        page = await context.new_page()

        best_url = None
        best_dur = 0

        for kw in keywords:
            encoded = urllib.parse.quote(kw)
            url = f"{CCTV_SEARCH}?qtext={encoded}&channel=体育,CCTV-5+体育赛事频道,CCTV-5体育频道"
            print(f"  [搜索] {kw}")
            try:
                await page.goto(url, timeout=20000)
                await page.wait_for_timeout(4000)
            except Exception as e:
                print(f"  [错误] 加载失败: {e}")
                continue

            # 用 JS 提取所有含 VIDE 的链接及其父容器文本
            items = await page.evaluate("""
                () => {
                    const links = Array.from(
                        document.querySelectorAll('a[href*="VIDE"]')
                    );
                    return links.map(a => {
                        const parent = a.closest('li') || a.closest('div') || a.parentElement;
                        return {
                            href: a.href,
                            title: a.title || a.innerText.trim(),
                            context: parent ? parent.innerText.trim() : ''
                        };
                    });
                }
            """)

            print(f"  [结果] 找到 {len(items)} 个含VIDEO链接")

            for item in items:
                href = item.get("href", "")
                if "/2026/" not in href or "VIDE" not in href:
                    continue
                # 从 title + context 中提取时长
                full_text = item.get("title", "") + " " + item.get("context", "")
                dur = parse_duration(full_text)
                if dur >= DURATION_MIN and dur > best_dur:
                    best_dur = dur
                    best_url = href
                    print(f"  [匹配] {dur}分钟 -> {href[-50:]}")
                elif dur > 0:
                    print(f"  [跳过] {dur}分钟(太短) -> {href[-50:]}")

        await browser.close()

        if best_url:
            print(f"[成功] 最佳: {best_dur}分钟 -> {best_url}")
            return best_url
        else:
            print(f"[失败] 未找到 > {DURATION_MIN} 分钟的视频")
            return None


async def update_matches(matches: list) -> int:
    updated = 0
    for i, m in enumerate(matches):
        team_a = m.get("teamA", "")
        team_b = m.get("teamB", "")
        existing = m.get("cctvUrl", "")

        if m.get("verified") is True and existing:
            print(f"[跳过] {strip_emoji(team_a)} vs {strip_emoji(team_b)} (已验证)")
            continue

        # 只处理缺少链接的
        if existing and not existing.startswith("http"):
            existing = ""
        if existing and "VIDE" in existing:
            print(f"[跳过] {strip_emoji(team_a)} vs {strip_emoji(team_b)} (已有链接)")
            continue

        print(f"\n[{i+1}/{len(matches)}] {strip_emoji(team_a)} vs {strip_emoji(team_b)}")

        new_url = await search_replay(team_a, team_b)
        if new_url and new_url != existing:
            m["cctvUrl"] = new_url
            m["verified"] = False
            updated += 1
            print("  [已更新]")
        elif new_url == existing:
            print("  [相同] 链接已存在")
        else:
            print("  [无结果]")

    return updated


def main():
    mp = Path(__file__).parent / "matches.json"
    if not mp.exists():
        print(f"ERROR: {mp} 不存在")
        sys.exit(1)

    with open(mp, "r", encoding="utf-8") as f:
        matches = json.load(f)

    print(f"加载 {len(matches)} 场比赛，时长阈值 > {DURATION_MIN} 分钟")
    print("=" * 50)

    updated = asyncio.run(update_matches(matches))

    if updated:
        with open(mp, "w", encoding="utf-8") as f:
            json.dump(matches, f, ensure_ascii=False, indent=2)
        # 同步更新 index.html 中的 match-data
        sync_to_html(matches)
        print(f"\n[完成] 更新了 {updated} 场比赛")
    else:
        print("\n[完成] 无需更新")


def sync_to_html(matches: list) -> None:
    """把 matches.json 同步到 index.html 的 <script id=match-data> 标签。"""
    hp = Path(__file__).parent / "index.html"
    if not hp.exists():
        return
    with open(hp, "r", encoding="utf-8") as f:
        html = f.read()
    json_str = json.dumps(matches, ensure_ascii=False, indent=2)
    # 替换 <script id=match-data> 和下一个 </script> 之间的内容
    pattern = r'(<script\s+id=["\']match-data["\']\s*type=["\']application/json["\']\s*>)(.*?)(</script>)'
    replacement = r'\1\n' + json_str + r'\n\3'
    new_html = re.sub(pattern, replacement, html, flags=re.DOTALL)
    with open(hp, "w", encoding="utf-8") as f:
        f.write(new_html)
    print("[同步] index.html 已更新")


if __name__ == "__main__":
    main()
