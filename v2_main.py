import asyncio

from telegram import Bot

import main as radar
from smx2_v2 import engine


async def main_async() -> None:
    if not radar.BOT_TOKEN:
        raise RuntimeError("缺少 BOT_TOKEN")
    if not radar.CHAT_ID:
        raise RuntimeError("缺少 CHAT_ID")
    if not radar.DATABASE_URL:
        raise RuntimeError("缺少 DATABASE_URL 或 DATABASE_PUBLIC_URL")
    if engine.V2_ENGINE not in engine.VALID_ENGINES or engine.V2_ENGINE == "v1":
        raise RuntimeError(f"v2_main requires SMX2_ENGINE=v2_shadow|v2_pilot|v2_full, got {engine.V2_ENGINE}")

    engine.validate_schedule()
    radar.init_db()
    engine.init_v2_db()

    bot = Bot(token=radar.BOT_TOKEN)
    await bot.initialize()

    try:
        await bot.delete_webhook(drop_pending_updates=True)
    except Exception as e:
        print("[v2] delete_webhook failed, ignored:", e)

    print("[startup] smx2 V2 started")
    print("[startup] chat:", radar.CHAT_ID)
    print("[startup] diagnostics:", engine.diagnostics())
    print("不会使用 getUpdates，不会产生 polling 冲突")

    try:
        await engine.v2_scheduler_loop(bot)
    finally:
        await bot.shutdown()


def main() -> None:
    asyncio.run(main_async())


if __name__ == "__main__":
    main()
