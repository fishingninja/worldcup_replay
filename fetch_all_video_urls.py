#!/usr/bin/env python3
"""从 XHS 赛程 API 实时获取回放比赛，并抓取视频流 URL。

v4 改进:
- 增量抓取：只抓取还没有视频URL的比赛，跳过已有的（节省资源）
- 修复 subdivision flag（如苏格兰🏴󠁧󠁭󠁯󠁰󠁿）的 emoji 匹配问题
- 支持 --full 参数强制全量抓取
- 自动回写 xhsNoteId 到 index.html（防止遗漏导致前端无法关联视频）

关键设计:
- 保持同一浏览器会话：xsec_token 是会话绑定的，换浏览器就失效
- 并发抓取：同一上下文中 4 个页面并行
"""
import asyncio
import json
import sys
import re
import argparse
from pathlib import Path


# ── 旗帜 emoji 正则 ──
# 匹配所有旗帜 emoji：
# - U+1F1E6-U+1F1FF: Regional indicator symbols（普通国旗，如🇨🇳🇲🇽🇰🇷）
# - U+1F3F4: Waving black flag（subdivision flag 基础字符，如🏴）
# - U+E0020-U+E007F: Tag characters（subdivision flag 标签序列，如󠁧󠁭󠁯󠁰󠁿）
#   苏格兰🏴󠁧󠁭󠁯󠁰󠁿 = U+1F3F4 + U+E0067 + U+E006D + U+E006F + U+E0070 + U+E007F
FLAG_EMOJI_RE = re.compile(r'[\U0001F1E6-\U0001F1FF\U0001F3F4\U000E0020-\U000E007F]+')


def strip_flags(text):
    """去掉旗帜 emoji，返回纯文本队名"""
    return FLAG_EMOJI_RE.sub('', text).strip()


# ── 第1步：拦截 calendar_info，提取回放列表 ──

async def fetch_calendar_in_session(page):
    """在当前 page 上访问 worldcup26 并拦截 calendar_info，返回回放列表。"""
    print('>>> 1/2 拦截 calendar_info API...', flush=True)

    calendar_responses = []
    calendar_event = asyncio.Event()

    async def on_response(res):
        if 'calendar_info' in res.url:
            try:
                body = await res.text()
                calendar_responses.append(body)
                print(f'  [CAPTURED] calendar_info #{len(calendar_responses)} ({len(body)} bytes)', flush=True)
                if len(body) > 100 and not calendar_event.is_set():  # 有效数据
                    calendar_event.set()
            except Exception as e:
                print(f'  [ERR] 读取失败: {e}', flush=True)

    page.on('response', on_response)

    print('  goto worldcup26 ...', flush=True)
    try:
        await page.goto('https://www.xiaohongshu.com/worldcup26?wcup_source=web_sidebar_entry',
                        wait_until='domcontentloaded', timeout=30000)
    except Exception as e:
        print(f'  goto 超时（不影响数据拦截）: {e}', flush=True)

    # 等待 calendar_info 响应（最多 15 秒）
    print('  等待 calendar_info 响应...', flush=True)
    try:
        await asyncio.wait_for(calendar_event.wait(), timeout=15)
        print('  ✅ 已获取到 calendar_info 数据', flush=True)
    except asyncio.TimeoutError:
        print('  ⚠️ 等待超时，使用已捕获的响应', flush=True)

    # 额外等待，确保拿到完整数据
    await asyncio.sleep(3)

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
                    'teamA': home_name,   # 来自 calendar_info API（无国旗）
                    'teamB': away_name,   # 来自 calendar_info API（无国旗）
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


# ── 第3步：加载已有数据（增量用）──

def load_existing_results():
    """加载已有的 all_video_urls.json，返回 {note_id: entry} 映射。"""
    path = Path('xhs_debug/all_video_urls.json')
    if not path.exists():
        return {}
    try:
        with open(path, encoding='utf-8') as f:
            data = json.load(f)
        return {r['note_id']: r for r in data if r.get('note_id')}
    except (json.JSONDecodeError, KeyError, TypeError):
        return {}


# ── 主流程 ──

async def main():
    parser = argparse.ArgumentParser(description='抓取 XHS 世界杯回放视频 URL')
    parser.add_argument('--full', action='store_true',
                        help='强制全量抓取（忽略已有数据，重新抓取所有比赛）')
    args = parser.parse_args()

    from playwright.async_api import async_playwright

    # 加载已有数据（用于增量判断）
    existing = {} if args.full else load_existing_results()
    if args.full:
        print('🔧 --full 模式：全量抓取所有比赛', flush=True)
    elif existing:
        print(f'📦 已有数据: {len(existing)} 场（将跳过已有视频URL的比赛）', flush=True)
    else:
        print('📦 无已有数据，将全量抓取', flush=True)

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

        # 2. 增量分析：哪些比赛需要抓取视频URL
        to_fetch = []
        skipped = []
        for r in replays:
            old = existing.get(r['note_id'])
            if old and old.get('video_urls'):
                # 已有视频URL，跳过
                skipped.append(r)
            else:
                # 新比赛或之前抓取失败，需要抓取
                to_fetch.append(r)

        print(f'\n>>> 增量分析: 共 {len(replays)} 场', flush=True)
        print(f'    需抓取: {len(to_fetch)} 场', flush=True)
        print(f'    跳过(已有URL): {len(skipped)} 场', flush=True)

        if skipped:
            print('  跳过的比赛:', flush=True)
            for r in skipped:
                print(f'    ✓ {r["date"]} {r["home"]} vs {r["away"]}', flush=True)

        # 3. 并发抓取视频（同一 context，不同 page）
        raw_results = []
        if to_fetch:
            print(f'\n>>> 2/2 并发抓取 {len(to_fetch)} 场视频URL (并发数=4)...\n', flush=True)
            sem = asyncio.Semaphore(4)

            tasks = []
            for i, r in enumerate(to_fetch):
                label = f"[{i + 1}/{len(to_fetch)}] {r['date']} {r['home']} vs {r['away']} ({r['score']})"
                tasks.append(fetch_video_for_match(
                    ctx, r['note_id'], r['xsec_token'], label, sem))

            raw_results = await asyncio.gather(*tasks)
        else:
            print('\n✅ 所有比赛已有视频URL，无需抓取', flush=True)

        await browser.close()

    # 4. 合并结果（新抓取 + 保留已有）
    results = []

    # 新抓取的结果
    for r, urls in zip(to_fetch, raw_results):
        results.append({
            'match': (f"{r['date']} {r['group']} "
                      f"{r['home']} vs {r['away']} {r['score']}"),
            'note_id': r['note_id'],
            'teamA': r['teamA'],   # 来自 calendar_info API（无国旗）
            'teamB': r['teamB'],   # 来自 calendar_info API（无国旗）
            'video_urls': list(set(urls))
        })

    # 保留已有的结果（用新的 match 信息，保留旧的 video_urls）
    for r in skipped:
        old = existing[r['note_id']]
        results.append({
            'match': (f"{r['date']} {r['group']} "
                      f"{r['home']} vs {r['away']} {r['score']}"),
            'note_id': r['note_id'],
            'teamA': r['teamA'],
            'teamB': r['teamB'],
            'video_urls': old.get('video_urls', [])
        })

    # 恢复日期正序（方便阅读）
    results.sort(key=lambda x: x['match'])

    # 5. 保存
    out_dir = Path('xhs_debug')
    out_dir.mkdir(exist_ok=True)
    out_path = out_dir / 'all_video_urls.json'
    out_path.write_text(json.dumps(results, ensure_ascii=False, indent=2),
                        encoding='utf-8')
    print(f'\n结果已保存: {out_path}', flush=True)

    # 6. 汇总
    print('\n===== 汇总 =====', flush=True)
    new_success = sum(1 for _, urls in zip(to_fetch, raw_results) if urls)
    total_success = sum(1 for r in results if r['video_urls'])

    if to_fetch:
        print(f'本次抓取: {new_success}/{len(to_fetch)} 成功', flush=True)
        for r, urls in zip(to_fetch, raw_results):
            label = f"{r['date']} {r['home']} vs {r['away']}"
            if urls:
                print(f"  ✅ {label} ({len(urls)} 个URL)", flush=True)
            else:
                print(f"  ❌ {label}", flush=True)
    else:
        print('本次无需抓取', flush=True)

    print(f'\n总计: {total_success}/{len(results)} 有视频URL', flush=True)
    print(f'  (新抓取 {new_success} + 保留 {len(skipped)} 场)', flush=True)

    if total_success == 0:
        print('⚠️ 全部失败，检查 xsec_token 是否在日历页加载后立即使用', flush=True)
        sys.exit(1)

    # 7. 自动回写 xhsNoteId 到 index.html 的 match-data
    update_match_data_xhs_note_id(results)


def update_match_data_xhs_note_id(results):
    """将 results 中的 note_id 回写到 index.html 的 match-data。

    匹配逻辑：results 中的 teamA/teamB 来自 calendar_info API（无国旗 emoji），
    而 match-data 中的 teamA/teamB 含国旗 emoji（包括 subdivision flag 如苏格兰🏴󠁧󠁭󠁯󠁰󠁿），
    所以匹配时两边统一用 strip_flags() 去掉所有旗帜 emoji 后比较。
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
    #    results 中的 teamA/teamB 来自 calendar_info API（无国旗）
    #    使用 strip_flags() 确保两边格式一致
    NOTE_MAP = {}  # key: (teamA_no_flag, teamB_no_flag) -> note_id
    for r in results:
        team_a = r.get('teamA', '')
        team_b = r.get('teamB', '')
        if team_a and team_b:
            clean_a = strip_flags(team_a)
            clean_b = strip_flags(team_b)
            NOTE_MAP[(clean_a, clean_b)] = r['note_id']

    if not NOTE_MAP:
        print('\n[回写] 没有可回写的 note_id，跳过', flush=True)
        return

    # 3. 遍历 matches，补充 xhsNoteId
    updated = 0
    for entry in matches:
        entry_a = strip_flags(entry.get('teamA', ''))
        entry_b = strip_flags(entry.get('teamB', ''))

        found = False
        for (r_a, r_b), nid in NOTE_MAP.items():
            if entry_a == r_a and entry_b == r_b:
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
