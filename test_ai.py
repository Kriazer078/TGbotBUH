import asyncio
import os
import sys
import codecs

sys.stdout = codecs.getwriter('utf-8')(sys.stdout.detach())
from dotenv import load_dotenv

load_dotenv()

from bot.services.ai_service import get_ai_response

async def main():
    try:
        res = await get_ai_response("кто ты")
        print("RESULT:", res)
    except Exception as e:
        print("EXCEPTION:", e)

if __name__ == "__main__":
    asyncio.run(main())
