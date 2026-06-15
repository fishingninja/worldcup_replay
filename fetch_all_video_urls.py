#!/usr/bin/env python3
"""获取全部12场回放的视频流URL"""
import asyncio, json, sys
from pathlib import Path

async def fetch_video_for_match(note_id, xsec_token, match_name, page):
    """为单场比赛获取视频URL"""
    url = f'https://www.xiaohongshu.com/explore/{note_id}?xsec_token={xsec_token}&xsec_source=pc_sfeed'
    
    video_urls = []
    req_count = 0
    
    def on_req(req):
        nonlocal req_count
        u = req.url
        req_count += 1
        if any(k in u for k in ['sns-video', '.mp4?', '/stream/']):
            if u not in video_urls:
                video_urls.append(u)
    
    page.on('request', on_req)
    
    print(f'  loading {url[:100]}...', flush=True)
    try:
        await page.goto(url, wait_until='domcontentloaded', timeout=20000)
        await asyncio.sleep(6)
    except Exception as e:
        print(f'  error: {e}', flush=True)
    
    page.remove_listener('request', on_req)
    return video_urls


async def main():
    from playwright.async_api import async_playwright
    
    # 读取赛程数据
    with open(r'E:\project\worldcup_replay\xhs_debug\xiaohongshu_com_api_sns_web_worldcup_calendar_info_FULL.json',
              encoding='utf-8') as f:
        data = json.loads(f.read())
    
    # 提取所有有回放的比赛
    replays = []
    for day in data['data']['calendar_list']:
        for m in day['matches']:
            info = m.get('live_info', {})
            nid = info.get('replay_note_id')
            token = info.get('xsec_token')
            if nid and token:
                replays.append({
                    'note_id': nid,
                    'xsec_token': token,
                    'date': day['date_label'],
                    'group': m.get('group_label', ''),
                    'home': m.get('home_team_name', ''),
                    'away': m.get('away_team_name', ''),
                    'score': f"{m.get('home_score','?')}:{m.get('away_score','?')}"
                })
    
    print(f'共 {len(replays)} 场回放，开始获取视频URL...\n', flush=True)
    
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        ctx = await browser.new_context(
            user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            viewport={'width': 1920, 'height': 1080}
        )
        page = await ctx.new_page()
        
        results = []
        for i, r in enumerate(replays):
            match_label = f"{r['date']} {r['group']} {r['home']} vs {r['away']} {r['score']}"
            print(f'[{i+1}/{len(replays)}] {match_label}', flush=True)
            
            urls = await fetch_video_for_match(
                r['note_id'], r['xsec_token'], match_label, page)
            
            results.append({
                'match': match_label,
                'note_id': r['note_id'],
                'video_urls': list(set(urls))
            })
            print(f'  -> {len(urls)} 个视频URL\n', flush=True)
        
        await browser.close()
    
    # 保存
    out = Path('xhs_debug/all_video_urls.json')
    out.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding='utf-8')
    print(f'结果已保存: {out}', flush=True)
    
    # 打印汇总
    print('\n===== 汇总 =====', flush=True)
    for r in results:
        print(f"{r['match']}", flush=True)
        for u in (r['video_urls'] or ['⚠️ 未获取到']):
            print(f"  {u[:150]}", flush=True)


if __name__ == '__main__':
    asyncio.run(main())
