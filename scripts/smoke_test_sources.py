"""Manual live smoke test. This is intentionally not part of the unit suite."""
import asyncio, sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"backend"))
from app.scrapers.registry import SOURCES
from app.scrapers.errors import SourceError

async def main(query="thor hammer"):
    for key,source in SOURCES.items():
        print("="*50);print(source.label);print("="*50)
        try:
            results=await source.search(query,10)
            print("Status:","OK" if results else "SUCCESS_EMPTY");print("Results:",len(results))
            if results:
                first=results[0]
                print("First:");print("  Title:",first.title);print("  Author:",first.author or "unknown");print("  URL:",first.model_url)
                detail=await source.get_model_details(first.model_url)
                files=await source.get_downloads(detail)
                print("  Files:",", ".join(f"{file.name} ({file.category})" for file in files) or "none discovered")
        except SourceError as exc:print("Status:",exc.status);print("Error:",exc.code,exc.message)
        except Exception as exc:print("Status: ERROR");print("Error:",type(exc).__name__,str(exc))

if __name__=="__main__":asyncio.run(main(sys.argv[1] if len(sys.argv)>1 else "thor hammer"))
