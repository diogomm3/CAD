import asyncio
from ..browser import BROWSER

async def run():
    page=await BROWSER.new_page()
    try:
        response=await page.goto("https://example.com",wait_until="domcontentloaded",timeout=30000)
        title=await page.title()
        text=await page.locator("body").inner_text()
        ok=bool(response and response.status==200 and "Example Domain" in title and "Example Domain" in text)
        print("Browser smoke test\n")
        print("Chromium: OK")
        print("JavaScript: OK" if await page.evaluate("() => Boolean(document.readyState && location.hostname)") else "JavaScript: failed")
        print("Navigation: OK" if response and response.status==200 else f"Navigation: HTTP {response.status if response else 'none'}")
        print("DOM access: OK" if "Example Domain" in text else "DOM access: failed")
        if not ok:raise SystemExit(1)
    finally:
        await page.close();await BROWSER.close()

def main():asyncio.run(run())
if __name__=="__main__":main()
