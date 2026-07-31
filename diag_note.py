#!/usr/bin/env python3
"""诊断：打开指定 XHS 笔记页，捕获视频请求/登录墙/页面状态。"""
import asyncio
import json
from pathlib import Path
from playwright.async_api import async_playwright

NOTE_ID = "6a5d62670000000014006d25"  # 07/20 西班牙 vs 阿根廷
TOKEN = open('xhs_debug/calendar_info_raw.json', encoding='utf-8')  # placeholder


def get_token():
    d = json.load(open('xhs_debug/calendar_info_raw.json', encoding='utf-8'))
    for day in d['data']['calendar_list']:
        for m in day['matches']:
            li = m.get('live_info', {})
            if li.get('replay_note_id') == NOTE_ID:
                return li['xsec_token']
    return ""


async def main():
    token = get_token()
    url = f'https://www.xiaohongshu.com/explore/{NOTE_ID}?xsec_token={token}&xsec_source=pc_feed'
    print('URL:', url[:120], '...')

    reqs_media = []
    reqs_all = []

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        ctx = await browser.new_context(
            user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/149.0.0.0 Safari/537.36 Edg/149.0.0.0',
            viewport={'width': 1920, 'height': 1080}
        )
        page = await ctx.new_page()

        def on_req(req):
            u = req.url
            reqs_all.append(u)
            if any(k in u for k in ['sns-video', '.mp4', '/stream/', 'playback', 'video', 'm3u8', 'blob:']):
                reqs_media.append(u)

        def on_resp(resp):
            u = resp.url
            if any(k in u for k in ['sns-video', '.mp4', '/stream/', 'playback', 'video', 'm3u8']):
                print(f'  [RESP {resp.status}] {u[:100]}')

        page.on('request', on_req)
        page.on('response', on_resp)

        try:
            await page.goto(url, wait_until='domcontentloaded', timeout=20000)
        except Exception as e:
            print('goto 出错:', e)
        await asyncio.sleep(8)

        title = await page.title()
        print('TITLE:', title)

        # 检测登录墙
        body_text = await page.evaluate('() => document.body ? document.body.innerText.slice(0,500) : ""')
        print('BODY前500字:', repr(body_text[:500]))

        # 检测 video 元素
        vids = await page.evaluate('''() => Array.from(document.querySelectorAll("video")).map(v => ({
            src: v.src,
            currentSrc: v.currentSrc,
            hasBlob: v.src.startsWith("blob:")
        }))''')
        print('VIDEO元素数量:', len(vids))
        for v in vids[:5]:
            print('  video:', v)

        # 检测播放按钮/封面
        has_play = await page.evaluate('''() => {
            const txt = document.body ? document.body.innerText : "";
            return txt.includes("登录") || txt.includes("登录后") || txt.includes("该笔记已被删除") || txt.includes("内容不存在");
        }''')
        print('疑似登录墙/删除提示:', has_play)

        # 输出所有媒体相关请求
        print(f'\n媒体相关请求数: {len(reqs_media)}')
        for u in reqs_media[:20]:
            print('  ', u[:120])

        await ctx.close()

    print('\n总请求数:', len(reqs_all))
    # 列出所有包含常见视频域名的请求
    vid_domains = [u for u in reqs_all if any(d in u for d in ['sns-video', 'xhscdn', 'v.xhscdn', '.mp4', 'playback'])]
    print('含视频域名请求:', len(vid_domains))
    for u in vid_domains[:20]:
        print('  ', u[:140])


asyncio.run(main())
