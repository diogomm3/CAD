import re
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from bs4 import BeautifulSoup
from playwright.async_api import Page

SECRET_KEY=re.compile(r"password|passwd|secret|token|authorization|cookie|session",re.I)
SECRET_VALUE=re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._~+/-]+=*|((?:access|refresh|api)[_-]?token|password|secret|session(?:id)?)\s*[:=]\s*[\"']?[^\s\"'&,;<>]+")

def safe_url(url: str) -> str:
    parts=urlsplit(url)
    query=[(key,"[redacted]" if SECRET_KEY.search(key) else value) for key,value in parse_qsl(parts.query,keep_blank_values=True)]
    return urlunsplit((parts.scheme,parts.netloc,parts.path,urlencode(query),""))

def safe_text(value: str) -> str:
    return SECRET_VALUE.sub(lambda match:(match.group(1) or match.group(2))+"[redacted]",value)

def sanitize_html(html: str) -> str:
    soup=BeautifulSoup(html,"lxml")
    for node in soup.select('input[type="password"], input[name*="token" i], input[name*="secret" i], input[name*="auth" i], input[name*="cookie" i]'):
        node.decompose()
    for node in soup.select("input,textarea"):
        if node.has_attr("value"):node["value"]="[redacted]"
        if node.name=="textarea":node.string="[redacted]"
    for node in soup.select("script"):
        if node.string:node.string=safe_text(node.string)
    for node in soup.find_all(True):
        for attr in ("href","src","content","action"):
            if node.has_attr(attr) and isinstance(node[attr],str):node[attr]=safe_url(node[attr]) if attr in {"href","src","action"} else safe_text(node[attr])
    return str(soup)

async def inspect_dom(page: Page) -> dict:
    return await page.evaluate("""() => ({
      title: document.title,
      html: document.documentElement.outerHTML,
      links: Array.from(document.querySelectorAll('a[href]')).map(a => ({href:a.href, text:(a.innerText || a.getAttribute('aria-label') || a.title || '').trim().slice(0,240)})),
      images: document.images.length,
      buttons: document.querySelectorAll('button,[role=button]').length,
      inputs: document.querySelectorAll('input,textarea,select').length,
      bodyText: (document.body?.innerText || '').slice(0,1000)
    })""")
