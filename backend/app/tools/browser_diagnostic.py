import argparse, asyncio, logging
from pathlib import Path
from ..browser import BROWSER
from ..browser.diagnostics import inspect_dom, safe_url, safe_text, sanitize_html
from ..config import DEBUG_DIR

async def run(url: str, output: Path | None=None, authenticated: bool=False):
    output=output or DEBUG_DIR/"browser"
    output.mkdir(parents=True,exist_ok=True)
    page=await BROWSER.new_page(authenticated=authenticated);console=[];network=[]
    page.on("console",lambda message:console.append(f"{message.type}: {safe_text(message.text)}") if message.type=="error" else None)
    page.on("pageerror",lambda exc:console.append(f"pageerror: {safe_text(str(exc))}"))
    page.on("response",lambda response:network.append(f"{response.status} {safe_url(response.url)}"))
    try:
        response=await page.goto(url,wait_until="domcontentloaded",timeout=45000)
        await page.wait_for_timeout(1000)
        data=await inspect_dom(page)
        (output/"page.html").write_text(sanitize_html(data["html"]),encoding="utf-8")
        await page.screenshot(path=str(output/"screenshot.png"),full_page=True)
        (output/"console.log").write_text("\n".join(console),encoding="utf-8")
        (output/"network.log").write_text("\n".join(network),encoding="utf-8")
        print(f"HTTP response: {response.status if response else 'none'}")
        print(f"Final URL: {safe_url(page.url)}")
        print(f"Browser session: {'authenticated profile' if authenticated else 'clean public context'}")
        print(f"Title: {safe_text(data['title'])}")
        print(f"HTML length: {len(data['html'])}")
        print(f"JavaScript console errors: {len(console)}")
        print(f"Saved diagnostics: {output}")
        return data
    finally:
        await page.close();await BROWSER.close()

def main():
    parser=argparse.ArgumentParser(description="Capture sanitized diagnostics from a page opened in the local browser profile")
    parser.add_argument("url");parser.add_argument("--output",type=Path)
    parser.add_argument("--authenticated",action="store_true",help="Use the persistent manual-login browser profile")
    args=parser.parse_args();asyncio.run(run(args.url,args.output,args.authenticated))
if __name__=="__main__":main()
