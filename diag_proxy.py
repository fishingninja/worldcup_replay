import asyncio
from playwright.async_api import async_playwright

NOTE = "https://www.xiaohongshu.com/explore/6a5d62670000000014006d25?xsec_token=ABptOl0N30gDF7CmqPmaGCO2O83W0rOA2RI_oSjtN24YY=&xsec_source=pc_feed"
IP = "81.69.116.86"

async def test(label, url, args, wait="commit", timeout=60000):
    print(f"\n===== {label} =====")
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, proxy=None, args=args)
        ctx = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/149.0.0.0 Safari/537.36 Edg/149.0.0.0",
            ignore_https_errors=True,
        )
        page = await ctx.new_page()
        responses = []
        page.on("response", lambda r: responses.append((r.status, r.url[:70])))
        page.on("requestfailed", lambda r: responses.append(("FAIL", r.url[:70] + " :: " + str(r.failure))))
        try:
            resp = await page.goto(url, wait_until=wait, timeout=timeout)
            st = resp.status if resp else "None"
            title = await page.title()
            body_len = len(await page.content())
            print(f"  status={st} title={title!r} body_len={body_len} events={len(responses)}")
            for ev in responses[:8]:
                print("   ", ev)
        except Exception as e:
            print(f"  ERROR: {e}")
            print(f"  events({len(responses)}):")
            for ev in responses[:8]:
                print("   ", ev)
        await ctx.close()

async def main():
    # 1) force IPv4 via host-resolver-rules, no proxy
    await test("A: IPv4 forced (host-resolver-rules) no-proxy", NOTE,
               ["--no-proxy-server", "--host-resolver-rules=MAP www.xiaohongshu.com " + IP])
    # 2) raw IP with Host via header (SNI still IP though) - connection test
    await test("B: raw IP " + IP + " ignore-cert", f"https://{IP}/",
               ["--no-proxy-server", "--disable-ipv6"])
    # 3) default (let Chromium pick, no flags) - might use IE proxy
    await test("C: default no flags", NOTE, [])

asyncio.run(main())
