import os
import json
import asyncio
from datetime import datetime, timezone, timedelta
from typing import Dict, Optional, Tuple, List

import requests
import psycopg2
from psycopg2.extras import RealDictCursor

from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes


# =========================
# 必填环境变量
# =========================

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
CHAT_ID = os.getenv("CHAT_ID", "").strip()
DATABASE_URL = (os.getenv("DATABASE_URL") or os.getenv("DATABASE_PUBLIC_URL") or "").strip()

# AI 可选
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "").strip()
MODEL_NAME = os.getenv("MODEL_NAME", "gpt-4.1-mini").strip()
ENABLE_AI = os.getenv("ENABLE_AI", "true").lower().strip() == "true"

LOCAL_TZ_OFFSET = int(os.getenv("LOCAL_TZ_OFFSET", "8"))
LOCAL_TZ = timezone(timedelta(hours=LOCAL_TZ_OFFSET))


# =========================
# 固定参数
# =========================

# 每天 3 轮，每轮 5 条 = 每天 15 条固定栏目
ROUNDS_PER_DAY = 3

CATEGORIES = [
    "今日盯盘",
    "热词榜",
    "山寨雷达",
    "情绪温度",
    "夜间复盘",
]

# 一天 15 条，平均每 96 分钟一条
POST_INTERVAL_SECONDS = 96 * 60

# 行情异动：每 5 分钟检查一次
PRICE_CHECK_INTERVAL_SECONDS = 5 * 60

# 同一个币 30 分钟内最多发一次异动
ALERT_COOLDOWN_MINUTES = 30

# 每轮行情检查最多发 2 条异动，防止刷屏
MAX_ALERTS_PER_CHECK = 2

# 固定栏目图片
CATEGORY_IMAGES = {
    "今日盯盘": "images/daily_watch.png",
    "热词榜": "images/hot_words.png",
    "山寨雷达": "images/altcoin_radar.png",
    "情绪温度": "images/sentiment.png",
    "夜间复盘": "images/night_review.png",
}

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
    "BTCUSDT": 0.3,
    "ETHUSDT": 0.4,
    "SOLUSDT": 0.5,
    "BNBUSDT": 0.4,
    "XRPUSDT": 0.5,
    "DOGEUSDT": 0.8,
    "PEPEUSDT": 0.8,
    "LINKUSDT": 0.8,
    "AVAXUSDT": 0.8,
    "WLDUSDT": 0.8,
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

CATEGORY_KEYS = {
    "今日盯盘": "daily_watch",
    "热词榜": "hot_words",
    "山寨雷达": "altcoin_radar",
    "情绪温度": "sentiment",
    "夜间复盘": "night_review",
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

    cur.execute("""
    CREATE INDEX IF NOT EXISTS idx_posts_log_sent_at
    ON posts_log(sent_at);
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


def fetch_all(query: str, params=()):
    conn = db_conn()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute(query, params)
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return rows


def execute(query: str, params=()):
    conn = db_conn()
    cur = conn.cursor()
    cur.execute(query, params)
    conn.commit()
    cur.close()
    conn.close()


def post_exists(post_key: str) -> bool:
    row = fetch_one("SELECT id FROM posts_log WHERE post_key=%s;", (post_key,))
    return bool(row)


def record_post(post_key: str, category: str, round_no: int, content: str):
    execute(
        """
        INSERT INTO posts_log(post_key, category, round_no, content)
        VALUES (%s, %s, %s, %s)
        ON CONFLICT(post_key) DO NOTHING;
        """,
        (post_key, category, round_no, content)
    )


def record_alert(symbol: str, direction: str, change_1h: float, content: str):
    execute(
        """
        INSERT INTO alerts_log(symbol, direction, change_1h, content)
        VALUES (%s, %s, %s, %s);
        """,
        (symbol, direction, change_1h, content)
    )


def clear_today_logs():
    today = today_key()
    local_now = now_local()
    day_start = datetime(local_now.year, local_now.month, local_now.day, tzinfo=LOCAL_TZ)

    execute(
        "DELETE FROM posts_log WHERE post_key LIKE %s;",
        (f"{today}:%",)
    )

    execute(
        "DELETE FROM alerts_log WHERE sent_at >= %s;",
        (day_start,)
    )


# =========================
# 时间工具
# =========================

def now_local() -> datetime:
    return datetime.now(LOCAL_TZ)


def today_key() -> str:
    return now_local().strftime("%Y%m%d")


def format_percent(value: Optional[float]) -> str:
    if value is None:
        return "未知"
    sign = "+" if value > 0 else ""
    return f"{sign}{value:.2f}%"


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
    返回格式：
    {
        "BTCUSDT": {
            "symbol": "BTCUSDT",
            "display": "BTC",
            "price": 100000.0,
            "change_1h": 0.25,
            "change_24h": 2.1,
            "quote_volume": 123456789.0
        }
    }
    """
    base = "https://api.binance.com"
    result: Dict[str, dict] = {}

    try:
        ticker_data = http_get_json(
            f"{base}/api/v3/ticker/24hr",
            params={"symbols": json.dumps(SYMBOLS)},
        )
    except Exception as e:
        print("获取 24h ticker 失败:", e)
        ticker_data = []

    if isinstance(ticker_data, dict):
        ticker_data = [ticker_data]

    for item in ticker_data:
        symbol = item.get("symbol")
        if symbol not in SYMBOLS:
            continue

        result[symbol] = {
            "symbol": symbol,
            "display": SYMBOL_DISPLAY.get(symbol, symbol.replace("USDT", "")),
            "price": safe_float(item.get("lastPrice")),
            "change_1h": None,
            "change_24h": safe_float(item.get("priceChangePercent")),
            "quote_volume": safe_float(item.get("quoteVolume")),
        }

    for symbol in SYMBOLS:
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

                if symbol not in result:
                    result[symbol] = {
                        "symbol": symbol,
                        "display": SYMBOL_DISPLAY.get(symbol, symbol.replace("USDT", "")),
                        "price": close_price,
                        "change_1h": change_1h,
                        "change_24h": None,
                        "quote_volume": None,
                    }
                else:
                    result[symbol]["change_1h"] = change_1h
                    if result[symbol]["price"] is None:
                        result[symbol]["price"] = close_price

        except Exception as e:
            print(f"获取 {symbol} 1h kline 失败:", e)

    return result


async def fetch_market_data() -> Dict[str, dict]:
    return await asyncio.to_thread(fetch_market_data_sync)


def market_snapshot_text(market: Dict[str, dict]) -> str:
    lines = []

    for symbol in SYMBOLS:
        item = market.get(symbol)
        if not item:
            continue

        price = item["price"]
        if isinstance(price, float):
            if price >= 100:
                price_text = f"{price:.2f}"
            elif price >= 1:
                price_text = f"{price:.4f}"
            else:
                price_text = f"{price:.8f}"
        else:
            price_text = "未知"

        lines.append(
            f"{item['display']}: 价格 {price_text}, "
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

    if abs(btc.get("change_1h") or 0) >= 0.3:
        words.append("比特币异动")

    if abs(eth.get("change_1h") or 0) >= 0.4:
        words.append("以太坊")

    if (sol.get("change_1h") or 0) > 0.5:
        words.append("SOL")

    if (doge.get("change_1h") or 0) > 0.8 or (pepe.get("change_1h") or 0) > 0.8:
        words.append("MEME")

    if (wld.get("change_1h") or 0) > 0.8:
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
            "max_output_tokens": 700,
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


def build_fixed_prompt(category: str, round_no: int, market: Dict[str, dict]) -> str:
    up = top_movers(market, "change_1h", 3, True)
    down = top_movers(market, "change_1h", 3, False)
    hot_words = build_hot_words(market)
    mood = market_mood(market)

    return f"""
请生成一条“石墨烯财经”频道内容。

要求：
1. 开头必须带：【第{round_no}轮】
2. 标题必须是：【石墨烯财经｜{category}】
3. 必须有“石墨烯观察：”
4. 必须有“当前状态：”或“市场倾向：”
5. 末尾带 2-3 个标签
6. 不要编造新闻、巨鲸金额、爆仓金额
7. 不要投资建议，不要喊单
8. 尽量每轮写法不同，不要像模板机
9. 控制在 450 个中文字以内，适合放在图片 caption 里

当前栏目：{category}
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
请生成一条“石墨烯财经｜异动雷达”频道内容。

要求：
1. 标题必须是：【石墨烯财经｜异动雷达】
2. 说明 {display} 1小时出现{direction}
3. 必须写出 1小时涨跌幅：{format_percent(item.get("change_1h"))}
4. 必须有“石墨烯观察：”
5. 必须有“当前倾向：”
6. 末尾带 2-3 个标签
7. 不要编造新闻、巨鲸金额、爆仓金额
8. 不要投资建议，不要喊单
9. 控制在 350 个中文字以内

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

def fallback_fixed_content(category: str, round_no: int, market: Dict[str, dict]) -> str:
    mood = market_mood(market)
    up = top_movers(market, "change_1h", 3, True)
    down = top_movers(market, "change_1h", 3, False)

    up_text = "、".join([f"{x['display']} {format_percent(x.get('change_1h'))}" for x in up]) or "暂无明显领涨"
    down_text = "、".join([f"{x['display']} {format_percent(x.get('change_1h'))}" for x in down]) or "暂无明显回落"

    if category == "今日盯盘":
        body = f"""【第{round_no}轮】
【石墨烯财经｜今日盯盘】

本轮重点看三个方向：

1. BTC 是否稳住主方向
2. ETH 能不能跟随放量
3. 山寨方向有没有资金外溢

当前短线靠前：{up_text}

石墨烯观察：
现在不是看单个币涨多少，而是看资金有没有连续性。如果 BTC 不拖后腿，山寨才有继续表现空间。

当前状态：{mood}
#BTC #ETH #山寨币"""

    elif category == "热词榜":
        hot_words = build_hot_words(market)[:5]
        body = f"""【第{round_no}轮】
【石墨烯财经｜热词榜】

本轮关键词：

1. {hot_words[0]}
2. {hot_words[1]}
3. {hot_words[2]}
4. {hot_words[3]}
5. {hot_words[4]}

石墨烯观察：
热词从主流币扩散到山寨和情绪方向，说明市场注意力没有完全冷掉。关键还是看成交量和 BTC 配合。

今日情绪：{mood}
#BTC #山寨币 #市场情绪"""

    elif category == "山寨雷达":
        body = f"""【第{round_no}轮】
【石墨烯财经｜山寨雷达】

山寨方向继续观察，短线活跃度靠前：{up_text}

石墨烯观察：
山寨行情不是看一两个币突然拉升，而是看多个板块有没有一起动。如果只是单点上涨，更多是短线资金试探。

当前状态：{mood}
#山寨币 #MEME #AI币"""

    elif category == "情绪温度":
        body = f"""【第{round_no}轮】
【石墨烯财经｜情绪温度】

当前市场情绪：{mood}

短线靠前：{up_text}
短线回落：{down_text}

石墨烯观察：
情绪热得太快，容易被洗；情绪冷到没人看，反而容易出现修复。现在重点看资金是否愿意持续进场。

市场倾向：观察
#市场情绪 #BTC #交易心理"""

    else:
        body = f"""【第{round_no}轮】
【石墨烯财经｜夜间复盘】

本轮市场没有给出绝对单边信号。

短线靠前：{up_text}
短线回落：{down_text}

石墨烯观察：
BTC 如果能稳住，山寨还有轮动机会；如果 BTC 明显走弱，短线资金通常会先从高弹性标的撤出。

明天重点：
继续看 BTC、ETH 和山寨资金能不能形成合力。

市场状态：{mood}
#BTC #ETH #市场复盘"""

    return body.strip()


def fallback_alert_content(symbol: str, item: dict, market: Dict[str, dict]) -> str:
    display = item["display"]
    change = item.get("change_1h") or 0
    direction = "拉升" if change > 0 else "回落"
    tendency = "观察偏多" if change > 0 else "谨慎观察"

    return f"""【石墨烯财经｜异动雷达】

{display} 短线出现{direction}，1小时涨跌幅约 {format_percent(change)}。

石墨烯观察：
这种异动说明短线资金正在重新定价，但不能只看一根K线。后面重点看成交量能不能延续，以及 BTC 是否配合。

当前倾向：{tendency}
#{display} #异动 #加密市场""".strip()


# =========================
# 内容生成
# =========================

async def generate_fixed_content(category: str, round_no: int, market: Dict[str, dict]) -> str:
    prompt = build_fixed_prompt(category, round_no, market)
    ai_text = await call_openai(prompt)

    if ai_text:
        return ai_text.strip()

    return fallback_fixed_content(category, round_no, market)


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


async def send_to_channel(bot, content: str, image_path: Optional[str] = None):
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
# 固定栏目循环：每天 15 条
# =========================

def next_pending_fixed_post() -> Optional[Tuple[str, str, int]]:
    date_key = today_key()

    for round_no in range(1, ROUNDS_PER_DAY + 1):
        for category in CATEGORIES:
            cat_key = CATEGORY_KEYS[category]
            post_key = f"{date_key}:round{round_no}:{cat_key}"

            if not post_exists(post_key):
                return post_key, category, round_no

    return None


async def fixed_posts_loop(app: Application):
    await asyncio.sleep(8)

    while True:
        try:
            pending = next_pending_fixed_post()

            if not pending:
                print("今天 15 条固定栏目已发完，等待下一轮检查")
                await asyncio.sleep(10 * 60)
                continue

            post_key, category, round_no = pending

            print(f"准备发送固定栏目：{post_key}")

            market = await fetch_market_data()
            content = await generate_fixed_content(category, round_no, market)

            image_path = CATEGORY_IMAGES.get(category)
            await send_to_channel(app.bot, content, image_path=image_path)

            record_post(post_key, category, round_no, content)

            print(f"固定栏目已发送：{post_key}")

        except Exception as e:
            print("固定栏目循环异常:", e)

        await asyncio.sleep(POST_INTERVAL_SECONDS)


# =========================
# 异动提醒循环
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


async def alerts_loop(app: Application):
    await asyncio.sleep(30)

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

                threshold = ALERT_THRESHOLDS_1H.get(symbol, 0.8)

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

                # 异动雷达暂时纯文字
                await send_to_channel(app.bot, content, image_path=None)

                record_alert(symbol, direction, change, content)

                sent_count += 1
                await asyncio.sleep(2)

            if sent_count == 0:
                print("本轮无异动触发")

        except Exception as e:
            print("异动监控循环异常:", e)

        await asyncio.sleep(PRICE_CHECK_INTERVAL_SECONDS)


# =========================
# 简单命令
# =========================

async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    today = today_key()
    local_now = now_local()
    day_start = datetime(local_now.year, local_now.month, local_now.day, tzinfo=LOCAL_TZ)

    sent_today = fetch_one(
        "SELECT COUNT(*) AS c FROM posts_log WHERE post_key LIKE %s;",
        (f"{today}:%",)
    )["c"]

    alerts_today = fetch_one(
        """
        SELECT COUNT(*) AS c
        FROM alerts_log
        WHERE sent_at >= %s;
        """,
        (day_start,)
    )["c"]

    await update.message.reply_text(
        f"石墨烯雷达图片版运行中。\n\n"
        f"今日固定栏目：{sent_today} / {ROUNDS_PER_DAY * len(CATEGORIES)}\n"
        f"今日异动提醒：{alerts_today}\n"
        f"固定栏目间隔：{POST_INTERVAL_SECONDS // 60} 分钟\n"
        f"行情检查间隔：{PRICE_CHECK_INTERVAL_SECONDS // 60} 分钟\n"
        f"AI：{'开启' if ENABLE_AI else '关闭'}\n"
        f"模型：{MODEL_NAME}\n\n"
        f"栏目图片：已启用"
    )


async def post_now(update: Update, context: ContextTypes.DEFAULT_TYPE):
    pending = next_pending_fixed_post()

    if not pending:
        await update.message.reply_text("今天 15 条固定栏目已经发完了。")
        return

    post_key, category, round_no = pending

    await update.message.reply_text(f"正在发送下一条：第{round_no}轮｜{category}")

    try:
        market = await fetch_market_data()
        content = await generate_fixed_content(category, round_no, market)

        image_path = CATEGORY_IMAGES.get(category)
        await send_to_channel(context.bot, content, image_path=image_path)

        record_post(post_key, category, round_no, content)

        await update.message.reply_text("已发送。")
    except Exception as e:
        await update.message.reply_text(f"发送失败：{e}")


async def clear_today(update: Update, context: ContextTypes.DEFAULT_TYPE):
    clear_today_logs()
    await update.message.reply_text("已清空今天的固定栏目和异动记录，可以重新测试。")


# =========================
# 启动
# =========================

async def post_init(app: Application):
    asyncio.create_task(fixed_posts_loop(app))
    asyncio.create_task(alerts_loop(app))


def main():
    if not BOT_TOKEN:
        raise RuntimeError("缺少 BOT_TOKEN")

    if not CHAT_ID:
        raise RuntimeError("缺少 CHAT_ID")

    if not DATABASE_URL:
        raise RuntimeError("缺少 DATABASE_URL 或 DATABASE_PUBLIC_URL")

    init_db()

    app = (
        Application.builder()
        .token(BOT_TOKEN)
        .post_init(post_init)
        .build()
    )

    app.add_handler(CommandHandler("status", status))
    app.add_handler(CommandHandler("post_now", post_now))
    app.add_handler(CommandHandler("clear_today", clear_today))

    print("石墨烯雷达图片版启动成功：每天 15 条固定栏目，自动配图")
    app.run_polling()


if __name__ == "__main__":
    main()
