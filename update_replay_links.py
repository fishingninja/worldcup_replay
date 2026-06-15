#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
使用 Playwright 从央视频搜索页面抓取比赛回放链接（v2）
核心改进：复用浏览器，一次启动处理全部比赛；每场搜索有独立超时保护。
- 搜索："{teamA} {teamB} 世界杯 全场回放"
- 筛选时长 > 90 分钟的视频
- 提取 sports.cctv.com 回放链接
"""

import asyncio
import json
import re
import sys
import urllib.parse
import traceback
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
PER_MATCH_TIMEOUT = 45  # 每场比赛最多 45 秒


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


async def search_one_match(page, team_a: str, team_b: str) -> str | None:
    """
    对一对比赛搜索回放链接。复用外部传入的 page，不自行管理浏览器。
    每场比赛最多 PER_MATCH_TIMEOUT 秒。
    """
    a = strip_emoji(team_a)
    b = strip_emoji(team_b)

    # 多组搜索关键词
    keywords = [
        f"{a} {b} 世界杯 全场回放",
        f"{a}vs{b}世界杯",
        f"{a} {b} 世界杯 完整版",
    ]

    best_url = None
    best_dur = 0

    for kw in keywords:
        encoded = urllib.parse.quote(kw)
        url = f"{CCTV_SEARCH}?qtext={encoded}&channel=体育,CCTV-5+体育赛事频道,CCTV-5体育频道"
        print(f"    [搜索] {kw}")
        try:
            await page.goto(url, timeout=20000, wait_until="domcontentloaded")
            await page.wait_for_timeout(3000)
        except asyncio.TimeoutError:
            print(f"    [超时] 页面加载超时(20s)，跳过此关键词")
            continue
        except Exception as e:
            print(f"    [错误] 加载失败: {e}")
            continue

        # 用 JS 提取所有含 VIDE 的链接及其父容器文本
        try:
            items = await page.evaluate("""
                () => {
                    const links = Array.from(
                        document.querySelectorAll('a[href*="VIDE"]')
                    );
                    return links.map(a => {
                        const parent = a.closest('li') || a.closest('div') || a.parentElement;
                        return {
                            href: a.href,
                            title: a.title || a.innerText.trim().slice(0, 200),
                            context: parent ? parent.innerText.trim().slice(0, 500) : ''
                        };
                    });
                }
            """)
        except Exception as e:
            print(f"    [错误] JS 提取失败: {e}")
            continue

        print(f"    [结果] 找到 {len(items)} 个含 VIDE 链接")

        for item in items:
            href = item.get("href", "")
            if "/2026/" not in href or "VIDE" not in href:
                continue
            full_text = item.get("title", "") + " " + item.get("context", "")
            dur = parse_duration(full_text)
            if dur >= DURATION_MIN and dur > best_dur:
                best_dur = dur
                best_url = href
                print(f"    [匹配] {dur}分钟 -> ...{href[-50:]}")
            elif dur > 0 and dur < DURATION_MIN:
                print(f"    [跳过] {dur}分钟(太短)")

    if best_url:
        print(f"  [命中] {best_dur}分钟")
        return best_url
    else:
        print(f"  [未找到] 所有关键词均无 > {DURATION_MIN}min 的视频")
        return None


async def update_all_matches(matches: list) -> int:
    """
    复用一个浏览器实例处理所有比赛。
    返回更新的场次数。
    """
    updated = 0
    total = len(matches)

    # 统计需要处理的
    todo = []
    for m in matches:
        existing = m.get("cctvUrl", "")
        if m.get("verified") is True and existing:
            continue
        if existing and "VIDE" in existing:
            continue
        todo.append(m)

    if not todo:
        print("所有比赛链接均已齐全，无需处理。")
        return 0

    print(f"共 {total} 场比赛，需要抓取 {len(todo)} 场\n")

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-setuid-sandbox", "--disable-dev-shm-usage"],
        )
        context = await browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            # 不加载图片/字体/媒体，加快速度
            extra_http_headers={"Accept-Language": "zh-CN,zh;q=0.9"},
        )
        page = await context.new_page()

        # 关闭不必要的资源请求以加速
        await page.route("**/*.{png,jpg,jpeg,gif,svg,woff,woff2,ttf,eot}", lambda route: route.abort())
        await page.route("**/*.mp4,**/*.mp3,**/*.webm,**/*.ogg", lambda route: route.abort())

        for i, m in enumerate(todo):
            team_a = m.get("teamA", "")
            team_b = m.get("teamB", "")
            date_str = m.get("date", "")
            existing = m.get("cctvUrl", "")

            print(f"[{i+1}/{len(todo)}] {date_str} {strip_emoji(team_a)} vs {strip_emoji(team_b)}")

            try:
                new_url = await asyncio.wait_for(
                    search_one_match(page, team_a, team_b),
                    timeout=PER_MATCH_TIMEOUT,
                )
            except asyncio.TimeoutError:
                print(f"  [超时] 整场比赛搜索超过 {PER_MATCH_TIMEOUT}s，跳过")
                continue
            except Exception as e:
                print(f"  [异常] {e}")
                continue

            if new_url and new_url != existing:
                m["cctvUrl"] = new_url
                m["verified"] = False
                updated += 1
                print(f"  [已更新] ✓")
            elif new_url == existing:
                print(f"  [相同]")
            else:
                print(f"  [无结果]")

        await browser.close()

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

    updated = asyncio.run(update_all_matches(matches))

    if updated:
        with open(mp, "w", encoding="utf-8") as f:
            json.dump(matches, f, ensure_ascii=False, indent=2)
        sync_to_html(matches)
        print(f"\n[完成] 更新了 {updated} 场比赛的回放链接")
    else:
        print("\n[完成] 无新链接，全部已有链接或暂无回放")


def sync_to_html(matches: list) -> None:
    """把 matches.json 同步到 index.html 的 <script id=match-data> 标签。"""
    hp = Path(__file__).parent / "index.html"
    if not hp.exists():
        return
    with open(hp, "r", encoding="utf-8") as f:
        html = f.read()
    json_str = json.dumps(matches, ensure_ascii=False, indent=2)
    pattern = (
        r'(<script\s+id=["\']match-data["\']\s*type=["\']application/json["\']\s*>)'
        r'(.*?)'
        r'(</script>)'
    )
    replacement = r"\1\n" + json_str + r"\n\3"
    new_html = re.sub(pattern, replacement, html, flags=re.DOTALL)
    with open(hp, "w", encoding="utf-8") as f:
        f.write(new_html)
    print("[同步] index.html 已更新")


if __name__ == "__main__":
    main()
