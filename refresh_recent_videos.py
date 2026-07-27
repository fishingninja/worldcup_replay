#!/usr/bin/env python3
"""定点刷新指定 note_id 的视频 URL（重新抓取带时效签名的 XHS 视频）。

适用场景：某几场比赛的视频签名 URL 过期、无法播放时，仅针对这几场重新抓取，
不触发全量抓取。

用法：
    python refresh_recent_videos.py
（默认刷新最近三场：决赛 / 季军赛 / 半决赛）

流程：
1. 从赛程 SSR 取得这三场的最新 xsec_token（xsec_token 与笔记页绑定，会失效）
2. 用 Playwright 打开笔记页、捕获最新的 .mp4 视频请求
3. 将新 video_urls 写回 xhs_debug/all_video_urls.json（其余比赛保留不变）
4. 打印结果，供后续 generate_video_urls_json.py + sync_inline_urls.py + push_api.py 使用
"""
import asyncio
import json
import re
import sys
import urllib.request
from pathlib import Path


SSR_URL = 'https://www.xiaohongshu.com/worldcup26?channel_id=&channel_type=explore_feed'

# 最近三场（按 kickoff 倒序）：决赛、季军赛、半决赛
TARGET_NOTE_IDS = [
    '6a5d62670000000014006d25',  # 7/20 决赛 西班牙 vs 阿根廷
    '6a5c15d5000000001d02099f',  # 7/19 季军赛 法国 vs 英格兰
    '6a58008e00000000070293c3',  # 7/16 半决赛 英格兰 vs 阿根廷
]


def _download_ssr_html():
    req = urllib.request.Request(SSR_URL, headers={
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/149.0.0.0 Safari/537.36 Edg/149.0.0.0',
        'Accept': 'text/html,*/*',
        'Accept-Language': 'zh-CN,zh;q=0.9',
    })
    try:
        return urllib.request.urlopen(req, timeout=30).read().decode('utf-8', errors='replace')
    except Exception as e:
        print(f'  ❌ SSR 页面下载失败: {e}', flush=True)
        return ''


def _xsec_tokens_from_ssr(html):
    """返回 {note_id: xsec_token} 映射（涵盖全部回放）。"""
    if not html:
        return {}
    start = html.find('window.__INITIAL_STATE__')
    if start < 0:
        return {}
    eq = html.find('=', start)
    script_end = html.find('</script>', eq)
    if script_end < 0:
        return {}
    raw = html[eq + 1:script_end]
    raw = re.sub(r':undefined(?=[,}])', ':null', raw)
    try:
        state = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    try:
        rc = state['worldCupMatchSchedule']['rawCalendarData']
    except (KeyError, TypeError):
        return {}
    cl = rc.get('calendarList', [])
    mapping = {}
    for day in cl:
        for m in day.get('matches', []):
            info = m.get('liveInfo', {})
            nid = info.get('replayNoteId')
            token = info.get('xsecToken')
            if nid and token:
                mapping[nid] = {
                    'xsec_token': token,
                    'home': m.get('homeTeamName', ''),
                    'away': m.get('awayTeamName', ''),
                    'date': day.get('dateLabel', ''),
                }
    return mapping


async def fetch_video_for_note(ctx, note_id, xsec_token, label, sem, max_retry=3):
    """打开单个笔记页，捕获最新的 .mp4 视频请求。保持同一 context（xsec_token 会话绑定）。"""
    async with sem:
        for attempt in range(1, max_retry + 1):
            page = await ctx.new_page()
            video_urls = []

            def on_req(req):
                u = req.url
                if any(k in u for k in ['sns-video', '.mp4?', '/stream/']):
                    if u not in video_urls:
                        video_urls.append(u)

            page.on('request', on_req)
            url = (f'https://www.xiaohongshu.com/explore/{note_id}'
                   f'?xsec_token={xsec_token}&xsec_source=pc_feed')
            print(f'  [{label}] 尝试 {attempt}/{max_retry}...', flush=True)
            try:
                await page.goto(url, wait_until='domcontentloaded', timeout=20000)
                await asyncio.sleep(6)  # 等待视频请求触发
            except Exception as e:
                print(f'  [{label}] goto 出错: {e}', flush=True)
            page.remove_listener('request', on_req)
            await page.close()
            if video_urls:
                print(f'  [{label}] ✅ 抓到 {len(video_urls)} 个视频URL', flush=True)
                return video_urls
            if attempt < max_retry:
                await asyncio.sleep(2)
        print(f'  [{label}] ❌ 失败', flush=True)
        return []


async def main():
    print('>>> 从 SSR 获取最新 xsec_token...', flush=True)
    html = _download_ssr_html()
    token_map = _xsec_tokens_from_ssr(html)
    print(f'  SSR 中可用回放 token: {len(token_map)} 场', flush=True)

    targets = []
    for nid in TARGET_NOTE_IDS:
        info = token_map.get(nid)
        if not info:
            print(f'  ⚠️ note {nid} 未在 SSR 中找到（可能已下架），跳过', flush=True)
            continue
        targets.append((nid, info['xsec_token'], info['home'], info['away'], info['date']))

    if not targets:
        print('❌ 没有可刷新的目标，退出', flush=True)
        sys.exit(1)

    print(f'\n>>> 用 Playwright 刷新 {len(targets)} 场视频URL...\n', flush=True)
    from playwright.async_api import async_playwright
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        ctx = await browser.new_context(
            user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/149.0.0.0 Safari/537.36 Edg/149.0.0.0',
            viewport={'width': 1920, 'height': 1080}
        )
        sem = asyncio.Semaphore(3)
        tasks = []
        for i, (nid, token, home, away, date) in enumerate(targets):
            label = f'[{i + 1}/{len(targets)}] {date} {home} vs {away}'
            tasks.append(fetch_video_for_note(ctx, nid, token, label, sem))
        raw_results = await asyncio.gather(*tasks)
        await ctx.close()

    # 加载已有 all_video_urls.json，更新目标场次的 video_urls
    src = Path('xhs_debug/all_video_urls.json')
    if not src.exists():
        print(f'❌ 找不到 {src}，无法合并', flush=True)
        sys.exit(1)
    with open(src, encoding='utf-8') as f:
        existing = json.load(f)

    by_id = {r['note_id']: r for r in existing if r.get('note_id')}
    refreshed = 0
    for (nid, _t, home, away, date), urls in zip(targets, raw_results):
        if not urls:
            print(f'  ⚠️ {date} {home} vs {away} 未抓到新 URL，保留旧链接', flush=True)
            continue
        entry = by_id.get(nid)
        if entry is None:
            # 不在已有数据里：新建一条
            existing.append({
                'match': f'{date} {home} vs {away}',
                'note_id': nid,
                'teamA': home,
                'teamB': away,
                'video_urls': list(set(urls)),
            })
        else:
            entry['video_urls'] = list(set(urls))
        refreshed += 1
        print(f'  ✅ 已更新 {date} {home} vs {away} ({len(urls)} 个URL)', flush=True)

    src.write_text(json.dumps(existing, ensure_ascii=False, indent=2), encoding='utf-8')
    print(f'\n结果已写回: {src}（本次刷新 {refreshed} 场）', flush=True)


if __name__ == '__main__':
    asyncio.run(main())
