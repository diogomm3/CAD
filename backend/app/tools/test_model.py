import argparse, asyncio
from ..scrapers.registry import SOURCES

def model_url(source,model_id):
    if model_id.startswith("https://"):return model_id
    patterns={"printables":f"https://www.printables.com/model/{model_id}","makerworld":f"https://makerworld.com/en/models/{model_id}","grabcad":f"https://grabcad.com/library/{model_id}"}
    return patterns[source]

async def run(key,model_id):
    source=SOURCES[key];model=await source.get_model_details(model_url(key,model_id));files=await source.get_downloads(model)
    print(f"Source: {source.label}\nTitle: {model.title}\nAuthor: {model.author or 'unknown'}\nURL: {model.model_url}\nFiles: {len(files)}")
    for file in files:print(f"- {file.name} [{file.category}] downloadable={file.downloadable}")

def main():
    parser=argparse.ArgumentParser();parser.add_argument("source",choices=SOURCES);parser.add_argument("model_id");args=parser.parse_args()
    asyncio.run(run(args.source,args.model_id))

if __name__=="__main__":main()
