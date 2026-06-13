"""Catalog Manager — "living catalog" logika.

Denný refresh:
  1. Zozbieraj trendy (trend_agent)
  2. Vyfiltruj keywordy ktoré už v katalógu máme
  3. Pre top N nových trendov nájdi produkty (product_agent)
  4. Pridaj ich do Shopify + DB
  5. Zmaž rovnaký počet najstarších/najslabších produktov (ak katalóg presahuje limit)
"""
import logging
from datetime import datetime, timezone

from sqlalchemy import select, func

from trendoris.agents import trend_agent, product_agent
from trendoris.config import settings
from trendoris.db.base import AsyncSessionLocal
from trendoris.db.models import Product, TrendSignal
from trendoris.services.shopify_client import shopify_client

logger = logging.getLogger(__name__)


async def daily_refresh() -> dict:
    """Hlavný denný job. Vracia súhrn pre logy/API."""
    added: list[str] = []
    removed: list[str] = []

    async with AsyncSessionLocal() as db:
        # 1. Trendy
        candidates = await trend_agent.collect_trends()

        # Ulož signály do histórie
        for c in candidates[:50]:
            db.add(TrendSignal(keyword=c.keyword, score=c.score, source=c.source))
        await db.commit()

        # 2. Vyfiltruj keywordy ktoré už máme aktívne
        existing_keywords = set(
            (await db.execute(
                select(Product.trend_keyword).where(Product.active == True)  # noqa: E712
            )).scalars().all()
        )
        fresh = [c for c in candidates if c.keyword not in existing_keywords]

        # 3+4. Pridaj produkty — ak sme pod catalog_size, doplníme po max
        active_now = (await db.execute(
            select(func.count()).select_from(Product).where(Product.active == True)  # noqa: E712
        )).scalar_one()
        shortage = max(0, settings.catalog_size - active_now)
        target_new = max(settings.daily_refresh_count, shortage)
        for candidate in fresh:
            if len(added) >= target_new:
                break
            try:
                matched = await product_agent.match_trend_to_product(candidate)
            except Exception:
                logger.exception("Match zlyhal pre '%s'", candidate.keyword)
                continue
            if matched is None:
                continue

            # Duplicitný CJ produkt? (rovnaký pid už v katalógu)
            dup = (await db.execute(
                select(Product).where(Product.cj_pid == matched.cj_product["pid"])
            )).scalar_one_or_none()
            if dup is not None:
                continue

            imgs = matched.image_urls or [matched.cj_product.get("image_url", "")]
            shopify_id = await shopify_client.create_product(
                title=matched.title,
                body_html=matched.description_html,
                price=matched.price,
                image_urls=imgs,
            )
            db.add(Product(
                shopify_id=shopify_id,
                cj_pid=matched.cj_product["pid"],
                title=matched.title,
                description=matched.description_html,
                price=matched.price,
                cost=matched.cj_product["sell_price"],
                image_url=imgs[0] if imgs else "",
                trend_keyword=matched.trend_keyword,
                trend_score=matched.trend_score,
            ))
            await db.commit()
            added.append(matched.title)

        # 5. Odstráň prebytočné — najstaršie s najnižším trend skóre
        active_count = (await db.execute(
            select(func.count()).select_from(Product).where(Product.active == True)  # noqa: E712
        )).scalar_one()

        overflow = active_count - settings.catalog_size
        if overflow > 0:
            stale = (await db.execute(
                select(Product)
                .where(Product.active == True)  # noqa: E712
                .order_by(Product.trend_score.asc(), Product.added_at.asc())
                .limit(overflow)
            )).scalars().all()
            for prod in stale:
                if prod.shopify_id:
                    try:
                        await shopify_client.delete_product(prod.shopify_id)
                    except Exception:
                        logger.exception("Mazanie Shopify produktu %s zlyhalo", prod.shopify_id)
                        continue
                prod.active = False
                prod.removed_at = datetime.now(timezone.utc).replace(tzinfo=None)
                removed.append(prod.title)
            await db.commit()

    summary = {"added": added, "removed": removed, "trends_collected": len(candidates)}
    logger.info("Denný refresh hotový: +%d / -%d", len(added), len(removed))
    return summary
