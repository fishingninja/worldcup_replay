#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
增量抓取回放链接（v3 — 高效版）
- 只处理最近2天已完成（kickoff+2.5h < now）且缺链接的比赛
- 复用一个浏览器，每场搜索独立超时保护
"""

import asyncio
import json
import re
import sys
import urllib.parse
from datetime import datetime, timedelta, timezone
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
PER_MATCH_TIMEOUT = 30  # 每场比赛最多 30 秒
TZ = timezone(timedelta(hours=8))  # 北京时间
LOOKBACK_DAYS = 2


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


def is_match_done(m: dict) -> bool:
    """比赛是否已完成（kickoff + 2.5h < now）"""
    kickoff_str = m.get("kickoff", "")
    if not kickoff_str:
        return False
    try:
        kickoff = datetime.fromisoformat(kickoff_str)
        now = datetime.now(TZ)
        return kickoff + timedelta(hours=2.5) < now
    except ValueError:
        return False


def is_recent(m: dict) -> bool:
    """比赛是否在最近 LOOKBACK_DAYS 天内"""
    kickoff_str = m.get("kickoff", "")
    if not kickoff_str:
        return False
    try:
        kickoff = datetime.fromisoformat(kickoff_str)
        now = datetime.now(TZ)
        cutoff = now.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=LOOKBACK_DAYS)
        return kickoff >= cutoff
    except ValueError:
        return False


def needs_scraping(m: dict) -> bool:
    """判断是否需要抓取：已完成 + 在时间窗口内 + 缺链接"""
    if m.get("verified") is True:
        return False
    existing = m.get("cctvUrl", "")
    if existing and "VIDE" in existing:
        return False
    if not is_match_done(m):
        return False
    if not is_recent(m):
        return False
    return True


async def search_one_match(page, team_a: str, team_b: str) -> str | None:
    a = strip_emoji(team_a)
    b = strip_emoji(team_b)

    keywords = [
        f"{a} {b} 世界杯 全场回放",
        f"{a}vs{b}世界杯",
        f"{a} {b} 世界杯 完整版",
    ]

    best_url = None
    best_dur = 0
    fallback_url = None  # 降级：无时长信息但标题匹配

    for kw in keywords:
        encoded = urllib.parse.quote(kw)
        url = f"{CCTV_SEARCH}?qtext={encoded}&channel=体育,CCTV-5+体育赛事频道,CCTV-5体育频道"
        print(f"    [搜索] {kw}")
        try:
            await page.goto(url, timeout=15000, wait_until="domcontentloaded")
            await page.wait_for_timeout(2500)
        except asyncio.TimeoutError:
            print(f"    [超时] 跳过此关键词")
            continue
        except Exception as e:
            print(f"    [错误] {e}")
            continue

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
            print(f"    [JS错误] {e}")
            continue

        print(f"    [结果] {len(items)} 个 VIDE 链接")

        for item in items:
            href = item.get("href", "")
            if "/2026/" not in href or "VIDE" not in href:
                continue
            dur = parse_duration(item.get("title", "") + " " + item.get("context", ""))
            if dur >= DURATION_MIN and dur > best_dur:
                best_dur = dur
                best_url = href
                print(f"    [匹配] {dur}分钟")
            elif dur > 0 and dur < DURATION_MIN:
                print(f"    [跳过] {dur}分钟(太短)")
            else:
                # dur == 0: 搜索摘要未包含时长 → 降级用标题匹配
                if fallback_url is None:
                    title_text = item.get("title", "")
                    # 必须同时包含两队名称（已去 emoji）
                    if a in title_text and b in title_text:
                        fallback_url = href
                        print(f"    [降级匹配] 标题含 {a} + {b}（无时长信息）")

    if best_url:
        print(f"  [命中] {best_dur}分钟")
        return best_url
    elif fallback_url:
        print(f"  [降级命中] 无时长信息，通过标题匹配")
        return fallback_url
    else:
        print(f"  [未找到]")
        return None


async def update_matches(matches: list) -> int:
    todo = [m for m in matches if needs_scraping(m)]

    if not todo:
        print("无需抓取（近2天已完成比赛链接均已齐全）。")
        return 0

    print(f"共 {len(matches)} 场比赛，需增量抓取 {len(todo)} 场\n")

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
            extra_http_headers={"Accept-Language": "zh-CN,zh;q=0.9"},
        )
        page = await context.new_page()
        # 不加载图片/字体/媒体
        await page.route("**/*.{png,jpg,jpeg,gif,svg,woff,woff2,ttf,eot,mp4,mp3,webm,ogg}", lambda route: route.abort())

        updated = 0
        for i, m in enumerate(todo):
            team_a = m.get("teamA", "")
            team_b = m.get("teamB", "")
            date_str = m.get("date", "")

            print(f"[{i+1}/{len(todo)}] {date_str} {strip_emoji(team_a)} vs {strip_emoji(team_b)}")

            try:
                new_url = await asyncio.wait_for(
                    search_one_match(page, team_a, team_b),
                    timeout=PER_MATCH_TIMEOUT,
                )
            except asyncio.TimeoutError:
                print(f"  [超时] >{PER_MATCH_TIMEOUT}s，跳过")
                continue
            except Exception as e:
                print(f"  [异常] {e}")
                continue

            if new_url:
                m["cctvUrl"] = new_url
                m["verified"] = False
                updated += 1
                print(f"  [已更新] ✓")
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

    # 统计
    total = len(matches)
    done = sum(1 for m in matches if is_match_done(m))
    recent = sum(1 for m in matches if is_recent(m))
    need = sum(1 for m in matches if needs_scraping(m))

    print(f"{total} 场比赛 | {done} 场已完成 | 近{LOOKBACK_DAYS}天 {recent} 场 | 需抓取 {need} 场")
    print("=" * 50)

    updated = asyncio.run(update_matches(matches))

    if updated:
        with open(mp, "w", encoding="utf-8") as f:
            json.dump(matches, f, ensure_ascii=False, indent=2)
        sync_to_html(matches)
        print(f"\n[完成] 更新了 {updated} 场比赛的回放链接")
    else:
        print("\n[完成] 无新链接")


def sync_to_html(matches: list) -> None:
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
    new_html = re.sub(pattern, r"\1\n" + json_str + r"\n\3", html, flags=re.DOTALL)
    with open(hp, "w", encoding="utf-8") as f:
        f.write(new_html)
    print("[同步] index.html 已更新")


if __name__ == "__main__":
    main()
