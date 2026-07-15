#!/usr/bin/env python3
"""从 XHS 赛程 API 实时获取回放比赛，并抓取视频流 URL。

v4 改进:
- 增量抓取：只抓取还没有视频URL的比赛，跳过已有的（节省资源）
- 自动刷新：最近 48 小时内的比赛强制重新抓取（签名~10分钟过期）
- 修复 subdivision flag（如苏格兰🏴󠁧󠁭󠁯󠁰󠁿）的 emoji 匹配问题
- 支持 --full 参数强制全量抓取，--no-refresh 关闭自动刷新
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


# ── 第1步：从 SSR HTML 提取赛程数据（无需浏览器，无需登录）──

SSR_URL = 'https://www.xiaohongshu.com/worldcup26?channel_id=&channel_type=explore_feed'


def _download_ssr_html():
    """下载 XHS 世界杯页面 HTML，返回字符串；失败返回空串。"""
    import urllib.request
    req = urllib.request.Request(SSR_URL, headers={
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/149.0.0.0 Safari/537.36 Edg/149.0.0.0',
        'Accept': 'text/html,*/*',
        'Accept-Language': 'zh-CN,zh;q=0.9',
    })
    try:
        resp = urllib.request.urlopen(req, timeout=30)
    except Exception as e:
        print(f'  ❌ 页面下载失败: {e}', flush=True)
        return ''
    return resp.read().decode('utf-8', errors='replace')


def _parse_calendar_state(html):
    """从 SSR HTML 中解析出 rawCalendarData 的 calendarList，失败返回 None。"""
    if not html:
        return None
    start = html.find('window.__INITIAL_STATE__')
    if start < 0:
        return None
    eq = html.find('=', start)
    script_end = html.find('</script>', eq)
    if script_end < 0:
        return None
    raw = html[eq + 1:script_end]
    raw = re.sub(r':undefined(?=[,}])', ':null', raw)
    try:
        state = json.loads(raw)
    except json.JSONDecodeError:
        return None
    try:
        rc = state['worldCupMatchSchedule']['rawCalendarData']
    except (KeyError, TypeError):
        return None
    cl = rc.get('calendarList', [])
    if not cl:
        return None
    return cl


def fetch_calendar_ssr(max_retry=4):
    """直接下载 XHS 页面 HTML，从 SSR 数据中提取回放比赛列表。

    XHS 的 SSR 响应并不稳定：有时会返回尚未填充 rawCalendarData 的
    精简页面（约 800KB，无赛程数据），导致一次性抓取失败。这里增加
    重试机制：若未解析出有效 calendarList，则短暂等待后重新下载。
    """
    import time
    print('>>> 1/2 从 SSR HTML 提取赛程数据...', flush=True)

    cl = None
    for attempt in range(1, max_retry + 1):
        html = _download_ssr_html()
        print(f'  页面大小: {len(html)} bytes (尝试 {attempt}/{max_retry})', flush=True)
        cl = _parse_calendar_state(html)
        if cl is not None:
            if attempt > 1:
                print(f'  ✅ 第 {attempt} 次重试成功解析出赛程', flush=True)
            break
        print('  ⚠️ 未解析出有效赛程数据（可能是 SSR 异步数据未就绪），准备重试', flush=True)
        if attempt < max_retry:
            time.sleep(3 * attempt)

    if cl is None:
        print('  ❌ 多次重试后仍无法获取赛程数据', flush=True)
        return []

    print(f'  calendarList: {len(cl)} 天', flush=True)

    # ── 保存实时赛程原始数据（兼容旧格式供 generate_schedule_from_xhs.py 使用）──
    # 将 camelCase 转为旧版 snake_case 格式
    def cc2sc(name):
        """camelCase 转 snake_case"""
        return re.sub(r'([A-Z])', r'_\1', name).lower()
    
    def convert_match(m):
        result = {}
        for k, v in m.items():
            sk = cc2sc(k)
            if isinstance(v, dict):
                result[sk] = convert_match(v)
            else:
                result[sk] = v
        return result
    
    old_format = {'data': {'calendar_list': []}}
    for day in cl:
        old_day = {'date_label': day.get('dateLabel', ''), 'date': day.get('date', ''), 'matches': []}
        for m in day.get('matches', []):
            old_day['matches'].append(convert_match(m))
        old_format['data']['calendar_list'].append(old_day)
    
    raw_path = Path('xhs_debug/calendar_info_raw.json')
    raw_path.write_text(json.dumps(old_format, ensure_ascii=False), encoding='utf-8')
    print(f'  ✅ 赛程原始数据已保存: {raw_path}', flush=True)

    replays = []
    for day in cl:
        for m in day.get('matches', []):
            info = m.get('liveInfo', {})
            nid = info.get('replayNoteId')
            token = info.get('xsecToken')
            if nid and token:
                replays.append({
                    'note_id': nid,
                    'xsec_token': token,
                    'date': day.get('dateLabel', ''),
                    'group': m.get('groupLabel', ''),
                    'home': m.get('homeTeamName', ''),
                    'away': m.get('awayTeamName', ''),
                    'score': f"{m.get('homeScore', '?')}:{m.get('awayScore', '?')}",
                    'teamA': m.get('homeTeamName', ''),
                    'teamB': m.get('awayTeamName', ''),
                })

    # 最新优先
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


# ── 自动刷新：最近 N 小时内的比赛强制重新抓取 ──

REFRESH_HOURS = 48  # 最近48小时内的比赛强制刷新（签名约10分钟过期）


def parse_date_label(label: str):
    """解析 dateLabel 如 '07月08日' 为 (month, day)，失败返回 None。"""
    if not label:
        return None
    m = re.match(r'(\d{1,2})月(\d{1,2})日', label)
    if not m:
        return None
    return int(m.group(1)), int(m.group(2))


def is_recent(date_label: str, hours: int = REFRESH_HOURS) -> bool:
    """判断 dateLabel 对应的比赛日期是否在最近 hours 小时内。"""
    md = parse_date_label(date_label)
    if not md:
        return False
    from datetime import datetime, timedelta, date
    month, day = md
    now = datetime.now()
    match_date = date(now.year, month, day)
    cutoff_date = (now - timedelta(hours=hours)).date()
    return match_date >= cutoff_date


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
    parser.add_argument('--no-refresh', action='store_true',
                        help='关闭自动刷新（最近48h内的比赛也跳过，仅抓缺URL的比赛）')
    args = parser.parse_args()

    # 加载已有数据（用于增量判断）
    existing = {} if args.full else load_existing_results()
    if args.full:
        print('🔧 --full 模式：全量抓取所有比赛', flush=True)
    elif existing:
        refresh_info = f' (最近{REFRESH_HOURS}h内强制刷新)' if not args.no_refresh else ''
        print(f'📦 已有数据: {len(existing)} 场（将跳过已有视频URL的比赛{refresh_info}）', flush=True)
    else:
        print('📦 无已有数据，将全量抓取', flush=True)

    # ── 第1步：从 SSR HTML 获取赛程（不需要浏览器）──
    replays = fetch_calendar_ssr()

    if not replays:
        print('没有找到回放比赛，退出。', flush=True)
        sys.exit(1)

    # ── 第2步：增量分析 ──
    to_fetch = []
    skipped = []
    refreshed = []  # 最近48h内强制刷新的比赛
    for r in replays:
        old = existing.get(r['note_id'])
        has_urls = bool(old and old.get('video_urls'))

        # 🔄 自动刷新：最近48小时内的比赛即使有URL也重新抓取
        if (has_urls
                and not args.full
                and not args.no_refresh
                and is_recent(r.get('date', ''))):
            refreshed.append(r)
            to_fetch.append(r)
            continue

        if has_urls:
            skipped.append(r)
        else:
            to_fetch.append(r)

    print(f'\n>>> 增量分析: 共 {len(replays)} 场', flush=True)
    print(f'    需抓取: {len(to_fetch)} 场', flush=True)
    if refreshed:
        print(f'      (其中 {len(refreshed)} 场为最近{REFRESH_HOURS}h内强制刷新)', flush=True)
    print(f'    跳过(已有URL): {len(skipped)} 场', flush=True)

    if skipped:
        print('  跳过的比赛:', flush=True)
        for r in skipped:
            print(f'    ✓ {r["date"]} {r["home"]} vs {r["away"]}', flush=True)

    # ── 第3步：启动 Playwright 并发抓取视频 URL ──
    raw_results = []
    if to_fetch:
        from playwright.async_api import async_playwright
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            ctx = await browser.new_context(
                user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/149.0.0.0 Safari/537.36 Edg/149.0.0.0',
                viewport={'width': 1920, 'height': 1080}
            )

            print(f'\n>>> 2/2 并发抓取 {len(to_fetch)} 场视频URL (并发数=4)...\n', flush=True)
            sem = asyncio.Semaphore(4)

            tasks = []
            for i, r in enumerate(to_fetch):
                label = f"[{i + 1}/{len(to_fetch)}] {r['date']} {r['home']} vs {r['away']} ({r['score']})"
                tasks.append(fetch_video_for_match(
                    ctx, r['note_id'], r['xsec_token'], label, sem))

            raw_results = await asyncio.gather(*tasks)
            await ctx.close()
    else:
        print('\n✅ 所有比赛已有视频URL，无需抓取', flush=True)

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
    if refreshed:
        print(f'  (刷新 {len(refreshed)} + 新抓取 {new_success - len(refreshed)} + 保留 {len(skipped)} 场)', flush=True)
    else:
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
