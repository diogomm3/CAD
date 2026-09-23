"""Exercise the full live adapter path through one verified test download."""
import argparse
import asyncio
import hashlib
import tempfile
from pathlib import Path

from ..scrapers.registry import SOURCES
from ..services.download_service import _validate_payload

workflow_stage="search"


async def run(source_key: str, query: str):
    source=SOURCES[source_key]
    global workflow_stage
    workflow_stage="search"
    print("="*50, f"\nSource: {source.label}\nQuery: {query}\n"+"="*50)
    models=await source.search(query,limit=10)
    if not models:raise RuntimeError("Search returned no results")
    print(f"Search:             OK\nResults:            {len(models)}")
    workflow_stage="model details"
    details=await source.get_model_details(models[0].model_url)
    workflow_stage="file discovery"
    files=await source.get_downloads(details)
    downloadable=[item for item in files if item.downloadable and item.url]
    print("Model details:      OK")
    print(f"Files discovered:   {len(files)}")
    for category in ("STL","3MF","CAD","SOURCE"):
        print(f"{category + ':':20}{sum(item.category==category for item in files)}")
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
    args=parser.parse_args()
    try:asyncio.run(run(args.source,args.query))
    except Exception as exc:
        print(f"\nCOMPLETE END-TO-END TEST: FAIL\nStage: {workflow_stage} — {type(exc).__name__}: {exc}")
        raise SystemExit(1)


if __name__=="__main__":main()
