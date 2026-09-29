"""Exercise the full live adapter path through one verified test download."""
import argparse
import asyncio
import hashlib
import re
import tempfile
from pathlib import Path
import httpx
from bs4 import BeautifulSoup

from ..scrapers.registry import SOURCES
from ..scrapers.adapters import HTMLSearchSource
from ..services.download_service import _validate_payload
from ..services.bambu_auth import BAMBU_API

workflow_stage="search"


async def run(source_key: str, query: str, inspect_scrapling_page: bool = False, force_scrapling_download: bool = False, force_scrapling_search: bool = False):
    source=SOURCES[source_key]
    global workflow_stage
    workflow_stage="search"
    print("="*50, f"\nSource: {source.label}\nQuery: {query}\n"+"="*50)
    if force_scrapling_search:
        source.config["access_mode"]="scrapling"
        models=await HTMLSearchSource.search(source,query,limit=12)
    else:
        models=await source.search(query,limit=12)
    if not models:raise RuntimeError("Search returned no results")
    print(f"Search:             OK\nResults:            {len(models)}")
    if inspect_scrapling_page:
        response=await source._scrapling_fetch(models[0].model_url)
        soup=BeautifulSoup(response.text,"lxml")
        print(f"Scrapling page:     HTTP {response.status} {response.url}")
        print(f"HTML bytes:         {len(response.body):,}")
        print(f"Title:              {soup.title.get_text(' ',strip=True) if soup.title else 'missing'}")
        for node in soup.select("a[href],button,[role=button]"):
            label=" ".join((node.get_text(" ",strip=True),node.get("href", ""),node.get("aria-label", "")))
            if re.search(r"download|\.(?:stl|3mf|step|stp)(?:\b|$)",label,re.I):
                print(f"Candidate action:   {label[:240]}")
        for match in list(re.finditer(r"modelUrl|modelFileName|fileName|\.stl|\.3mf",response.text,re.I))[:12]:
            print(f"Embedded data:      {response.text[max(0,match.start()-100):match.end()+220]}")
        if source.key=="makerworld":
            signed_previews=re.findall(r'"thumbnailUrl":"(https://makerworld\.bblmw\.com/[^"]+\.stl\.jpeg[^"]*)"',response.text,re.I)
            for preview in signed_previews[:3]:
                preview=preview.replace("\\u0026","&").replace("\\/","/")
                candidate=preview.replace(".stl.jpeg",".stl")
                try:
                    async with httpx.AsyncClient(timeout=15,follow_redirects=False) as client:
                        async with client.stream("GET",candidate,headers={"Range":"bytes=0-1023"}) as probe:
                            chunk=await probe.aread()
                            print(f"Derived STL URL:    HTTP {probe.status_code}; type={probe.headers.get('content-type')}; bytes_read={len(chunk)}; prefix={chunk[:32]!r}")
                except Exception as exc:
                    print(f"Derived STL URL:    {type(exc).__name__}: {exc}")
        if source.key=="makerworld":
            details=await source.get_model_details(models[0].model_url)
            profile=source._profiles_by_model_id.get(models[0].id) or {}
            print(f"MakerWorld files:   {[(item.name,item.size_bytes,item.url) for item in details.available_files]}")
            print(f"Profile metadata:   {[(item.get('profileId'),item.get('title')) for item in profile.get('instances',[])]}")
            if profile.get("model_id") and profile.get("instances"):
                profile_id=profile["instances"][0].get("profileId")
                if profile_id:
                    try:
                        response=await source._get_response(
                            f"{BAMBU_API}/v1/iot-service/api/user/profile/{profile_id}",
                            params={"model_id":str(profile["model_id"])},
                        )
                        print(f"Anonymous profile:  HTTP {response.status_code} {response.text[:300]}")
                    except Exception as exc:
                        print(f"Anonymous profile:  {type(exc).__name__}: {exc}")
        return
    workflow_stage="model details"
    details=await source.get_model_details(models[0].model_url)
    workflow_stage="file discovery"
    files=await source.get_downloads(details)
    downloadable=[item for item in files if item.downloadable and item.url]
    print("Model details:      OK")
    print(f"Files discovered:   {len(files)}")
    for category in ("STL","3MF","CAD","SOURCE"):
        print(f"{category + ':':20}{sum(item.category==category for item in files)}")
    for item in files:
        print(f"File: {item.name} | downloadable={item.downloadable} | url={bool(item.url)} | reason={item.reason or 'none'}")
    if force_scrapling_download:
        selected=next((item for item in files if item.extension.lower()==".stl"),None)
        if not selected:raise RuntimeError("No STL entry was returned by model details")
        with tempfile.TemporaryDirectory(prefix="formfinder-scrapling-test-") as temp_dir:
            target=Path(temp_dir)/Path(selected.name).name
            workflow_stage="Scrapling browser download"
            if selected.url:
                content_type,size=await source._scrapling_download_file(selected,target)
                selected.mime_type=content_type
                selected.size_bytes=size
            else:
                try:
                    await source.download_browser_file(models[0].model_url,selected,target)
                finally:
                    print(f"Scrapling actions:   {getattr(source,'_scrapling_download_actions',[])}")
                    print(f"Scrapling dialog:    {getattr(source,'_scrapling_download_page_text','')}")
                print(f"Scrapling dialog:    {getattr(source,'_scrapling_download_page_text','')}")
            workflow_stage="file validation"
            _validate_payload(target,selected.extension or target.suffix)
            print(f"Scrapling download:  OK ({target.stat().st_size:,} bytes)")
            print("COMPLETE SCRAPLING DOWNLOAD TEST: PASS")
        return
    if not downloadable:raise RuntimeError("Model details loaded, but no downloadable files were discovered")
    selected=downloadable[0]
    with tempfile.TemporaryDirectory(prefix="formfinder-source-test-") as temp_dir:
        target=Path(temp_dir)/Path(selected.name).name
        workflow_stage="test download"
        await source.download_file(selected,target)
        workflow_stage="file validation"
        _validate_payload(target,selected.extension or target.suffix)
        digest=hashlib.sha256()
        with target.open("rb") as inp:
            for chunk in iter(lambda:inp.read(1024*1024),b""):digest.update(chunk)
        print("Test download:      OK")
        print(f"Downloaded:         {target.name}\nSize:               {target.stat().st_size:,} bytes\nSHA256:             {digest.hexdigest()}")
    print("\nCOMPLETE END-TO-END TEST: PASS")


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source",choices=SOURCES)
    parser.add_argument("query")
    parser.add_argument("--inspect-scrapling-page",action="store_true",help="Fetch the first result page with Scrapling and list file/download actions")
    parser.add_argument("--force-scrapling-download",action="store_true",help="Click the first STL download action using Scrapling and validate the file")
    parser.add_argument("--force-scrapling-search",action="store_true",help="Search through the source HTML page using Scrapling instead of its API")
    args=parser.parse_args()
    try:asyncio.run(run(args.source,args.query,args.inspect_scrapling_page,args.force_scrapling_download,args.force_scrapling_search))
    except Exception as exc:
        print(f"\nCOMPLETE END-TO-END TEST: FAIL\nStage: {workflow_stage} — {type(exc).__name__}: {exc}")
        raise SystemExit(1)


if __name__=="__main__":main()
