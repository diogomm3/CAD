import argparse, asyncio, re
from ..browser import BROWSER
from ..browser.diagnostics import inspect_dom, safe_url, safe_text

PATTERN=re.compile(r"/library/|/models?/|/thing:\d+|/model/\d+",re.I)

async def run(url: str,authenticated: bool=False):
    page=await BROWSER.new_page(authenticated=authenticated)
    try:
        response=await page.goto(url,wait_until="domcontentloaded",timeout=45000)
        await page.wait_for_timeout(1000)
        data=await inspect_dom(page)
        print(f"HTTP: {response.status if response else 'none'} | Final URL: {safe_url(page.url)} | Title: {safe_text(data['title'])}")
        print(f"Links: {len(data['links'])} | Images: {data['images']} | Buttons: {data['buttons']} | Inputs: {data['inputs']}")
        matches=[link for link in data["links"] if PATTERN.search(link["href"])]
        print(f"\nPotential model links: {len(matches)}")
        for link in matches[:100]:print(f"  {safe_url(link['href'])}\n    {safe_text(link['text'])}")
        print("\nVisible page text:")
        print(safe_text(data["bodyText"]).replace("\n"," ")[:1000])
        return data
    finally:
        await page.close();await BROWSER.close()

def main():
    parser=argparse.ArgumentParser(description="Inspect rendered links and likely model routes")
    parser.add_argument("url");parser.add_argument("--authenticated",action="store_true");args=parser.parse_args();asyncio.run(run(args.url,args.authenticated))
if __name__=="__main__":main()
