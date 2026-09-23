import argparse, asyncio
from pathlib import Path
from ..models import ModelResult
from ..scrapers.registry import SOURCES
from ..tools.test_model import model_url
from ..services.download_service import download_model

async def run(key,model_id,destination):
    source=SOURCES[key];url=model_url(key,model_id)
    detail=await source.get_model_details(url);files=await source.get_downloads(detail)
    model=detail.model_copy(update={"available_files":files})
    destination=Path(destination).expanduser().resolve();destination.mkdir(parents=True,exist_ok=True)
    result=await download_model(model,destination,"Adapter smoke download","v1")
    print(f"Status: {result['status']}\nFolder: {destination}\nFiles: {len(result['files'])}")

def main():
    parser=argparse.ArgumentParser();parser.add_argument("source",choices=SOURCES);parser.add_argument("model_id");parser.add_argument("destination");args=parser.parse_args()
    asyncio.run(run(args.source,args.model_id,args.destination))

if __name__=="__main__":main()
