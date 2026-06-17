"""Trend Agent â denne zbiera trending produkty z viacerÃ½ch zdrojov.

Zdroje:
  - Google Trends (pytrends) â rastÃºce vyhÄ¾adÃ¡vania v kategÃ³rii shopping
  - Reddit (PRAW) â hot posty z produktovÃ½ch subredditov

VÃ½stup: zoznam TrendCandidate (keyword + skÃ³re + zdroj), uloÅ¾enÃ½ do trend_signals.
"""
import asyncio
import logging
from dataclasses import dataclass

from trendoris.config import settings

logger = logging.getLogger(__name__)

# Subreddity kde sa objavujÃº virÃ¡lne gadgety a produkty
PRODUCT_SUBREDDITS = [
    "shutupandtakemymoney",
    "INEEEEDIT",
    "gadgets",
    "BuyItForLife",
    "ProductPorn",
]

# Seed keywords pre Google Trends related queries
TREND_SEEDS = ["gadget", "viral product", "tiktok made me buy it", "home gadget"]

# Fallback keywords â pouÅ¾ije sa ak Google Trends aj Reddit zlyhajÃº (napr. cloud IP block)
FALLBACK_KEYWORDS = [
    # Smart home & tech
    "portable blender", "led strip lights", "magnetic phone mount",
    "mini projector", "smart plug wifi", "wireless earbuds",
    "laptop stand", "cable organizer", "ring light selfie",
    "wireless charging pad", "smart door lock", "usb hub multiport",
    "portable power bank", "bluetooth tracker", "noise cancelling headphones",
    # Health & beauty
    "posture corrector", "neck massager", "resistance bands",
    "jade roller face", "electric foot massager", "hair removal laser",
    "eye massager", "foam roller muscle", "teeth whitening kit",
    "smart water bottle", "pulse oximeter", "digital thermometer",
    # Kitchen & home
    "kitchen gadget", "air fryer accessories", "bathroom organizer",
    "cold brew coffee maker", "reusable bags", "solar garden lights",
    "vacuum sealer food", "electric can opener", "dish drying rack",
    "silicone baking mat", "kitchen scale digital", "spice rack organizer",
    "over door organizer", "foldable laundry basket", "shower caddy",
    # Car & outdoor
    "car phone holder", "waterproof phone case", "travel pillow",
    "camping lantern led", "portable car vacuum", "dashcam 4k",
    "inflatable mattress camping", "collapsible water bottle",
    "bike phone mount", "car seat organizer",
    # Pet & kids
    "automatic cat feeder", "dog gps tracker", "pet grooming glove",
    "interactive dog toy", "cat water fountain",
]


@dataclass
class TrendCandidate:
    keyword: str
    score: float  # 0-100, normalizovanÃ©
    source: str   # google_trends | reddit | fallback


def _fetch_google_trends() -> list[TrendCandidate]:
    """RastÃºce related queries pre seed keywords. SynchronnÃ© (pytrends nemÃ¡ async)."""
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
    """Hot posty z produktovÃ½ch subredditov â nÃ¡zov postu ~ produkt."""
    import praw

    if not settings.reddit_client_id:
        logger.warning("Reddit credentials chÃ½bajÃº â preskakujem")
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
    """SpustÃ­ vÅ¡etky zdroje paralelne (v thread pooli â sÃº synchrÃ³nne)."""
    if settings.mock_mode:
        from trendoris.services.mocks import MOCK_TRENDS
        candidates = [
            TrendCandidate(keyword=kw, score=95.0 - i * 7, source="mock")
            for i, kw in enumerate(MOCK_TRENDS)
        ]
        logger.info("[MOCK] %d faloÅ¡nÃ½ch trendov", len(candidates))
        return candidates

    loop = asyncio.get_event_loop()
    google_task = loop.run_in_executor(None, _fetch_google_trends)
    reddit_task = loop.run_in_executor(None, _fetch_reddit)
    google, reddit = await asyncio.gather(google_task, reddit_task)

    merged = google + reddit

    # Fallback: ak oba zdroje zlyhali (napr. Google blokuje cloud IP)
    if not merged:
        logger.warning("Å½iadne trendy zo Å¾ivÃ½ch zdrojov â pouÅ¾Ã­vam fallback zoznam")
        merged = [
            TrendCandidate(keyword=kw, score=80.0 - i * 2, source="fallback")
            for i, kw in enumerate(FALLBACK_KEYWORDS)
        ]

    merged.sort(key=lambda c: c.score, reverse=True)
    logger.info("ZozbieranÃ½ch %d trend kandidÃ¡tov", len(merged))
    return merged
