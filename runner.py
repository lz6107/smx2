import asyncio
import os
from datetime import datetime, timedelta
from pathlib import Path

from telegram import Bot

import main as radar
import market_video


VIDEO_CHECK_INTERVAL_SECONDS = 30


def env_bool(name: str, default: str = "false") -> bool:
    return os.getenv(name, default).strip().lower() in {"1", "true", "yes", "on"}


def env_int(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(os.getenv(name, str(default)).strip())
    except Exception:
        value = default
    return max(minimum, min(maximum, value))


VIDEO_MISSED_GRACE_MINUTES = env_int("MARKET_VIDEO_MISSED_GRACE_MINUTES", 60, 20, 180)
VIDEO_STARTUP_DELAY_SECONDS = env_int("MARKET_VIDEO_STARTUP_DELAY_SECONDS", 20, 0, 120)
VIDEO_TEST_ON_STARTUP = env_bool("MARKET_VIDEO_TEST_ON_STARTUP", "false")


def init_market_video_db():
    radar.execute("""
        CREATE TABLE IF NOT EXISTS market_video_log (
            id SERIAL PRIMARY KEY,
            post_key TEXT UNIQUE NOT NULL,
            slot_time TEXT NOT NULL,
            status TEXT NOT NULL,
            headline TEXT,
            video_path TEXT,
            error TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
    """)


def video_post_key(slot_time: str) -> str:
    return f"{radar.today_key()}:{market_video.market_video_slot_key(slot_time)}"


def video_post_exists(slot_time: str) -> bool:
    row = radar.fetch_one(
        "SELECT id FROM market_video_log WHERE post_key=%s;",
        (video_post_key(slot_time),),
    )
    return bool(row)


def record_video_post(slot_time: str, status: str, headline: str = "", video_path: str = "", error: str = ""):
    radar.execute(
        """
        INSERT INTO market_video_log(post_key, slot_time, status, headline, video_path, error)
        VALUES (%s, %s, %s, %s, %s, %s)
        ON CONFLICT(post_key) DO NOTHING;
        """,
        (
            video_post_key(slot_time),
            slot_time,
            status,
            headline[:300],
            video_path[:500],
            error[:800],
        ),
    )


def describe_next_market_video_slot() -> str:
    now = radar.now_local()

    for slot_time in market_video.MARKET_VIDEO_POST_TIMES:
        if video_post_exists(slot_time):
            continue

        scheduled = radar.scheduled_datetime_today(slot_time)
        grace_until = scheduled + timedelta(minutes=VIDEO_MISSED_GRACE_MINUTES)

        if now < scheduled:
            return f"next={slot_time} now={now.strftime('%H:%M:%S')}"

        if scheduled <= now <= grace_until:
            return f"due={slot_time} now={now.strftime('%H:%M:%S')} grace_until={grace_until.strftime('%H:%M:%S')}"

    return f"no remaining slot today now={now.strftime('%H:%M:%S')}"


def find_due_market_video_time():
    now = radar.now_local()

    for slot_time in market_video.MARKET_VIDEO_POST_TIMES:
        if video_post_exists(slot_time):
            continue

        scheduled = radar.scheduled_datetime_today(slot_time)
        grace_until = scheduled + timedelta(minutes=VIDEO_MISSED_GRACE_MINUTES)

        if now < scheduled:
            return None

        if scheduled <= now <= grace_until:
            return slot_time

        if now > grace_until:
            print(
                "[market-video] skip missed slot",
                slot_time,
                "now=",
                now.strftime("%H:%M:%S"),
                "grace_until=",
                grace_until.strftime("%H:%M:%S"),
            )
            record_video_post(slot_time, "skipped", error="MISSED_BY_VIDEO_SCHEDULER")
            continue

    return None


async def send_video_to_channel(bot: Bot, video_path: str, caption: str):
    with open(video_path, "rb") as f:
        await bot.send_video(
            chat_id=radar.CHAT_ID,
            video=f,
            caption=radar.safe_caption(caption),
            supports_streaming=True,
            width=market_video.MARKET_VIDEO_WIDTH,
            height=market_video.MARKET_VIDEO_HEIGHT,
            duration=market_video.MARKET_VIDEO_DURATION_SECONDS,
        )


def cleanup_video_file(video_path: str):
    if market_video.MARKET_VIDEO_KEEP_FILES:
        return
    try:
        Path(video_path).unlink(missing_ok=True)
    except Exception as e:
        print("[market-video] cleanup temporary file failed:", e)


async def build_and_send_market_video(bot: Bot, slot_time: str):
    result = {}
    try:
        print(f"[market-video] generating slot={slot_time}")
        result = await asyncio.to_thread(
            market_video.build_market_video,
            radar.SYMBOLS,
            radar.SYMBOL_DISPLAY,
        )

        await send_video_to_channel(bot, result["video_path"], result["caption"])
        record_video_post(
            slot_time,
            "sent",
            headline=result.get("headline", ""),
            video_path=result.get("video_path", ""),
        )
        print(f"[market-video] sent slot={slot_time} headline={result.get('headline', '')}")
    except Exception as e:
        record_video_post(slot_time, "failed", error=str(e))
        print(f"[market-video] failed slot={slot_time}: {e}")
    finally:
        cleanup_video_file(result.get("video_path", ""))


async def market_video_loop(bot: Bot):
    if not market_video.ENABLE_MARKET_VIDEOS:
        print("[market-video] disabled: ENABLE_MARKET_VIDEOS is not true")
        return

    await asyncio.sleep(VIDEO_STARTUP_DELAY_SECONDS)
    print("[market-video] enabled")
    print("[market-video] slots:", ", ".join(market_video.MARKET_VIDEO_POST_TIMES))
    print("[market-video] missed grace minutes:", VIDEO_MISSED_GRACE_MINUTES)
    print("[market-video] startup status:", describe_next_market_video_slot())

    if VIDEO_TEST_ON_STARTUP and not video_post_exists("startup"):
        await build_and_send_market_video(bot, "startup")

    while True:
        try:
            slot_time = find_due_market_video_time()
            if slot_time:
                await build_and_send_market_video(bot, slot_time)

        except Exception as e:
            print("[market-video] loop error:", e)

        await asyncio.sleep(VIDEO_CHECK_INTERVAL_SECONDS)


async def main_async():
    if not radar.BOT_TOKEN:
        raise RuntimeError("缺少 BOT_TOKEN")

    if not radar.CHAT_ID:
        raise RuntimeError("缺少 CHAT_ID")

    if not radar.DATABASE_URL:
        raise RuntimeError("缺少 DATABASE_URL 或 DATABASE_PUBLIC_URL")

    radar.init_db()
    init_market_video_db()

    bot = Bot(token=radar.BOT_TOKEN)
    await bot.initialize()

    try:
        await bot.delete_webhook(drop_pending_updates=True)
    except Exception as e:
        print("delete_webhook 失败，可忽略:", e)

    print("[startup] smx2 enhanced runner started")
    print("[startup] chat:", radar.CHAT_ID)
    print("[startup] fixed posts per day:", radar.DAILY_FIXED_POSTS)
    print("[startup] alert check minutes:", radar.PRICE_CHECK_INTERVAL_SECONDS // 60)
    print("[startup] market video enabled:", market_video.ENABLE_MARKET_VIDEOS)
    if market_video.ENABLE_MARKET_VIDEOS:
        print("[startup] market video slots:", ", ".join(market_video.MARKET_VIDEO_POST_TIMES))

    tasks = [
        radar.fixed_schedule_loop(bot),
        radar.alerts_loop(bot),
        market_video_loop(bot),
    ]

    try:
        await asyncio.gather(*tasks)
    finally:
        await bot.shutdown()


def main():
    asyncio.run(main_async())


if __name__ == "__main__":
    main()

