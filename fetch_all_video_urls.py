#!/usr/bin/env python3
"""从 XHS 赛程 API 实时获取所有回放比赛，并逐个抓取视频流 URL。
纯云端可运行：不再依赖本地硬盘文件，所有数据通过 Playwright 实时获取。
"""

import asyncio
import json
import sys
from pathlib import Path
from datetime import datetime, timezone, timedelta


# ── 第1步：从小红书赛程页拦截 calendar_info API，提取所有回放 ──

async def fetch_calendar_from_api():
    """访问 worldcup26 页面，拦截 calendar_info API 响应，返回回放列表。"""
    from playwright.async_api import async_playwright

    print('>>> 1/2 访问赛程页，拦截 calendar_info API...', flush=True)

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        ctx = await browser.new_context(
            user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            viewport={'width': 1920, 'height': 1080}
        )
        page = await ctx.new_page()

        calendar_responses = []  # 收集所有 calendar_info 响应

        async def on_response(res):
            url = res.url
            if 'calendar_info' in url:
                try:
                    body = await res.text()
                    calendar_responses.append(body)
                    print(f'  [CAPTURED] calendar_info #{len(calendar_responses)} ({len(body)} bytes)', flush=True)
                except Exception as e:
                    print(f'  [ERR] 读取 calendar_info 响应失败: {e}', flush=True)

        page.on('response', on_response)

        print('  goto https://www.xiaohongshu.com/worldcup26 ...', flush=True)
        await page.goto('https://www.xiaohongshu.com/worldcup26',
                        wait_until='domcontentloaded', timeout=30000)
        await asyncio.sleep(8)  # 等待 API 加载

        page.remove_listener('response', on_response)
        await browser.close()

    if not calendar_responses:
        print('  ❌ 未捕获到 calendar_info API 响应！', flush=True)
        return []

    # 取最大的响应（通常是完整数据）
    calendar_data = max(calendar_responses, key=len)
    print(f'  选择最大的响应 ({len(calendar_data)} bytes) 进行解析', flush=True)

    try:
        data = json.loads(calendar_data)
    except json.JSONDecodeError as e:
        print(f'  ❌ JSON 解析失败: {e}', flush=True)
        return []

    # 提取所有有回放的比赛
    replays = []
    for day in data.get('data', {}).get('calendar_list', []):
        for m in day.get('matches', []):
            info = m.get('live_info', {})
            nid = info.get('replay_note_id')
            token = info.get('xsec_token')
            if nid and token:
                replays.append({
                    'note_id': nid,
                    'xsec_token': token,
                    'date': day.get('date_label', ''),
                    'group': m.get('group_label', ''),
                    'home': m.get('home_team_name', ''),
                    'away': m.get('away_team_name', ''),
                    'score': f"{m.get('home_score', '?')}:{m.get('away_score', '?')}"
                })

    print(f'  共 {len(replays)} 场回放可供抓取', flush=True)
    for r in replays:
        print(f'    {r["date"]} {r["group"]} {r["home"]} vs {r["away"]} ({r["note_id"]})',
              flush=True)
    return replays


# ── 第2步：逐个获取视频 URL ──

async def fetch_video_for_match(note_id, xsec_token, match_name, page, max_retry=3):
    """为单场比赛获取视频URL，失败自动重试。"""
    for attempt in range(1, max_retry + 1):
        url = (f'https://www.xiaohongshu.com/explore/{note_id}'
               f'?xsec_token={xsec_token}&xsec_source=pc_feed')

        video_urls = []

        def on_req(req):
            u = req.url
            if any(k in u for k in ['sns-video', '.mp4?', '/stream/']):
                if u not in video_urls:
                    video_urls.append(u)

        page.on('request', on_req)

        print(f'    尝试 {attempt}/{max_retry}: {match_name[:50]}...', flush=True)
        try:
            await page.goto(url, wait_until='domcontentloaded', timeout=20000)
            await asyncio.sleep(6)
        except Exception as e:
            print(f'    error: {e}', flush=True)

        page.remove_listener('request', on_req)

        if video_urls:
            print(f'    ✅ 成功获取 {len(video_urls)} 个视频URL', flush=True)
            return video_urls
        else:
            print(f'    ⚠️ 未获取到视频URL (尝试 {attempt}/{max_retry})', flush=True)
            if attempt < max_retry:
                await asyncio.sleep(3)

    print(f'    ❌ 重试 {max_retry} 次后仍未获取到视频URL', flush=True)
    return []


async def fetch_all_videos(replays):
    """遍历所有回放，抓取视频 URL。"""
    from playwright.async_api import async_playwright

    print(f'\n>>> 2/2 共 {len(replays)} 场回放，开始获取视频URL...\n', flush=True)

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        ctx = await browser.new_context(
            user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            viewport={'width': 1920, 'height': 1080}
        )
        page = await ctx.new_page()

        results = []
        for i, r in enumerate(replays):
            match_label = (f"{r['date']} {r['group']} "
                           f"{r['home']} vs {r['away']} {r['score']}")
            print(f'  [{i + 1}/{len(replays)}] {match_label}', flush=True)

            urls = await fetch_video_for_match(
                r['note_id'], r['xsec_token'], match_label, page)

            results.append({
                'match': match_label,
                'note_id': r['note_id'],
                'video_urls': list(set(urls))
            })
            print(f'    -> {len(urls)} 个视频URL\n', flush=True)

        await browser.close()

    return results


# ── 主流程 ──

async def main():
    # 1. 获取回放列表
    replays = await fetch_calendar_from_api()
    if not replays:
        print('没有找到回放比赛，退出。', flush=True)
        sys.exit(1)

    # 2. 逐个抓取视频 URL
    results = await fetch_all_videos(replays)

    # 3. 保存结果
    out_dir = Path('xhs_debug')
    out_dir.mkdir(exist_ok=True)
    out_path = out_dir / 'all_video_urls.json'
    out_path.write_text(json.dumps(results, ensure_ascii=False, indent=2),
                        encoding='utf-8')
    print(f'结果已保存: {out_path}', flush=True)

    # 4. 打印汇总
    print('\n===== 汇总 =====', flush=True)
    success = 0
    for r in results:
        print(f"{r['match']}", flush=True)
        if r['video_urls']:
            success += 1
            for u in r['video_urls']:
                print(f"  {u[:150]}", flush=True)
        else:
            print(f"  ⚠️ 未获取到", flush=True)

    print(f'\n成功: {success}/{len(results)}', flush=True)


if __name__ == '__main__':
    asyncio.run(main())
