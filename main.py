import os
import json
import time
import asyncio
from datetime import datetime, timezone, timedelta
from typing import Dict, Optional, List

import requests
import psycopg2
from psycopg2.extras import RealDictCursor

from telegram import Bot


# =========================
# 环境变量
# =========================

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
CHAT_ID = os.getenv("CHAT_ID", "").strip()
DATABASE_URL = (os.getenv("DATABASE_URL") or os.getenv("DATABASE_PUBLIC_URL") or "").strip()

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "").strip()
MODEL_NAME = os.getenv("MODEL_NAME", "gpt-4.1-mini").strip()
ENABLE_AI = os.getenv("ENABLE_AI", "true").lower().strip() == "true"

LOCAL_TZ_OFFSET = int(os.getenv("LOCAL_TZ_OFFSET", "8"))
LOCAL_TZ = timezone(timedelta(hours=LOCAL_TZ_OFFSET))


# =========================
# 正式发布计划
# =========================

DAILY_POST_PLAN = [
    {
        "slot_no": 1,
        "slot_key": "morning_watch_1",
        "time": "08:30",
        "category": "今日盯盘",
        "title": "今日盯盘",
        "image_key": "daily_watch",
        "focus": "早盘盯盘，重点看 BTC、ETH 的主线方向，以及山寨资金有没有外溢。",
    },
    {
        "slot_no": 2,
        "slot_key": "hot_words_1",
        "time": "10:00",
        "category": "热词榜",
        "title": "热词榜",
        "image_key": "hot_words",
        "focus": "上午热词梳理，提炼 BTC、ETH、山寨币、MEME、AI币、资金情绪等关键词。",
    },
    {
        "slot_no": 3,
        "slot_key": "altcoin_radar_1",
        "time": "11:30",
        "category": "山寨雷达",
        "title": "山寨雷达",
        "image_key": "altcoin_radar",
        "focus": "上午山寨观察，重点看 SOL、DOGE、PEPE、WLD、AVAX、LINK 等高弹性方向。",
    },
    {
        "slot_no": 4,
        "slot_key": "sentiment_1",
        "time": "13:00",
        "category": "情绪温度",
        "title": "情绪温度",
        "image_key": "sentiment",
        "focus": "午间情绪判断，重点看市场是偏热、偏冷，还是震荡观察。",
    },
    {
        "slot_no": 5,
        "slot_key": "afternoon_watch_2",
        "time": "14:30",
        "category": "今日盯盘",
        "title": "今日盯盘",
        "image_key": "daily_watch",
        "focus": "午后盯盘，重点看 BTC 是否延续主方向，ETH 和山寨是否跟随。",
    },
    {
        "slot_no": 6,
        "slot_key": "hot_words_2",
        "time": "16:00",
        "category": "热词榜",
        "title": "热词榜",
        "image_key": "hot_words",
        "focus": "下午热词梳理，观察市场注意力是否从主流币扩散到山寨、MEME、AI币。",
    },
    {
        "slot_no": 7,
        "slot_key": "altcoin_radar_2",
        "time": "17:30",
        "category": "山寨雷达",
        "title": "山寨雷达",
        "image_key": "altcoin_radar",
        "focus": "下午山寨观察，重点看山寨是否只是局部活跃，还是出现板块扩散。",
    },
    {
        "slot_no": 8,
        "slot_key": "sentiment_2",
        "time": "19:00",
        "category": "情绪温度",
        "title": "情绪温度",
        "image_key": "sentiment",
        "focus": "晚间前情绪判断，重点看资金是否愿意继续进场，以及追涨风险。",
    },
    {
        "slot_no": 9,
        "slot_key": "night_watch_3",
        "time": "20:00",
        "category": "今日盯盘",
        "title": "今日盯盘",
        "image_key": "daily_watch",
        "focus": "夜盘盯盘，重点看 BTC、ETH、山寨资金的连续性。",
    },
    {
        "slot_no": 10,
        "slot_key": "hot_words_3",
        "time": "20:50",
        "category": "热词榜",
        "title": "热词榜",
        "image_key": "hot_words",
        "focus": "夜盘热词梳理，观察关键词是否集中在主流币、山寨季、MEME、AI币、爆仓清洗。",
    },
    {
        "slot_no": 11,
        "slot_key": "altcoin_radar_3",
        "time": "21:40",
        "category": "山寨雷达",
        "title": "山寨雷达",
        "image_key": "altcoin_radar",
        "focus": "夜盘山寨观察，重点看高弹性标的是否继续活跃，还是开始降温。",
    },
    {
        "slot_no": 12,
        "slot_key": "sentiment_3",
        "time": "22:20",
        "category": "情绪温度",
        "title": "情绪温度",
        "image_key": "sentiment",
        "focus": "夜盘情绪判断，重点看市场是否过热、是否容易洗盘，以及短线风险。",
    },
    {
        "slot_no": 13,
        "slot_key": "night_review_main",
        "time": "23:00",
        "category": "夜间复盘",
        "title": "夜间复盘",
        "image_key": "night_review",
        "focus": "夜间三连第一条：主线行情复盘，重点看 BTC、ETH 和大盘方向。",
    },
    {
        "slot_no": 14,
        "slot_key": "night_review_altcoin",
        "time": "23:08",
        "category": "夜间复盘",
        "title": "山寨复盘",
        "image_key": "altcoin_review",
        "focus": "夜间三连第二条：山寨与情绪复盘，重点看山寨币、MEME、AI币、资金外溢和市场热度。",
    },
    {
        "slot_no": 15,
        "slot_key": "tomorrow_watch",
        "time": "23:16",
        "category": "夜间复盘",
        "title": "明日盯盘",
        "image_key": "tomorrow_watch",
        "focus": "夜间三连第三条：明日盯盘，重点写明天需要观察什么，不要重复前两条。",
    },
]

DAILY_FIXED_POSTS = len(DAILY_POST_PLAN)

SCHEDULE_CHECK_INTERVAL_SECONDS = 30
MISSED_POST_GRACE_MINUTES = 20
MIN_FIXED_POST_GAP_MINUTES = 7

PRICE_CHECK_INTERVAL_SECONDS = 5 * 60
ALERT_COOLDOWN_MINUTES = 90
MAX_ALERTS_PER_CHECK = 1

IMAGE_FILES = {
    "daily_watch": "images/daily_watch.png",
    "hot_words": "images/hot_words.png",
    "altcoin_radar": "images/altcoin_radar.png",
    "sentiment": "images/sentiment.png",
    "night_review": "images/night_review.png",
    "altcoin_review": "images/altcoin_review.png",
    "tomorrow_watch": "images/tomorrow_watch.png",
    "market_alert": "images/market_alert.png",
}


# =========================
# 监控币种
# =========================

SYMBOLS = [
    "BTCUSDT",
    "ETHUSDT",
    "SOLUSDT",
    "BNBUSDT",
    "XRPUSDT",
    "DOGEUSDT",
    "PEPEUSDT",
    "LINKUSDT",
    "AVAXUSDT",
    "WLDUSDT",
]

ALERT_THRESHOLDS_1H = {
    "BTCUSDT": 1.0,
    "ETHUSDT": 1.2,
    "SOLUSDT": 1.8,
    "BNBUSDT": 1.2,
    "XRPUSDT": 1.8,
    "DOGEUSDT": 2.5,
    "PEPEUSDT": 2.5,
    "LINKUSDT": 2.0,
    "AVAXUSDT": 2.0,
    "WLDUSDT": 2.5,
}

SYMBOL_DISPLAY = {
    "BTCUSDT": "BTC",
    "ETHUSDT": "ETH",
    "SOLUSDT": "SOL",
    "BNBUSDT": "BNB",
    "XRPUSDT": "XRP",
    "DOGEUSDT": "DOGE",
    "PEPEUSDT": "PEPE",
    "LINKUSDT": "LINK",
    "AVAXUSDT": "AVAX",
    "WLDUSDT": "WLD",
}


# =========================
# 数据库
# =========================

def db_conn():
    if not DATABASE_URL:
        raise RuntimeError("缺少 DATABASE_URL 或 DATABASE_PUBLIC_URL")
    return psycopg2.connect(DATABASE_URL)


def init_db():
    conn = db_conn()
    cur = conn.cursor()

    cur.execute("""
    CREATE TABLE IF NOT EXISTS posts_log (
        id SERIAL PRIMARY KEY,
        post_key TEXT UNIQUE NOT NULL,
        category TEXT NOT NULL,
        round_no INTEGER NOT NULL,
        slot_key TEXT,
        scheduled_time TEXT,
        status TEXT NOT NULL DEFAULT 'sent',
        content TEXT,
        sent_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    );
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS alerts_log (
        id SERIAL PRIMARY KEY,
        symbol TEXT NOT NULL,
        direction TEXT NOT NULL,
        change_1h DOUBLE PRECISION,
        content TEXT,
        sent_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    );
    """)

    cur.execute("ALTER TABLE posts_log ADD COLUMN IF NOT EXISTS slot_key TEXT;")
    cur.execute("ALTER TABLE posts_log ADD COLUMN IF NOT EXISTS scheduled_time TEXT;")
    cur.execute("ALTER TABLE posts_log ADD COLUMN IF NOT EXISTS status TEXT NOT NULL DEFAULT 'sent';")

    cur.execute("""
    CREATE INDEX IF NOT EXISTS idx_posts_log_sent_at
    ON posts_log(sent_at);
    """)

    cur.execute("""
    CREATE INDEX IF NOT EXISTS idx_posts_log_post_key
    ON posts_log(post_key);
    """)

    cur.execute("""
    CREATE INDEX IF NOT EXISTS idx_alerts_log_symbol_sent_at
    ON alerts_log(symbol, sent_at);
    """)

    conn.commit()
    cur.close()
    conn.close()


def fetch_one(query: str, params=()):
    conn = db_conn()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute(query, params)
    row = cur.fetchone()
    cur.close()
    conn.close()
    return row


def execute(query: str, params=()):
    conn = db_conn()
    cur = conn.cursor()
    cur.execute(query, params)
    conn.commit()
    cur.close()
    conn.close()


def post_exists(post_key: str) -> bool:
    row = fetch_one(
        "SELECT id FROM posts_log WHERE post_key=%s;",
        (post_key,)
    )
    return bool(row)


def record_post(plan: dict, content: str, status: str = "sent"):
    post_key = build_post_key(plan)

    execute(
        """
        INSERT INTO posts_log(
            post_key, category, round_no, slot_key, scheduled_time, status, content
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT(post_key) DO NOTHING;
        """,
        (
            post_key,
            plan["category"],
            plan["slot_no"],
            plan["slot_key"],
            plan["time"],
            status,
            content,
        )
    )


def record_alert(symbol: str, direction: str, change_1h: float, content: str):
    execute(
        """
        INSERT INTO alerts_log(symbol, direction, change_1h, content)
        VALUES (%s, %s, %s, %s);
        """,
        (symbol, direction, change_1h, content)
    )


def last_fixed_sent_at():
    row = fetch_one(
        """
        SELECT sent_at
        FROM posts_log
        WHERE status='sent'
        ORDER BY sent_at DESC
        LIMIT 1;
        """
    )

    if not row:
        return None

    sent_at = row["sent_at"]
    if sent_at.tzinfo is None:
        sent_at = sent_at.replace(tzinfo=timezone.utc)

    return sent_at.astimezone(timezone.utc)


# =========================
# 时间工具
# =========================

def now_local() -> datetime:
    return datetime.now(LOCAL_TZ)


def today_key() -> str:
    return now_local().strftime("%Y%m%d")


def build_post_key(plan: dict) -> str:
    return f"{today_key()}:{plan['slot_key']}"


def scheduled_datetime_today(hhmm: str) -> datetime:
    hour, minute = [int(x) for x in hhmm.split(":")]
    n = now_local()
    return datetime(n.year, n.month, n.day, hour, minute, tzinfo=LOCAL_TZ)


def format_percent(value: Optional[float]) -> str:
    if value is None:
        return "未知"
    sign = "+" if value > 0 else ""
    return f"{sign}{value:.2f}%"


def format_price(price):
    if price is None:
        return "未知"

    try:
        price = float(price)
    except Exception:
        return "未知"

    if price >= 100:
        return f"{price:.2f}"
    if price >= 1:
        return f"{price:.4f}"
    return f"{price:.8f}"


# =========================
# Binance 行情
# =========================

def safe_float(value, default=None):
    try:
        return float(value)
    except Exception:
        return default


def http_get_json(url: str, params=None, timeout=12):
    r = requests.get(url, params=params, timeout=timeout)
    r.raise_for_status()
    return r.json()


def fetch_market_data_sync() -> Dict[str, dict]:
    """
    稳定版：一个币一个币请求。
    避免 Binance 批量 symbols 参数 400 导致整批失败。
    """
    base = "https://api.binance.com"
    result: Dict[str, dict] = {}

    for symbol in SYMBOLS:
        display = SYMBOL_DISPLAY.get(symbol, symbol.replace("USDT", ""))

        result[symbol] = {
            "symbol": symbol,
            "display": display,
            "price": None,
            "change_1h": None,
            "change_24h": None,
            "quote_volume": None,
        }

        try:
            item = http_get_json(
                f"{base}/api/v3/ticker/24hr",
                params={"symbol": symbol},
            )

            result[symbol]["price"] = safe_float(item.get("lastPrice"))
            result[symbol]["change_24h"] = safe_float(item.get("priceChangePercent"))
            result[symbol]["quote_volume"] = safe_float(item.get("quoteVolume"))

        except Exception as e:
            print(f"获取 {symbol} 24h ticker 失败:", e)

        try:
            klines = http_get_json(
                f"{base}/api/v3/klines",
                params={"symbol": symbol, "interval": "1h", "limit": 1},
            )

            if klines:
                k = klines[-1]
                open_price = safe_float(k[1])
                close_price = safe_float(k[4])

                if open_price and close_price:
                    change_1h = (close_price - open_price) / open_price * 100
                else:
                    change_1h = None

                result[symbol]["change_1h"] = change_1h

                if result[symbol]["price"] is None:
                    result[symbol]["price"] = close_price

        except Exception as e:
            print(f"获取 {symbol} 1h kline 失败:", e)

        time.sleep(0.15)

    return result


async def fetch_market_data() -> Dict[str, dict]:
    return await asyncio.to_thread(fetch_market_data_sync)


def market_snapshot_text(market: Dict[str, dict]) -> str:
    lines = []

    for symbol in SYMBOLS:
        item = market.get(symbol)
        if not item:
            continue

        lines.append(
            f"{item['display']}: 价格 {format_price(item.get('price'))}, "
            f"1h {format_percent(item.get('change_1h'))}, "
            f"24h {format_percent(item.get('change_24h'))}"
        )

    return "\n".join(lines)


def top_movers(market: Dict[str, dict], field="change_1h", limit=3, reverse=True) -> List[dict]:
    items = [
        item for item in market.values()
        if item.get(field) is not None
    ]

    items.sort(key=lambda x: x.get(field) or 0, reverse=reverse)
    return items[:limit]


def market_mood(market: Dict[str, dict]) -> str:
    valid = [x for x in market.values() if x.get("change_1h") is not None]
    if not valid:
        return "观察"

    up_count = sum(1 for x in valid if x["change_1h"] > 0)
    down_count = sum(1 for x in valid if x["change_1h"] < 0)

    avg_change = sum(x["change_1h"] for x in valid) / len(valid)

    if avg_change >= 0.4 and up_count >= down_count + 3:
        return "偏热"
    if avg_change <= -0.4 and down_count >= up_count + 3:
        return "偏冷"
    if avg_change > 0:
        return "中性偏热"
    if avg_change < 0:
        return "中性偏冷"
    return "中性"


def build_hot_words(market: Dict[str, dict]) -> List[str]:
    words = ["BTC", "ETH", "山寨币"]

    btc = market.get("BTCUSDT", {})
    eth = market.get("ETHUSDT", {})
    sol = market.get("SOLUSDT", {})
    doge = market.get("DOGEUSDT", {})
    pepe = market.get("PEPEUSDT", {})
    wld = market.get("WLDUSDT", {})

    if abs(btc.get("change_1h") or 0) >= 0.5:
        words.append("比特币异动")

    if abs(eth.get("change_1h") or 0) >= 0.6:
        words.append("以太坊")

    if (sol.get("change_1h") or 0) > 0.8:
        words.append("SOL")

    if (doge.get("change_1h") or 0) > 1.2 or (pepe.get("change_1h") or 0) > 1.2:
        words.append("MEME")

    if (wld.get("change_1h") or 0) > 1.2:
        words.append("AI币")

    for w in ["链上", "合约", "爆仓清洗", "资金情绪", "山寨季"]:
        if w not in words:
            words.append(w)
        if len(words) >= 7:
            break

    return words[:7]


# =========================
# AI 文案
# =========================

AI_SYSTEM_PROMPT = """
你是“石墨烯财经”的Telegram频道文案助手。
你只根据用户给的数据写内容，不能编造具体新闻、巨鲸金额、爆仓金额、交易所公告、链上转账数量。
风格：冷静、有判断、不官方、不喊单、不装神。
必须避免：投资建议、保证收益、必涨必跌、无脑追涨、夸张标题党。
这是正式频道内容，不要出现“测试”“模拟”“第几轮”“轮次”等字样。
每条内容要短，适合Telegram频道，一屏内看完。
输出只给最终频道文案，不要解释。
"""


def extract_openai_text(data: dict) -> Optional[str]:
    if isinstance(data, dict) and data.get("output_text"):
        return data["output_text"].strip()

    try:
        pieces = []
        for item in data.get("output", []):
            for content in item.get("content", []):
                if content.get("type") in {"output_text", "text"}:
                    text = content.get("text")
                    if text:
                        pieces.append(text)
        if pieces:
            return "\n".join(pieces).strip()
    except Exception:
        pass

    return None


def call_openai_sync(prompt: str) -> Optional[str]:
    if not ENABLE_AI:
        return None

    if not OPENAI_API_KEY:
        print("ENABLE_AI=true 但缺少 OPENAI_API_KEY，使用模板兜底")
        return None

    try:
        payload = {
            "model": MODEL_NAME,
            "input": [
                {"role": "system", "content": AI_SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
            "max_output_tokens": 650,
        }

        r = requests.post(
            "https://api.openai.com/v1/responses",
            headers={
                "Authorization": f"Bearer {OPENAI_API_KEY}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=35,
        )

        if r.status_code >= 400:
            print("OpenAI 返回错误:", r.status_code, r.text[:500])
            return None

        data = r.json()
        return extract_openai_text(data)

    except Exception as e:
        print("OpenAI 调用失败:", e)
        return None


async def call_openai(prompt: str) -> Optional[str]:
    return await asyncio.to_thread(call_openai_sync, prompt)


def build_fixed_prompt(plan: dict, market: Dict[str, dict]) -> str:
    up = top_movers(market, "change_1h", 3, True)
    down = top_movers(market, "change_1h", 3, False)
    hot_words = build_hot_words(market)
    mood = market_mood(market)

    return f"""
请生成一条“石墨烯财经”正式频道内容。

硬性要求：
1. 标题必须是：【石墨烯财经｜{plan["title"]}】
2. 不要出现“第几轮”“轮次”“测试”“模拟”等字样
3. 必须有“石墨烯观察：”
4. 必须有“当前状态：”或“市场倾向：”或“明日重点：”
5. 末尾带 2-3 个标签
6. 不要编造新闻、巨鲸金额、爆仓金额、交易所公告
7. 不要投资建议，不要喊单
8. 控制在 420 个中文字以内，适合放在图片 caption 里
9. 语言要像真人在盯盘，不要像公告，不要太干

当前栏目：{plan["category"]}
显示标题：{plan["title"]}
计划时间：{plan["time"]}
本条重点：{plan["focus"]}
当前市场情绪：{mood}

行情数据：
{market_snapshot_text(market)}

短线涨幅靠前：
{json.dumps([{x["display"]: x.get("change_1h")} for x in up], ensure_ascii=False)}

短线跌幅靠前：
{json.dumps([{x["display"]: x.get("change_1h")} for x in down], ensure_ascii=False)}

可用关键词：
{", ".join(hot_words)}

请直接输出频道文案。
""".strip()


def build_alert_prompt(symbol: str, item: dict, market: Dict[str, dict]) -> str:
    display = item["display"]
    direction = "上涨" if (item.get("change_1h") or 0) > 0 else "回落"
    mood = market_mood(market)

    return f"""
请生成一条“石墨烯财经｜行情异动”正式频道内容。

要求：
1. 标题必须是：【石墨烯财经｜行情异动】
2. 说明 {display} 1小时出现{direction}
3. 必须写出 1小时涨跌幅：{format_percent(item.get("change_1h"))}
4. 必须有“石墨烯观察：”
5. 必须有“当前倾向：”
6. 末尾带 2-3 个标签
7. 不要编造新闻、巨鲸金额、爆仓金额
8. 不要投资建议，不要喊单
9. 控制在 320 个中文字以内
10. 不要出现测试、模拟、轮次字样

触发币种：{display}
1小时涨跌：{format_percent(item.get("change_1h"))}
24小时涨跌：{format_percent(item.get("change_24h"))}
当前价格：{item.get("price")}
当前市场情绪：{mood}

其他行情：
{market_snapshot_text(market)}

请直接输出频道文案。
""".strip()


# =========================
# 模板兜底
# =========================

def fallback_fixed_content(plan: dict, market: Dict[str, dict]) -> str:
    title = plan["title"]
    mood = market_mood(market)

    up = top_movers(market, "change_1h", 3, True)
    down = top_movers(market, "change_1h", 3, False)

    up_text = "、".join([f"{x['display']} {format_percent(x.get('change_1h'))}" for x in up]) or "暂无明显领涨"
    down_text = "、".join([f"{x['display']} {format_percent(x.get('change_1h'))}" for x in down]) or "暂无明显回落"

    if title == "今日盯盘":
        return f"""【石墨烯财经｜今日盯盘】

今天重点看三个方向：

1. BTC 是否稳住主方向
2. ETH 能不能跟随放量
3. 山寨方向有没有资金外溢

短线靠前：{up_text}

石墨烯观察：
现在不是看单个币涨多少，而是看资金有没有连续性。如果 BTC 不拖后腿，山寨才有继续表现空间。

当前状态：{mood}
#BTC #ETH #山寨币""".strip()

    if title == "热词榜":
        hot_words = build_hot_words(market)[:5]
        return f"""【石墨烯财经｜热词榜】

当前币圈热词：

1. {hot_words[0]}
2. {hot_words[1]}
3. {hot_words[2]}
4. {hot_words[3]}
5. {hot_words[4]}

石墨烯观察：
热词从主流币扩散到山寨和情绪方向，说明市场注意力没有完全冷掉。关键还是看成交量和 BTC 配合。

今日情绪：{mood}
#BTC #山寨币 #市场情绪""".strip()

    if title == "山寨雷达":
        return f"""【石墨烯财经｜山寨雷达】

山寨方向继续观察，短线活跃度靠前：{up_text}

石墨烯观察：
山寨行情不是看一两个币突然拉升，而是看多个板块有没有一起动。如果只是单点上涨，更多是短线资金试探。

当前状态：{mood}
#山寨币 #MEME #AI币""".strip()

    if title == "情绪温度":
        return f"""【石墨烯财经｜情绪温度】

当前市场情绪：{mood}

短线靠前：{up_text}
短线回落：{down_text}

石墨烯观察：
情绪热得太快，容易被洗；情绪冷到没人看，反而容易出现修复。现在重点看资金是否愿意持续进场。

市场倾向：观察
#市场情绪 #BTC #交易心理""".strip()

    if title == "夜间复盘":
        return f"""【石墨烯财经｜夜间复盘】

主线行情今天没有给出绝对单边信号。

短线靠前：{up_text}
短线回落：{down_text}

石墨烯观察：
BTC 和 ETH 仍然是判断市场强弱的核心。只要主线不塌，山寨还有轮动空间；如果 BTC 明显走弱，高弹性标的通常会先被砸。

市场状态：{mood}
#BTC #ETH #市场复盘""".strip()

    if title == "山寨复盘":
        return f"""【石墨烯财经｜山寨复盘】

山寨方向今天主要看局部活跃度，而不是单个币的短线冲高。

短线靠前：{up_text}

石墨烯观察：
山寨行情最怕只有单点拉升。如果多个板块一起动，说明情绪在扩散；如果只是一两个币冲高，更像短线资金做热度。

当前状态：{mood}
#山寨币 #MEME #AI币""".strip()

    return f"""【石墨烯财经｜明日盯盘】

明天重点看三件事：

1. BTC 能不能继续稳住主方向
2. ETH 是否补涨
3. 山寨资金有没有继续外溢

石墨烯观察：
明天真正要看的不是谁突然拉升，而是资金有没有连续性。如果 BTC 稳住，山寨还有机会；如果 BTC 回落，高弹性标的通常会先被砸。

明日重点：观察为主
#BTC #ETH #山寨币""".strip()


def fallback_alert_content(symbol: str, item: dict, market: Dict[str, dict]) -> str:
    display = item["display"]
    change = item.get("change_1h") or 0
    direction = "拉升" if change > 0 else "回落"
    tendency = "观察偏多" if change > 0 else "谨慎观察"

    return f"""【石墨烯财经｜行情异动】

{display} 短线出现{direction}，1小时涨跌幅约 {format_percent(change)}。

石墨烯观察：
这种异动说明短线资金正在重新定价，但不能只看一根K线。后面重点看成交量能不能延续，以及 BTC 是否配合。

当前倾向：{tendency}
#{display} #行情异动 #加密市场""".strip()


# =========================
# 内容生成
# =========================

async def generate_fixed_content(plan: dict, market: Dict[str, dict]) -> str:
    prompt = build_fixed_prompt(plan, market)
    ai_text = await call_openai(prompt)

    if ai_text:
        return ai_text.strip()

    return fallback_fixed_content(plan, market)


async def generate_alert_content(symbol: str, item: dict, market: Dict[str, dict]) -> str:
    prompt = build_alert_prompt(symbol, item, market)
    ai_text = await call_openai(prompt)

    if ai_text:
        return ai_text.strip()

    return fallback_alert_content(symbol, item, market)


# =========================
# Telegram 发送
# =========================

def safe_caption(content: str) -> str:
    content = content.strip()
    if len(content) <= 1024:
        return content

    return content[:1000].rstrip() + "\n……"


async def send_to_channel(bot: Bot, content: str, image_path: Optional[str] = None):
    if image_path and os.path.isfile(image_path):
        with open(image_path, "rb") as f:
            await bot.send_photo(
                chat_id=CHAT_ID,
                photo=f,
                caption=safe_caption(content),
            )
        return

    await bot.send_message(
        chat_id=CHAT_ID,
        text=content,
        disable_web_page_preview=True,
    )


# =========================
# 固定栏目调度
# =========================

def fixed_gap_ok() -> bool:
    last = last_fixed_sent_at()
    if not last:
        return True

    diff = datetime.now(timezone.utc) - last
    return diff.total_seconds() >= MIN_FIXED_POST_GAP_MINUTES * 60


def find_due_fixed_post() -> Optional[dict]:
    now = now_local()

    for plan in DAILY_POST_PLAN:
        post_key = build_post_key(plan)

        if post_exists(post_key):
            continue

        scheduled = scheduled_datetime_today(plan["time"])
        grace_until = scheduled + timedelta(minutes=MISSED_POST_GRACE_MINUTES)

        if now < scheduled:
            return None

        if scheduled <= now <= grace_until:
            return plan

        if now > grace_until:
            print(f"跳过错过的固定栏目：{plan['time']} {plan['title']}")
            record_post(plan, content="MISSED_BY_SCHEDULER", status="skipped")
            continue

    return None


async def fixed_schedule_loop(bot: Bot):
    await asyncio.sleep(8)

    while True:
        try:
            plan = find_due_fixed_post()

            if plan:
                if not fixed_gap_ok():
                    print("固定栏目间隔保护中，暂不发送")
                    await asyncio.sleep(SCHEDULE_CHECK_INTERVAL_SECONDS)
                    continue

                print(f"准备发送固定栏目：{plan['time']} {plan['title']}")

                market = await fetch_market_data()
                content = await generate_fixed_content(plan, market)

                image_path = IMAGE_FILES.get(plan["image_key"])
                await send_to_channel(bot, content, image_path=image_path)

                record_post(plan, content, status="sent")

                print(f"固定栏目已发送：{plan['time']} {plan['title']}")

        except Exception as e:
            print("固定栏目调度异常:", e)

        await asyncio.sleep(SCHEDULE_CHECK_INTERVAL_SECONDS)


# =========================
# 异动提醒
# =========================

def recently_alerted(symbol: str) -> bool:
    row = fetch_one(
        """
        SELECT sent_at
        FROM alerts_log
        WHERE symbol=%s
        ORDER BY sent_at DESC
        LIMIT 1;
        """,
        (symbol,)
    )

    if not row:
        return False

    sent_at = row["sent_at"]
    if sent_at.tzinfo is None:
        sent_at = sent_at.replace(tzinfo=timezone.utc)

    diff = datetime.now(timezone.utc) - sent_at.astimezone(timezone.utc)
    return diff.total_seconds() < ALERT_COOLDOWN_MINUTES * 60


async def alerts_loop(bot: Bot):
    await asyncio.sleep(45)

    while True:
        try:
            market = await fetch_market_data()

            triggered = []

            for symbol in SYMBOLS:
                item = market.get(symbol)
                if not item:
                    continue

                change = item.get("change_1h")
                if change is None:
                    continue

                threshold = ALERT_THRESHOLDS_1H.get(symbol, 2.0)

                if abs(change) >= threshold and not recently_alerted(symbol):
                    triggered.append(item)

            triggered.sort(key=lambda x: abs(x.get("change_1h") or 0), reverse=True)

            sent_count = 0

            for item in triggered[:MAX_ALERTS_PER_CHECK]:
                symbol = item["symbol"]
                change = item.get("change_1h") or 0
                direction = "up" if change > 0 else "down"

                print(f"准备发送异动提醒：{symbol} {format_percent(change)}")

                content = await generate_alert_content(symbol, item, market)

                alert_image_path = IMAGE_FILES.get("market_alert")
                await send_to_channel(bot, content, image_path=alert_image_path)

                record_alert(symbol, direction, change, content)

                sent_count += 1
                await asyncio.sleep(2)

            if sent_count == 0:
                print("本轮无异动触发")

        except Exception as e:
            print("异动监控异常:", e)

        await asyncio.sleep(PRICE_CHECK_INTERVAL_SECONDS)


# =========================
# 启动
# =========================

async def main_async():
    if not BOT_TOKEN:
        raise RuntimeError("缺少 BOT_TOKEN")

    if not CHAT_ID:
        raise RuntimeError("缺少 CHAT_ID")

    if not DATABASE_URL:
        raise RuntimeError("缺少 DATABASE_URL 或 DATABASE_PUBLIC_URL")

    init_db()

    bot = Bot(token=BOT_TOKEN)

    await bot.initialize()

    # 不使用 polling，不 getUpdates。
    # 这里删除 webhook 只是为了避免旧 webhook 干扰发送，不会造成 getUpdates 冲突。
    try:
        await bot.delete_webhook(drop_pending_updates=True)
    except Exception as e:
        print("delete_webhook 失败，可忽略:", e)

    print("石墨烯雷达无命令正式版启动成功")
    print("频道:", CHAT_ID)
    print("固定栏目:", DAILY_FIXED_POSTS, "条/天")
    print("行情检查间隔:", PRICE_CHECK_INTERVAL_SECONDS // 60, "分钟")
    print("不会使用 getUpdates，不会产生 polling 冲突")

    try:
        await asyncio.gather(
            fixed_schedule_loop(bot),
            alerts_loop(bot),
        )
    finally:
        await bot.shutdown()


def main():
    asyncio.run(main_async())


if __name__ == "__main__":
    main()
