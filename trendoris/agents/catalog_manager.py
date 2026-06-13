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
    added: list[str] = []
    removed: list[str] = []

    async with AsyncSessionLocal() as db:
        candidates = await trend_agent.collect_trends()

        for c in candidates[:50]:
            db.add(TrendSignal(keyword=c.keyword, score=c.score, source=c.source))
        await db.commit()

        existing_keywords = set(
            (await db.execute(
                select(Product.trend_keyword).where(Product.active == True)
            )).scalars().all()
        )
        fresh = [c for c in candidates if c.keyword not in existing_keywords]

        target_new = settings.daily_refresh_count
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

            dup = (await db.execute(
                select(Product).where(Product.cj_pid == matched.cj_product["pid"])
            )).scalar_one_or_none()
            if dup is not None:
                continue

            shopify_id = await shopify_client.create_product(
                title=matched.title,
                body_html=matched.description_html,
                price=matched.price,
                image_url=matched.cj_product["image_url"],
            )
            db.add(Product(
                shopify_id=shopify_id,
                cj_pid=matched.cj_product["pid"],
                title=matched.title,
                description=matched.description_html,
                price=matched.price,
                cost=matched.cj_product["sell_price"],
                image_url=matched.cj_product["image_url"],
                trend_keyword=matched.trend_keyword,
                trend_score=matched.trend_score,
            ))
            await db.commit()
            added.append(matched.title)

        active_count = (await db.execute(
            select(func.count()).select_from(Product).where(Product.active == True)
        )).scalar_one()

        overflow = active_count - settings.catalog_size
        if overflow > 0:
            stale = (await db.execute(
                select(Product)
                .where(Product.active == True)
                .order_by(Product.trend_score.asc(), Product.added_at.asc())
                .limit(overflow)
            )).scalars().all()
            for prod in stale:
                if prod.shopify_id:
                    try:
                        await shopify_client.delete_product(prod.shopify_id)
                    except Exception:
                        continue
                prod.active = False
                prod.removed_at = datetime.now(timezone.utc).replace(tzinfo=None)
                removed.append(prod.title)
            await db.commit()

    return {"added": added, "removed": removed, "trends_collected": len(candidates)}
