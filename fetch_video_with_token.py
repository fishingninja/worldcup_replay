#!/usr/bin/env python3
"""使用xsec_token构造完整URL后获取视频流"""
import asyncio, json, sys
from pathlib import Path

async def main():
    from playwright.async_api import async_playwright
    
    # 读取赛程数据
    with open(r'E:\project\worldcup_replay\xhs_debug\xiaohongshu_com_api_sns_web_worldcup_calendar_info_FULL.json',
              encoding='utf-8') as f:
        data = json.loads(f.read())
    
    # 提取有回放的比赛 (note_id + xsec_token)
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
                    'match': f"{m['home_team_name']} vs {m['away_team_name']}",
                    'score': f"{m['home_score']}:{m['away_score']}"
                })
    
    print(f'找到 {len(replays)} 场回放', flush=True)
    
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        ctx = await browser.new_context(
            user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            viewport={'width': 1920, 'height': 1080}
        )
        page = await ctx.new_page()
        
        results = []
        
        for i, r in enumerate(replays[:4]):  # 先测试前4个
            note_id = r['note_id']
            token = r['xsec_token']
            match_name = r['match']
            
            # 构造完整URL
            url = f'https://www.xiaohongshu.com/explore/{note_id}?xsec_token={token}&xsec_source=pc_sfeed'
            
            print(f'\n[{i+1}/4] {match_name} ({r["score"]})', flush=True)
            print(f'  URL: {url[:120]}...', flush=True)
            
            video_urls = []
            
            def on_req(req):
                u = req.url
                if any(k in u for k in ['sns-video', '.mp4?', '/stream/']):
                    video_urls.append(u)
                    print(f'    [VIDEO] {u[:150]}', flush=True)
            
            page.on('request', on_req)
            
            await page.goto(url, wait_until='domcontentloaded', timeout=20000)
            await asyncio.sleep(8)
            
            page.remove_listener('request', on_req)
            
            results.append({
                'match': match_name,
                'note_id': note_id,
                'video_urls': video_urls
            })
            print(f'  -> {len(video_urls)} 个视频URL', flush=True)
        
        await browser.close()
    
    # 保存结果
    out_path = Path('xhs_debug/video_urls_with_token.json')
    out_path.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding='utf-8')
    print(f'\n结果保存到: {out_path}', flush=True)
    
    for r in results:
        print(f"\n{r['match']}", flush=True)
        for u in r['video_urls']:
            print(f"  {u[:200]}", flush=True)

if __name__ == '__main__':
    asyncio.run(main())
