"""Open a visible persistent browser so the user can sign in manually."""
from .login_source import main
import asyncio

if __name__ == "__main__":
    asyncio.run(main())
