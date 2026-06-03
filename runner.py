import asyncio
import os
from datetime import datetime, timedelta
from pathlib import Path

from telegram import Bot

import main as radar
import market_video


VIDEO_CHECK_INTERVAL_SECONDS = 30
VIDEO_MISSED_GRACE_MINUTES = 20


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
            print(f"跳过错过的行情视频：{slot_time}")
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
        print("清理行情视频临时文件失败:", e)


async def market_video_loop(bot: Bot):
    if not market_video.ENABLE_MARKET_VIDEOS:
        print("横屏行情视频：未开启 ENABLE_MARKET_VIDEOS")
        return

    await asyncio.sleep(20)
    print("横屏行情视频调度已启动:", ", ".join(market_video.MARKET_VIDEO_POST_TIMES))

    while True:
        try:
            slot_time = find_due_market_video_time()
            if slot_time:
                print(f"准备生成横屏行情视频：{slot_time}")
                result = await asyncio.to_thread(
                    market_video.build_market_video,
                    radar.SYMBOLS,
                    radar.SYMBOL_DISPLAY,
                )

                try:
                    await send_video_to_channel(bot, result["video_path"], result["caption"])
                    record_video_post(
                        slot_time,
                        "sent",
                        headline=result.get("headline", ""),
                        video_path=result.get("video_path", ""),
                    )
                    print(f"横屏行情视频已发送：{slot_time} {result.get('headline', '')}")
                finally:
                    cleanup_video_file(result.get("video_path", ""))

        except Exception as e:
            print("横屏行情视频处理失败:", e)
            try:
                slot_time = find_due_market_video_time()
                if slot_time:
                    record_video_post(slot_time, "failed", error=str(e))
            except Exception as log_error:
                print("横屏行情视频失败日志写入失败:", log_error)

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

    print("石墨烯雷达增强入口启动成功")
    print("频道:", radar.CHAT_ID)
    print("固定栏目:", radar.DAILY_FIXED_POSTS, "条/天")
    print("行情检查间隔:", radar.PRICE_CHECK_INTERVAL_SECONDS // 60, "分钟")
    print("横屏行情视频:", "开启" if market_video.ENABLE_MARKET_VIDEOS else "关闭")
    if market_video.ENABLE_MARKET_VIDEOS:
        print("横屏行情视频时间:", ", ".join(market_video.MARKET_VIDEO_POST_TIMES))

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

