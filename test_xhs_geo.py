#!/usr/bin/env python3
"""
GHA Geo 限制测试：用已缓存的 note_id + xsec_token 直接访问回放页，能否拿到视频URL？

只测试 3 场比赛，验证境外 IP (GHA Azure) 访问小红书回放页是否被限制。
"""

import asyncio
import json
import re
import sys
from pathlib import Path

# 硬编码 3 个已确认的比赛数据（从本地 calendar_info 提取）
TEST_MATCHES = [
    {
        "note_id": "6a2b3526000000000701093f",
        "xsec_token": "ABIoGoQbsu6RBFHW1MGptGm02NSsjStReKLvYgBMGUrGM=",
        "name": "墨西哥 vs 南非"
    },
    {
        "note_id": "6a2b8b570000000007020226",
        "xsec_token": "ABIoGoQbsu6RBFHW1MGptGm3fXMRcOrAco2jx8TdVsIQA=",
        "name": "韩国 vs 捷克"
    },
    {
        "note_id": "6a2c7ccb00000000210214ba",
        "xsec_token": "ABm0qIoovjvehvk1ZQtbxukbuxJr2EDayK_HA6uSWJpu0=",
        "name": "加拿大 vs 波黑"
    },
]


async def fetch_one(page, match):
    """访问单个回放页，拦截视频URL"""
    note_id = match["note_id"]
    xsec_token = match["xsec_token"]
    name = match["name"]

    url = f"https://www.xiaohongshu.com/explore/{note_id}?xsec_token={xsec_token}&xsec_source=pc_feed"

    video_urls = []

    async def on_response(res):
        if "sns-video" in res.url and ".mp4" in res.url:
            video_urls.append(res.url)

    page.on("response", on_response)

    try:
        await page.goto(url, timeout=30000, wait_until="load")
        # 等待页面完全加载，视频链接通常需要 JS 渲染
        await asyncio.sleep(3)

        # 再等待一下，看有没有视频请求
        await asyncio.sleep(2)

    except Exception as e:
        print(f"  [ERR] 页面加载异常: {e}")
    finally:
        page.remove_listener("response", on_response)

    return video_urls


async def main():
    from playwright.async_api import async_playwright

    print("=" * 60)
    print("GHA Geo 限制测试")
    print("测试: 用已缓存的 note_id 直接访问回放页获取视频 URL")
    print("=" * 60)
    print()

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()

        results = []
        ok = 0

        for i, m in enumerate(TEST_MATCHES):
            print(f"[{i + 1}/{len(TEST_MATCHES)}] {m['name']}")
            print(f"  note_id: {m['note_id']}")
            print(f"  URL: explore/{m['note_id']}?xsec_token=...")

            urls = await fetch_one(page, m)

            if urls:
                print(f"  ✅ 成功！获取到 {len(urls)} 个视频URL:")
                for u in urls:
                    print(f"     {u[:120]}")
                ok += 1
            else:
                print(f"  ❌ 失败！未获取到视频URL")

            results.append({"match": m["name"], "note_id": m["note_id"], "urls": urls})
            print()

        await browser.close()

    # 打印总结
    print("=" * 60)
    print(f"结果: {ok}/{len(TEST_MATCHES)} 成功")
    print("=" * 60)

    if ok > 0:
        print("\n✅ 结论: GHA 可以直接访问回放页获取视频URL!")
        print("   混合方案可行: 赛程列表存仓库，GHA 每5分钟刷新视频URL")
    else:
        print("\n❌ 结论: GHA 无法访问回放页，需要 Plan B 完整方案")

    # 写出结果文件
    output = {
        "test_time": "GHA auto",
        "total": len(TEST_MATCHES),
        "success": ok,
        "results": results,
        "verdict": "PASS" if ok > 0 else "FAIL"
    }
    Path("xhs_debug").mkdir(exist_ok=True)
    with open("xhs_debug/geo_test_result.json", "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    return 0 if ok > 0 else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
