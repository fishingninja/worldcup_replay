#!/usr/bin/env python3
"""从 XHS 赛程 API 实时获取所有回放比赛，并抓取视频流 URL。

关键修复 v3:
- 保持同一浏览器会话：不在 calendar_info 和视频抓取之间关闭浏览器，
  xsec_token 是会话绑定的，换浏览器就失效。
- 最新优先处理：用户最关心最近的比赛。
- 并发抓取：同一上下文中 4 个页面并行，16 场比赛 ~30 秒完成。
- 自动回写 xhsNoteId 到 index.html（防止遗漏导致前端无法关联视频）。
"""
import asyncio
import json
import sys
import re
from pathlib import Path


# ── 第1步：拦截 calendar_info，提取回放列表 ──

async def fetch_calendar_in_session(page):
    """在当前 page 上访问 worldcup26 并拦截 calendar_info，返回回放列表。"""
    print('>>> 1/2 拦截 calendar_info API...', flush=True)
    
    calendar_responses = []
    
    async def on_response(res):
        if 'calendar_info' in res.url:
            try:
                body = await res.text()
                calendar_responses.append(body)
                print(f'  [CAPTURED] calendar_info #{len(calendar_responses)} ({len(body)} bytes)', flush=True)
            except Exception as e:
                print(f'  [ERR] 读取失败: {e}', flush=True)
    
    page.on('response', on_response)
    
    print('  goto worldcup26 ...', flush=True)
    await page.goto('https://www.xiaohongshu.com/worldcup26',
                    wait_until='domcontentloaded', timeout=30000)
    
    # 等待初始数据加载
    await asyncio.sleep(5)
    
    # 滚动页面以触发更多内容加载
    print('  滚动页面加载更多数据...', flush=True)
    for i in range(5):
        await page.evaluate('window.scrollBy(0, 500)')
        await asyncio.sleep(1)
    
    # 再次等待，确保 API 响应完成
    await asyncio.sleep(5)
    
    page.remove_listener('response', on_response)

    if not calendar_responses:
        print('  ❌ 未捕获到 calendar_info！', flush=True)
        return []

    # 取最大响应
    body = max(calendar_responses, key=len)
    print(f'  选择最大响应 ({len(body)} bytes)', flush=True)

    try:
        data = json.loads(body)
    except json.JSONDecodeError as e:
        print(f'  ❌ JSON 解析失败: {e}', flush=True)
        return []

    replays = []
    for day in data.get('data', {}).get('calendar_list', []):
        for m in day.get('matches', []):
            info = m.get('live_info', {})
            nid = info.get('replay_note_id')
            token = info.get('xsec_token')
            if nid and token:
                home_name = m.get('home_team_name', '')
                away_name = m.get('away_team_name', '')
                replays.append({
                    'note_id': nid,
                    'xsec_token': token,
                    'date': day.get('date_label', ''),
                    'group': m.get('group_label', ''),
                    'home': home_name,
                    'away': away_name,
                    'score': f"{m.get('home_score', '?')}:{m.get('away_score', '?')}",
                    'teamA': home_name,   # 和 match-data 中的 teamA 格式一致（含国旗）
                    'teamB': away_name,   # 和 match-data 中的 teamB 格式一致（含国旗）
                })

    # 最新优先（日期靠后的排前面，用户最关心）
    replays.reverse()

    print(f'  共 {len(replays)} 场回放（最新优先）', flush=True)
    for r in replays[:5]:
        print(f'    {r["date"]} {r["group"]} {r["home"]} vs {r["away"]}', flush=True)
    if len(replays) > 5:
        print(f'    ... 及其他 {len(replays) - 5} 场', flush=True)

    return replays


# ── 第2步：并发抓取视频 URL ──

async def fetch_video_for_match(ctx, note_id, xsec_token, match_label, sem, max_retry=2):
    """为单场比赛获取视频URL（在独立 page 上，同一 context）。"""
    async with sem:  # 限流
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

            prefix = f'  [{match_label[:40]}]'
            print(f'{prefix} 尝试 {attempt}/{max_retry}...', flush=True)
            try:
                await page.goto(url, wait_until='domcontentloaded', timeout=20000)
                await asyncio.sleep(5)  # 等待视频请求触发
            except Exception as e:
                print(f'{prefix} goto 出错: {e}', flush=True)

            page.remove_listener('request', on_req)
            await page.close()

            if video_urls:
                print(f'{prefix} ✅ {len(video_urls)} 个视频URL', flush=True)
                return video_urls
            else:
                print(f'{prefix} ⚠️ 无视频URL', flush=True)
                if attempt < max_retry:
                    await asyncio.sleep(2)

        print(f'{prefix} ❌ 失败', flush=True)
        return []


async def main():
    from playwright.async_api import async_playwright

    async with async_playwright() as p:
        # ── 单一浏览器会话，贯穿全程 ──
        browser = await p.chromium.launch(headless=True)
        ctx = await browser.new_context(
            user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            viewport={'width': 1920, 'height': 1080}
        )
        page = await ctx.new_page()

        # 1. 获取回放列表（使用同一会话）
        replays = await fetch_calendar_in_session(page)
        await page.close()

        if not replays:
            print('没有找到回放比赛，退出。', flush=True)
            await browser.close()
            sys.exit(1)

        # 2. 并发抓取视频（同一 context，不同 page）
        print(f'\n>>> 2/2 并发抓取 {len(replays)} 场视频URL (并发数=4)...\n', flush=True)

        sem = asyncio.Semaphore(4)  # 最多 4 个并发

        tasks = []
        for i, r in enumerate(replays):
            label = f"[{i + 1}/{len(replays)}] {r['date']} {r['home']} vs {r['away']} ({r['score']})"
            tasks.append(fetch_video_for_match(
                ctx, r['note_id'], r['xsec_token'], label, sem))

        raw_results = await asyncio.gather(*tasks)

        await browser.close()

    # 3. 组装结果（恢复原始顺序：日期从前到后）
    results = []
    for (i, r), urls in zip(enumerate(replays), raw_results):
        results.append({
            'match': (f"{r['date']} {r['group']} "
                      f"{r['home']} vs {r['away']} {r['score']}"),
            'note_id': r['note_id'],
            'teamA': r['teamA'],   # 含国旗，和 match-data 一致
            'teamB': r['teamB'],   # 含国旗，和 match-data 一致
            'video_urls': list(set(urls))
        })

    # 恢复日期正序（方便阅读）
    results.reverse()

    # 4. 保存
    out_dir = Path('xhs_debug')
    out_dir.mkdir(exist_ok=True)
    out_path = out_dir / 'all_video_urls.json'
    out_path.write_text(json.dumps(results, ensure_ascii=False, indent=2),
                        encoding='utf-8')
    print(f'\n结果已保存: {out_path}', flush=True)

    # 5. 汇总
    print('\n===== 汇总 =====', flush=True)
    success = 0
    for r in results:
        if r['video_urls']:
            success += 1
            print(f"✅ {r['match']}", flush=True)
            for u in r['video_urls']:
                print(f"   {u[:150]}", flush=True)
        else:
            print(f"❌ {r['match']}", flush=True)

    print(f'\n成功: {success}/{len(results)}', flush=True)
    if success == 0:
        print('⚠️ 全部失败，检查 xsec_token 是否在日历页加载后立即使用', flush=True)
        sys.exit(1)

    # 6. 自动回写 xhsNoteId 到 index.html 的 match-data
    update_match_data_xhs_note_id(results)


def update_match_data_xhs_note_id(results):
    """将 results 中的 note_id 回写到 index.html 的 match-data。

    匹配逻辑：results 每条都有 teamA/teamB（含国旗，和 match-data 一致），
    直接用 (teamA, teamB) 元组匹配 match-data 中的条目，补充 xhsNoteId。
    """
    index_path = Path('index.html')
    if not index_path.exists():
        print('\n[回写] 未找到 index.html，跳过 xhsNoteId 回写', flush=True)
        return

    html = index_path.read_text(encoding='utf-8')

    # 1. 提取 match-data JSON
    m = re.search(
        r'<script id="match-data" type="application/json">(.*?)</script>',
        html, re.DOTALL)
    if not m:
        print('\n[回写] 未找到 match-data，跳过', flush=True)
        return

    try:
        matches = json.loads(m.group(1))
    except json.JSONDecodeError as e:
        print(f'\n[回写] match-data JSON 解析失败: {e}', flush=True)
        return

    # 2. 从 results 构建 (clean_teamA, clean_teamB) -> note_id 映射
    #    results 中的 teamA/teamB 来自 calendar_info API（无国旗 emoji），
    #    而 match-data 中的 teamA/teamB 含国旗 emoji，
    #    所以 NOTE_MAP 的 key 用无国旗格式，匹配时两边统一去掉国旗再比较。
    NOTE_MAP = {}  # key: (teamA_no_flag, teamB_no_flag) -> note_id
    for r in results:
        team_a = r.get('teamA', '')
        team_b = r.get('teamB', '')
        if team_a and team_b:
            # teamA/teamB 可能含国旗，统一去掉
            clean_a = re.sub(r'[\U0001F1E6-\U0001F1FF]+', '', team_a).strip()
            clean_b = re.sub(r'[\U0001F1E6-\U0001F1FF]+', '', team_b).strip()
            NOTE_MAP[(clean_a, clean_b)] = r['note_id']

    if not NOTE_MAP:
        print('\n[回写] 没有可回写的 note_id，跳过', flush=True)
        return

    # 3. 遍历 matches，补充 xhsNoteId
    #    注意：results 中的 teamA/teamB 来自 calendar_info API（无国旗），
    #    而 match-data 中的 teamA/teamB 含国旗 emoji，
    #    所以匹配时两边都要去掉国旗 emoji。
    EMOJI_RE = re.compile(r'[\U0001F1E6-\U0001F1FF]+')

    updated = 0
    for entry in matches:
        entry_a = EMOJI_RE.sub('', entry.get('teamA', '')).strip()
        entry_b = EMOJI_RE.sub('', entry.get('teamB', '')).strip()

        found = False
        for (r_a, r_b), nid in NOTE_MAP.items():
            r_a_clean = EMOJI_RE.sub('', r_a).strip()
            r_b_clean = EMOJI_RE.sub('', r_b).strip()
            if entry_a == r_a_clean and entry_b == r_b_clean:
                found = True
                note_id = nid
                break

        if not found:
            continue

        if entry.get('xhsNoteId'):
            continue  # 已有，跳过

        entry['xhsNoteId'] = note_id
        updated += 1
        print(f'  [回写] ✅ {entry["teamA"]} vs {entry["teamB"]} -> {note_id}', flush=True)

    if updated == 0:
        print('\n[回写] 没有需要补充的 xhsNoteId（可能已全部存在）', flush=True)
        return

    # 4. 把更新后的 matches 写回 HTML
    new_json = json.dumps(matches, ensure_ascii=False, indent=2)
    # 按原风格缩进（每行加 2 空格）
    indented = '\n'.join('  ' + line for line in new_json.splitlines())
    new_script = f'<script id="match-data" type="application/json">\n{indented}\n  </script>'

    new_html = html[:m.start()] + new_script + html[m.end():]
    index_path.write_text(new_html, encoding='utf-8')
    print(f'\n[回写] ✅ 已将 {updated} 条 xhsNoteId 写入 index.html', flush=True)


if __name__ == '__main__':
    asyncio.run(main())
