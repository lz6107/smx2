import os
import re
import json
import time
import html
import sqlite3
import hashlib
from datetime import datetime, timedelta

import feedparser
import requests
from openai import OpenAI

# =========================
# 基础配置（石墨烯财经 正式精简版 + 8图版）
# =========================

RSS_URLS = [
    "https://www.coindesk.com/arc/outboundfeeds/rss/",
    "https://cointelegraph.com/rss",
    "https://decrypt.co/feed",
]

BOT_TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

CHECK_INTERVAL = int(os.getenv("CHECK_INTERVAL", "900"))
SEND_DELAY = float(os.getenv("SEND_DELAY", "2"))
MAX_SUMMARY_LENGTH = int(os.getenv("MAX_SUMMARY_LENGTH", "420"))
MODEL_NAME = os.getenv("MODEL_NAME", "gpt-5.4-nano")

FIRST_RUN_SKIP_OLD = os.getenv("FIRST_RUN_SKIP_OLD", "true").lower() == "true"
IMAGES_DIR = os.getenv("IMAGES_DIR", "images")

MAX_FEED_ITEMS_PER_CHECK = int(os.getenv("MAX_FEED_ITEMS_PER_CHECK", "8"))
MAX_AI_CALLS_PER_CHECK = int(os.getenv("MAX_AI_CALLS_PER_CHECK", "3"))
MAX_POSTS_PER_DAY = int(os.getenv("MAX_POSTS_PER_DAY", "6"))
MIN_NEWS_SCORE = int(os.getenv("MIN_NEWS_SCORE", "2"))
MIN_POST_INTERVAL_SECONDS = int(os.getenv("MIN_POST_INTERVAL_SECONDS", "3600"))

LOCAL_TZ_OFFSET = int(os.getenv("LOCAL_TZ_OFFSET", "8"))

BTC_IMAGE = os.getenv("BTC_IMAGE", "btc.png")
ETH_IMAGE = os.getenv("ETH_IMAGE", "eth.png")
ALTCOIN_IMAGE = os.getenv("ALTCOIN_IMAGE", "altcoin.png")
ONCHAIN_IMAGE = os.getenv("ONCHAIN_IMAGE", "onchain.png")
MACRO_IMAGE = os.getenv("MACRO_IMAGE", "macro.png")
ALT_RECAP_IMAGE = os.getenv("ALT_RECAP_IMAGE", "alt_recap.png")
TOMORROW_WATCH_IMAGE = os.getenv("TOMORROW_WATCH_IMAGE", "tomorrow_watch.png")
MARKET_ALERT_IMAGE = os.getenv("MARKET_ALERT_IMAGE", "market_alert.png")

client = OpenAI(api_key=OPENAI_API_KEY)

# =========================
# 过滤和打分
# =========================

SKIP_KEYWORDS = [
    "podcast",
    "newsletter",
    "video",
    "watch live",
    "live blog",
    "live updates",
    "opinion",
    "editorial",
    "price prediction",
    "sponsored",
    "advertisement",
]

HIGH_VALUE_KEYWORDS = [
    "etf", "sec", "fed", "rate", "inflation", "tariff", "treasury",
    "bitcoin", "btc", "ethereum", "eth", "sol", "xrp", "bnb",
    "doge", "meme", "altcoin", "altseason", "stablecoin",
    "regulation", "lawsuit", "approval", "rejection", "launch",
    "hack", "exploit", "breach", "liquidation", "whale", "unlock",
    "inflow", "outflow", "staking", "airdrop", "listing", "delisting",
    "reserve", "bank", "institution", "adoption", "on-chain", "onchain",
]

ALERT_KEYWORDS = [
    "surge", "jump", "plunge", "drop", "crash", "rally", "selloff",
    "liquidation", "hack", "exploit", "breach", "warning", "panic",
    "breakout", "breakdown", "soar", "slump",
]

# =========================
# 数据库
# =========================

def db_conn():
    return sqlite3.connect("data.db")


def init_db():
    conn = db_conn()
    cur = conn.cursor()

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS sent_links (
            link TEXT PRIMARY KEY,
            created_at TEXT
        )
        """
    )

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS sent_fingerprints (
            fingerprint TEXT PRIMARY KEY,
            created_at TEXT
        )
        """
    )

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS sent_posts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            link TEXT,
            fingerprint TEXT,
            title_en TEXT,
            title_cn TEXT,
            image_type TEXT,
            created_at TEXT
        )
        """
    )

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS meta (
            key TEXT PRIMARY KEY,
            value TEXT
        )
        """
    )

    conn.commit()
    conn.close()


def has_any_sent_data() -> bool:
    conn = db_conn()
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM sent_links")
    link_count = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM sent_fingerprints")
    fp_count = cur.fetchone()[0]
    conn.close()
    return (link_count + fp_count) > 0


def has_sent_link(link: str) -> bool:
    conn = db_conn()
    cur = conn.cursor()
    cur.execute("SELECT 1 FROM sent_links WHERE link = ?", (link,))
    row = cur.fetchone()
    conn.close()
    return row is not None


def has_sent_fingerprint(fingerprint: str) -> bool:
    conn = db_conn()
    cur = conn.cursor()
    cur.execute("SELECT 1 FROM sent_fingerprints WHERE fingerprint = ?", (fingerprint,))
    row = cur.fetchone()
    conn.close()
    return row is not None


def mark_sent(link: str, fingerprint: str, title_en: str = "", title_cn: str = "", image_type: str = ""):
    now = datetime.now().isoformat()
    conn = db_conn()
    cur = conn.cursor()

    if link:
        cur.execute(
            "INSERT OR IGNORE INTO sent_links(link, created_at) VALUES (?, ?)",
            (link, now),
        )

    if fingerprint:
        cur.execute(
            "INSERT OR IGNORE INTO sent_fingerprints(fingerprint, created_at) VALUES (?, ?)",
            (fingerprint, now),
        )

    cur.execute(
        """
        INSERT INTO sent_posts(link, fingerprint, title_en, title_cn, image_type, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (link, fingerprint, title_en, title_cn, image_type, now),
    )

    conn.commit()
    conn.close()


def count_posts_today() -> int:
    start = local_now().replace(hour=0, minute=0, second=0, microsecond=0)
    conn = db_conn()
    cur = conn.cursor()
    cur.execute(
        "SELECT COUNT(*) FROM sent_posts WHERE created_at >= ?",
        (start.isoformat(),),
    )
    count = cur.fetchone()[0]
    conn.close()
    return count


def get_meta(key: str, default: str = "") -> str:
    conn = db_conn()
    cur = conn.cursor()
    cur.execute("SELECT value FROM meta WHERE key = ?", (key,))
    row = cur.fetchone()
    conn.close()
    return row[0] if row else default


def set_meta(key: str, value: str):
    conn = db_conn()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO meta(key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, value),
    )
    conn.commit()
    conn.close()

# =========================
# 时间工具
# =========================

def local_now() -> datetime:
    return datetime.utcnow() + timedelta(hours=LOCAL_TZ_OFFSET)


def last_post_time():
    raw = get_meta("last_post_time", "")
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw)
    except Exception:
        return None


def enough_post_interval() -> bool:
    last_dt = last_post_time()
    if not last_dt:
        return True
    seconds = (local_now() - last_dt).total_seconds()
    return seconds >= MIN_POST_INTERVAL_SECONDS

# =========================
# 文本处理
# =========================

def clean_html(text: str) -> str:
    if not text:
        return ""
    text = html.unescape(text)
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.I)
    text = re.sub(r"</p\s*>", "\n", text, flags=re.I)
    text = re.sub(r"<.*?>", "", text, flags=re.S)
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r"[ \t]+", " ", text)
    return text.strip()


def shorten_text(text: str, max_len: int) -> str:
    if not text:
        return ""
    text = text.strip()
    if len(text) <= max_len:
        return text
    cut = text[:max_len].rstrip()
    split_chars = ["。", "！", "？", "；", "，", ".", "!", "?", ";", ","]
    last_pos = -1
    for ch in split_chars:
        pos = cut.rfind(ch)
        if pos > last_pos:
            last_pos = pos
    if last_pos >= max_len // 2:
        cut = cut[: last_pos + 1].rstrip()
    return cut


def extract_summary(entry) -> str:
    raw_summary = getattr(entry, "summary", "") or getattr(entry, "description", "")
    content_list = getattr(entry, "content", None)
    if content_list and isinstance(content_list, list):
        for item in content_list:
            value = item.get("value", "")
            if value and len(value) > len(raw_summary):
                raw_summary = value

    summary_clean = clean_html(raw_summary)
    summary_clean = re.sub(r"\s+", " ", summary_clean).strip()

    if len(summary_clean) < 40:
        return ""

    return shorten_text(summary_clean, MAX_SUMMARY_LENGTH)


def clean_one_line(text: str) -> str:
    if not text:
        return ""
    text = clean_html(text)
    text = text.replace("...", "").replace("……", "")
    text = re.sub(r"\s+", " ", text)
    return text.strip(" \n\r\t-—:：")


def clean_paragraph(text: str) -> str:
    if not text:
        return ""
    text = clean_html(text)
    text = text.replace("...", "").replace("……", "")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{2,}", "\n", text)
    lines = [x.strip() for x in text.split("\n") if x.strip()]
    return "\n".join(lines).strip()


def should_skip_title(title_en: str) -> bool:
    title_lower = (title_en or "").lower().strip()
    if not title_lower:
        return True
    return any(k in title_lower for k in SKIP_KEYWORDS)


def make_fingerprint(title_en: str) -> str:
    normalized = (title_en or "").lower()
    normalized = re.sub(r"&amp;", "and", normalized)
    normalized = re.sub(r"[^a-z0-9]+", " ", normalized)
    normalized = re.sub(r"\s+", " ", normalized).strip()
    return hashlib.md5(normalized.encode("utf-8")).hexdigest() if normalized else ""


def score_entry(title_en: str, summary_en: str) -> int:
    text = f"{title_en} {summary_en}".lower()
    score = 0

    for word in HIGH_VALUE_KEYWORDS:
        if word in text:
            score += 1

    if any(k in text for k in ALERT_KEYWORDS):
        score += 1

    if len(summary_en) >= 120:
        score += 1

    if len(title_en) <= 8:
        score -= 1

    return score

# =========================
# 图片处理（8图版）
# =========================

def image_path(filename: str) -> str:
    return os.path.join(IMAGES_DIR, filename)


def get_best_local_image(result: dict) -> str:
    image_type = result.get("image_type", "")

    mapping = {
        "btc": BTC_IMAGE,
        "eth": ETH_IMAGE,
        "altcoin": ALTCOIN_IMAGE,
        "onchain": ONCHAIN_IMAGE,
        "macro": MACRO_IMAGE,
        "alt_recap": ALT_RECAP_IMAGE,
        "tomorrow_watch": TOMORROW_WATCH_IMAGE,
        "market_alert": MARKET_ALERT_IMAGE,
    }

    filename = mapping.get(image_type, MACRO_IMAGE)
    path = image_path(filename)
    if os.path.isfile(path):
        return path

    fallback = image_path(MACRO_IMAGE)
    if os.path.isfile(fallback):
        return fallback

    return ""

# =========================
# AI 提示词
# =========================

SYSTEM_PROMPT = """
你是“石墨烯财经”的中文加密市场编辑，负责把英文加密新闻加工成适合中文频道发布的内容。

你的工作重点：
1. 只抓重点，不机械翻译
2. 中文要自然，像成熟频道编辑写的
3. 输出短标题、短正文、一句判断
4. 尽量用更省字、更清晰的表达
5. 不要英文，不要来源，不要链接，不要省略号
6. 不要总是用同一种句式开头
7. 只输出 JSON

image_type 只能是：
btc、eth、altcoin、onchain、macro、alt_recap、tomorrow_watch、market_alert

image_type 参考：
- btc：比特币、BTC、比特币 ETF、矿工、比特币主导行情
- eth：以太坊、ETH、L2、以太坊生态
- altcoin：SOL、XRP、DOGE、BNB、MEME、公链、单一山寨币新闻
- onchain：链上数据、地址、资金流向、巨鲸、质押、解锁
- macro：监管、政策、ETF审批、利率、全球宏观、综合快讯
- alt_recap：山寨币轮动、板块联动、MEME热度扩散、多个山寨币共同表现
- tomorrow_watch：明日值得关注、前瞻、事件日历、即将发生的关键时间点
- market_alert：突发行情、急涨急跌、清算、黑客攻击、市场异动

bias 只能是：
偏多、偏空、中性、观望
""".strip()


def build_user_prompt(title_en: str, summary_en: str) -> str:
    return f"""
请根据下面这条英文加密新闻，输出一个 JSON 对象，不要输出 JSON 以外的任何内容。

JSON 格式：
{{
  "title_cn": "简洁中文标题",
  "image_type": "btc/eth/altcoin/onchain/macro/alt_recap/tomorrow_watch/market_alert",
  "bias": "偏多/偏空/中性/观望",
  "main_text": "2到3句加工后的中文正文",
  "takeaway": "1句简短核心判断"
}}

要求：
1. title_cn：8到16个字，简洁、自然、有内容感
2. main_text：2到3句，写重点，不要啰嗦，不要翻译腔
3. takeaway：一句话点出市场关注点
4. 不要输出英文、来源、链接
5. 不要使用省略号
6. 句子必须完整
7. 如果新闻更像“前瞻/即将发生”，优先用 tomorrow_watch
8. 如果新闻更像“突然异动/风险事件/大涨大跌”，优先用 market_alert
9. 如果新闻更像“山寨轮动/多个山寨币一起表现”，优先用 alt_recap

英文标题：
{title_en}

英文摘要：
{summary_en if summary_en else "（无摘要）"}
""".strip()


def extract_json_object(text: str) -> str:
    if not text:
        return ""
    m = re.search(r"\{.*\}", text, re.S)
    return m.group(0).strip() if m else ""


def ai_compile_news(title_en: str, summary_en: str) -> dict:
    prompt = build_user_prompt(title_en, summary_en)

    response = client.responses.create(
        model=MODEL_NAME,
        instructions=SYSTEM_PROMPT,
        input=prompt,
    )

    raw_text = (response.output_text or "").strip()
    raw_json = extract_json_object(raw_text)
    if not raw_json:
        return {}

    try:
        data = json.loads(raw_json)
    except Exception:
        return {}

    title_cn = clean_one_line(str(data.get("title_cn", "")))
    image_type = clean_one_line(str(data.get("image_type", "")))
    bias = clean_one_line(str(data.get("bias", "")))
    main_text = clean_paragraph(str(data.get("main_text", "")))
    takeaway = clean_one_line(str(data.get("takeaway", "")))

    valid_types = {
        "btc", "eth", "altcoin", "onchain", "macro",
        "alt_recap", "tomorrow_watch", "market_alert"
    }
    valid_bias = {"偏多", "偏空", "中性", "观望"}

    if image_type not in valid_types:
        return {}
    if bias not in valid_bias:
        return {}
    if not title_cn or not main_text or not takeaway:
        return {}

    return {
        "title_cn": title_cn,
        "image_type": image_type,
        "bias": bias,
        "main_text": main_text,
        "takeaway": takeaway,
    }

# =========================
# 标签映射
# =========================

PRIMARY_TAG_MAP = {
    "btc": "#BTC",
    "eth": "#ETH",
    "altcoin": "#山寨币",
    "onchain": "#链上",
    "macro": "#宏观",
    "alt_recap": "#山寨复盘",
    "tomorrow_watch": "#明日盯盘",
    "market_alert": "#行情异动",
}

SECONDARY_TAG_MAP = {
    "btc": "#比特币快讯",
    "eth": "#以太坊观察",
    "altcoin": "#山寨币快讯",
    "onchain": "#链上趋势",
    "macro": "#宏观与加密",
    "alt_recap": "#板块轮动",
    "tomorrow_watch": "#事件前瞻",
    "market_alert": "#市场警报",
}


def build_final_text(result: dict) -> str:
    primary_tag = PRIMARY_TAG_MAP.get(result["image_type"], "#加密市场")
    secondary_tag = SECONDARY_TAG_MAP.get(result["image_type"], "#石墨烯财经")
    bias_tag = "#" + result["bias"]

    return (
        f"石墨烯财经｜{result['title_cn']}\n\n"
        f"{result['main_text']}\n\n"
        f"一句话：{result['takeaway']}\n"
        f"{primary_tag} {secondary_tag} {bias_tag}"
    ).strip()

# =========================
# Telegram 发送
# =========================

def send_telegram_message(text: str):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    resp = requests.post(
        url,
        data={
            "chat_id": CHAT_ID,
            "text": text,
            "disable_web_page_preview": True,
        },
        timeout=30,
    )
    print("sendMessage 结果:", resp.status_code, resp.text)
    return resp


def send_telegram_photo_by_file(photo_path: str, caption: str):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendPhoto"
    with open(photo_path, "rb") as f:
        resp = requests.post(
            url,
            data={
                "chat_id": CHAT_ID,
                "caption": caption,
            },
            files={"photo": f},
            timeout=30,
        )
    print("sendPhoto(file) 结果:", resp.status_code, resp.text)
    return resp

# =========================
# 主流程
# =========================

def process_feed(feed_url: str):
    print(f"[{datetime.now()}] 检查 RSS: {feed_url}")

    if count_posts_today() >= MAX_POSTS_PER_DAY:
        print(f"今日已达上限 {MAX_POSTS_PER_DAY} 条，跳过本轮")
        return

    if not enough_post_interval():
        print(f"未到最小发送间隔 {MIN_POST_INTERVAL_SECONDS} 秒，跳过本轮")
        return

    feed = feedparser.parse(feed_url)
    if not feed.entries:
        print("没有抓到内容")
        return

    entries = list(feed.entries[:MAX_FEED_ITEMS_PER_CHECK])
    entries.reverse()

    first_run = not has_any_sent_data()
    ai_calls = 0

    for entry in entries:
        if count_posts_today() >= MAX_POSTS_PER_DAY:
            print(f"今日已达上限 {MAX_POSTS_PER_DAY} 条，停止本轮")
            break

        if not enough_post_interval():
            print("达到发送间隔保护，等待下一轮")
            break

        if ai_calls >= MAX_AI_CALLS_PER_CHECK:
            print(f"本轮 AI 调用达到上限 {MAX_AI_CALLS_PER_CHECK}，停止本轮")
            break

        link = getattr(entry, "link", "").strip()
        title_en = clean_html(getattr(entry, "title", "").strip())
        fingerprint = make_fingerprint(title_en)

        if not link or not title_en:
            continue

        if should_skip_title(title_en):
            print("跳过低价值标题:", title_en)
            continue

        if has_sent_link(link) or (fingerprint and has_sent_fingerprint(fingerprint)):
            print("已存在，跳过:", title_en)
            continue

        if first_run and FIRST_RUN_SKIP_OLD:
            print("首次运行，跳过旧新闻:", title_en)
            mark_sent(link, fingerprint, title_en=title_en)
            continue

        summary_en = extract_summary(entry)
        score = score_entry(title_en, summary_en)
        if score < MIN_NEWS_SCORE:
            print(f"分值过低({score})，跳过:", title_en)
            mark_sent(link, fingerprint, title_en=title_en)
            continue

        try:
            ai_calls += 1
            result = ai_compile_news(title_en, summary_en)
            if not result:
                print("AI 结果无效，跳过:", title_en)
                mark_sent(link, fingerprint, title_en=title_en)
                continue

            final_text = build_final_text(result)
            photo_path = get_best_local_image(result)

            if photo_path and os.path.isfile(photo_path):
                resp = send_telegram_photo_by_file(photo_path, final_text)
                if resp.status_code != 200:
                    print("图片发送失败，改为纯文字")
                    resp = send_telegram_message(final_text)
            else:
                resp = send_telegram_message(final_text)

            if resp.status_code == 200:
                mark_sent(
                    link,
                    fingerprint,
                    title_en=title_en,
                    title_cn=result["title_cn"],
                    image_type=result["image_type"],
                )
                set_meta("last_post_time", local_now().isoformat())
                print("已发送:", title_en)
            else:
                print("发送失败，未记录:", title_en)

        except Exception as e:
            print("处理失败:", title_en, "->", e)

        time.sleep(SEND_DELAY)


def main():
    if not BOT_TOKEN:
        raise ValueError("缺少环境变量 BOT_TOKEN")
    if not CHAT_ID:
        raise ValueError("缺少环境变量 CHAT_ID")
    if not OPENAI_API_KEY:
        raise ValueError("缺少环境变量 OPENAI_API_KEY")

    init_db()

    print("石墨烯财经频道机器人启动成功（8图精简版）")
    print("频道:", CHAT_ID)
    print("每日上限:", MAX_POSTS_PER_DAY)
    print("最小发送间隔(秒):", MIN_POST_INTERVAL_SECONDS)
    print("单轮 AI 调用上限:", MAX_AI_CALLS_PER_CHECK)

    while True:
        for rss in RSS_URLS:
            try:
                process_feed(rss)
            except Exception as e:
                print(f"处理 RSS 失败 {rss}: {e}")

        print(f"休眠 {CHECK_INTERVAL} 秒...\n")
        time.sleep(CHECK_INTERVAL)


if __name__ == "__main__":
    main()
