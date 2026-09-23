import argparse, asyncio
from ..scrapers.registry import SOURCES
from ..scrapers.errors import SourceError

async def run(source_key,query):
    source=SOURCES[source_key]
    try:
        results=await source.search(query,10)
        print(f"Source: {source.label}\nStatus: {'success' if results else 'success_empty'}\nResults: {len(results)}")
        for i,model in enumerate(results,1):print(f"{i}. {model.title} — {model.author or 'unknown'} — {model.model_url}")
        if results:
            detail=await source.get_model_details(results[0].model_url)
            files=await source.get_downloads(detail)
            print("First result files:",", ".join(f.name for f in files) or "none discovered")
    except SourceError as exc:
        print(f"Source: {source.label}\nStatus: {exc.status}\nError: {exc.code}: {exc.message}")

def main():
    parser=argparse.ArgumentParser();parser.add_argument("source",choices=SOURCES);parser.add_argument("query");args=parser.parse_args()
    asyncio.run(run(args.source,args.query))

if __name__=="__main__":main()
