import json
import logging

from sqlalchemy import select

from trendoris.db.base import AsyncSessionLocal
from trendoris.db.models import Order, Product
from trendoris.services.cj_client import cj_client
from trendoris.services.shopify_client import shopify_client

logger = logging.getLogger(__name__)


async def handle_new_order(webhook_payload: dict) -> None:
    shopify_order_id = str(webhook_payload["id"])
    email = webhook_payload.get("email", "")
    shipping = webhook_payload.get("shipping_address") or {}

    async with AsyncSessionLocal() as db:
        existing = (await db.execute(
            select(Order).where(Order.shopify_order_id == shopify_order_id)
        )).scalar_one_or_none()
        if existing is not None:
            return

        for line_item in webhook_payload.get("line_items", []):
            product_id = str(line_item.get("product_id", ""))
            product = (await db.execute(
                select(Product).where(Product.shopify_id == product_id)
            )).scalar_one_or_none()
            if product is None:
                continue

            quantity = int(line_item.get("quantity", 1))
            detail = await cj_client.get_product_detail(product.cj_pid)
            variants = detail.get("variants") or []
            if not variants:
                continue
            vid = variants[0]["vid"]

            try:
                cj_order_id = await cj_client.create_order(
                    order_number=f"TRD-{shopify_order_id}-{product.id}",
                    shipping_name=f"{shipping.get('first_name', '')} {shipping.get('last_name', '')}".strip(),
                    shipping_address=f"{shipping.get('address1', '')} {shipping.get('address2', '') or ''}".strip(),
                    shipping_city=shipping.get("city", ""),
                    shipping_country_code=shipping.get("country_code", ""),
                    shipping_zip=shipping.get("zip", ""),
                    shipping_phone=shipping.get("phone") or webhook_payload.get("phone") or "",
                    vid=vid,
                    quantity=quantity,
                )
                status = "forwarded"
            except Exception:
                logger.exception("CJ objednavka zlyhala pre %s", shopify_order_id)
                cj_order_id = None
                status = "failed"

            db.add(Order(
                shopify_order_id=shopify_order_id,
                product_id=product.id,
                cj_order_id=cj_order_id,
                customer_email=email,
                shipping_address=json.dumps(shipping, ensure_ascii=False),
                quantity=quantity,
                total_price=float(webhook_payload.get("total_price", 0)),
                status=status,
            ))
        await db.commit()


async def sync_tracking() -> int:
    updated = 0
    async with AsyncSessionLocal() as db:
        pending = (await db.execute(
            select(Order).where(
                Order.status == "forwarded",
                Order.tracking_number.is_(None),
                Order.cj_order_id.is_not(None),
            )
        )).scalars().all()

        for order in pending:
            try:
                tracking = await cj_client.get_tracking(order.cj_order_id)
            except Exception:
                continue
            if not tracking:
                continue
            try:
                await shopify_client.add_tracking(order.shopify_order_id, tracking)
            except Exception:
                continue
            order.tracking_number = tracking
            order.status = "shipped"
            updated += 1
        await db.commit()
    return updated
