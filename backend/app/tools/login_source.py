"""Open a normal visible browser for a user to log in manually."""
import argparse, asyncio, os

URLS={"printables":"https://www.printables.com/","makerworld":"https://makerworld.com/","thingiverse":"https://www.thingiverse.com/","grabcad":"https://grabcad.com/library"}

async def main():
    parser=argparse.ArgumentParser(description="Open the persistent profile for manual website login")
    parser.add_argument("source",choices=URLS)
    args=parser.parse_args()
    os.environ["BROWSER_HEADLESS"]="false"
    from ..browser import BROWSER
    context=await BROWSER.context()
    page=await context.new_page()
    await page.goto(URLS[args.source],wait_until="domcontentloaded")
    print("Log in manually in the browser window. Close the window or press Ctrl+C when finished.")
    try:
        while not page.is_closed():await page.wait_for_timeout(1000)
    except KeyboardInterrupt: pass
    finally: await BROWSER.close()

if __name__=="__main__":
    asyncio.run(main())
