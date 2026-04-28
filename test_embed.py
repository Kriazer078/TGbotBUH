import asyncio
import os
from openai import AsyncOpenAI
from dotenv import load_dotenv

load_dotenv()

client = AsyncOpenAI(
    api_key=os.getenv("OPENROUTER_API_KEY"),
    base_url="https://openrouter.ai/api/v1"
)

async def test():
    try:
        response = await client.embeddings.create(
            input="тест",
            model="openai/text-embedding-3-small"
        )
        print("SUCCESS")
    except Exception as e:
        print(f"FAILED: {e}")

asyncio.run(test())
