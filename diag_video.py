import asyncio, sys
from playwright.async_api import async_playwright

NOTE = "https://www.xiaohongshu.com/explore/6a5d62670000000014006d25?xsec_token=ABptOl0N30gDF7CmqPmaGCO2O83W0rOA2RI_oSjtN24YY=&xsec_source=pc_feed"

def log(*a):
    print(*a, flush=True)

async def main():
    log("launching browser...")
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, proxy=None, args=["--no-proxy-server"])
        ctx = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/149.0.0.0 Safari/537.36 Edg/149.0.0.0",
            viewport={"width": 1920, "height": 1080},
        )
        page = await ctx.new_page()
        all_reqs, video_reqs = [], []
        def on_req(req):
            u = req.url
            all_reqs.append(u)
            if any(k in u for k in ["sns-video", ".mp4?", "/stream/", "m3u8", "playback", "video"]):
                video_reqs.append(u)
        page.on("request", on_req)
        log("goto (commit)...")
        try:
            resp = await asyncio.wait_for(page.goto(NOTE, wait_until="commit", timeout=25000), timeout=30)
            log("nav status:", resp.status if resp else None)
        except Exception as e:
            log("goto error:", repr(e))
            await browser.close()
            return
        log("sleeping 10s to capture requests...")
        await asyncio.sleep(10)
        # 打印请求统计（不调用会卡住的 page.content()）
        log("page.url:", page.url)
        log("total requests:", len(all_reqs))
        log("video-ish requests:", len(video_reqs))
        for v in video_reqs[:15]:
            log("  VIDEO:", v[:140])
        media = [u for u in all_reqs if any(k in u for k in ["video", "mp4", "m3u8", "play", "stream", "media", "blob:"])]
        log("media-ish (first 15):")
        for m in media[:15]:
            log("   ", m[:140])
        # 打印所有请求的域名分布（前 40 个）
        log("first 40 requests:")
        for u in all_reqs[:40]:
            log("   ", u[:140])
        await browser.close()
        log("done")

asyncio.run(asyncio.wait_for(main(), timeout=80))
