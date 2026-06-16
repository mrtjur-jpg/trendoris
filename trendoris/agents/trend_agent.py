"""Trend Agent — denne zbiera trending produkty z viacerých zdrojov.

Zdroje:
  - Google Trends (pytrends) — rastúce vyhľadávania v kategórii shopping
  - Reddit (PRAW) — hot posty z produktových subredditov

Výstup: zoznam TrendCandidate (keyword + skóre + zdroj), uložený do trend_signals.
"""
import asyncio
import logging
from dataclasses import dataclass

from trendoris.config import settings

logger = logging.getLogger(__name__)

# Subreddity kde sa objavujú virálne gadgety a produkty
PRODUCT_SUBREDDITS = [
    "shutupandtakemymoney",
    "INEEEEDIT",
    "gadgets",
    "BuyItForLife",
    "ProductPorn",
]

# Seed keywords pre Google Trends related queries
TREND_SEEDS = ["gadget", "viral product", "tiktok made me buy it", "home gadget"]

# Fallback keywords — použije sa ak Google Trends aj Reddit zlyhajú (napr. cloud IP block)
FALLBACK_KEYWORDS = [
    "portable blender", "led strip lights", "magnetic phone mount",
    "posture corrector", "mini projector", "smart plug wifi",
    "neck massager", "kitchen gadget", "waterproof phone case",
    "solar garden lights", "air fryer accessories", "wireless earbuds",
    "cable organizer", "laptop stand", "bathroom organizer",
    "car phone holder", "resistance bands", "cold brew coffee maker",
    "reusable bags", "travel pillow",
]


@dataclass
class TrendCandidate:
    keyword: str
    score: float  # 0-100, normalizované
    source: str   # google_trends | reddit | fallback


def _fetch_google_trends() -> list[TrendCandidate]:
    """Rastúce related queries pre seed keywords. Synchronné (pytrends nemá async)."""
    from pytrends.request import TrendReq

    candidates: list[TrendCandidate] = []
    try:
        pytrends = TrendReq(hl="en-US", tz=0)
        for seed in TREND_SEEDS:
            pytrends.build_payload([seed], timeframe="now 7-d")
            related = pytrends.related_queries()
            rising = related.get(seed, {}).get("rising")
            if rising is None:
                continue
            for _, row in rising.head(10).iterrows():
                raw = row["value"]
                score = 100.0 if raw == "Breakout" else min(float(raw) / 50, 100.0)
                candidates.append(TrendCandidate(
                    keyword=str(row["query"]),
                    score=score,
                    source="google_trends",
                ))
    except Exception:
        logger.exception("Google Trends fetch zlyhal")
    return candidates


def _fetch_reddit() -> list[TrendCandidate]:
    """Hot posty z produktových subredditov — názov postu ~ produkt."""
    import praw

    if not settings.reddit_client_id:
        logger.warning("Reddit credentials chýbajú — preskakujem")
        return []

    candidates: list[TrendCandidate] = []
    try:
        reddit = praw.Reddit(
            client_id=settings.reddit_client_id,
            client_secret=settings.reddit_client_secret,
            user_agent=settings.reddit_user_agent,
        )
        for sub_name in PRODUCT_SUBREDDITS:
            for post in reddit.subreddit(sub_name).hot(limit=10):
                if post.stickied:
                    continue
                score = min(post.score / 100, 100.0)
                candidates.append(TrendCandidate(
                    keyword=post.title[:255],
                    score=score,
                    source="reddit",
                ))
    except Exception:
        logger.exception("Reddit fetch zlyhal")
    return candidates


async def collect_trends() -> list[TrendCandidate]:
    """Spustí všetky zdroje paralelne (v thread pooli — sú synchrónne)."""
    if settings.mock_mode:
        from trendoris.services.mocks import MOCK_TRENDS
        candidates = [
            TrendCandidate(keyword=kw, score=95.0 - i * 7, source="mock")
            for i, kw in enumerate(MOCK_TRENDS)
        ]
        logger.info("[MOCK] %d falošných trendov", len(candidates))
        return candidates

    loop = asyncio.get_event_loop()
    google_task = loop.run_in_executor(None, _fetch_google_trends)
    reddit_task = loop.run_in_executor(None, _fetch_reddit)
    google, reddit = await asyncio.gather(google_task, reddit_task)

    merged = google + reddit

    # Fallback: ak oba zdroje zlyhali (napr. Google blokuje cloud IP)
    if not merged:
        logger.warning("Žiadne trendy zo živých zdrojov — používam fallback zoznam")
        merged = [
            TrendCandidate(keyword=kw, score=80.0 - i * 2, source="fallback")
            for i, kw in enumerate(FALLBACK_KEYWORDS)
        ]

    merged.sort(key=lambda c: c.score, reverse=True)
    logger.info("Zozbieraných %d trend kandidátov", len(merged))
    return merged
