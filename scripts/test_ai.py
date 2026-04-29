import asyncio
import os
import sys

from bot.services.ai_service import get_ai_response

async def main():
    print("Testing AI service...")
    try:
        response = await get_ai_response("Привет, как считается ИПН в 2026 году?")
        print("\n--- AI Response ---")
        print(response)
        print("-------------------\n")
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    asyncio.run(main())
