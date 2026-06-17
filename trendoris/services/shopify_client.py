"""Shopify Admin API klient (REST, verzia 2024-10)."""
import logging

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential

from trendoris.config import settings

logger = logging.getLogger(__name__)

API_VERSION = "2024-10"


class ShopifyClient:
    def __init__(self) -> None:
        self._client = httpx.AsyncClient(
            base_url=f"https://{settings.shopify_store}/admin/api/{API_VERSION}",
            headers={
                "X-Shopify-Access-Token": settings.shopify_access_token,
                "Content-Type": "application/json",
            },
            timeout=30,
        )

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=10))
    async def create_product(
        self,
        title: str,
        body_html: str,
        price: float,
        image_urls: list,
        tags: str = "trendoriuso-auto",
    ) -> str:
        """Vytvorí produkt, vracia Shopify product ID."""
        # Shopify odmieta mnohé externé CDN URL (napr. CJ) ako "Image URL is invalid".
        # Preto najskôr vytvoríme produkt bez obrázkov, potom ich skúsime pridať.
        resp = await self._client.post("/products.json", json={
            "product": {
                "title": title,
                "body_html": body_html,
                "vendor": "Trendoriuso",
                "tags": tags,
                "status": "active",
                "variants": [{
                    "price": f"{price:.2f}",
                    "requires_shipping": True,
                }],
            }
        })
        if not resp.is_success:
            logger.error("Shopify create detail: %s", resp.text[:500])
        resp.raise_for_status()
        product_id = str(resp.json()["product"]["id"])

        # Pokus o pridanie obrázkov zvlášť — chyba tu NIE JE fatálna
        for url in (image_urls or []):
            if not url:
                continue
            img_resp = await self._client.post(
                f"/products/{product_id}/images.json",
                json={"image": {"src": url}},
            )
            if img_resp.is_success:
                break  # stačí jeden hlavný obrázok
            logger.warning("Obrázok odmietnutý Shopify (%s): %s", img_resp.status_code, url[:100])

        logger.info("Shopify produkt vytvorený: %s (%s)", title, product_id)
        return product_id

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=10))
    async def delete_product(self, product_id: str) -> None:
        resp = await self._client.delete(f"/products/{product_id}.json")
        if resp.status_code not in (200, 404):
            resp.raise_for_status()
        logger.info("Shopify produkt zmazaný: %s", product_id)

    async def list_products(self, limit: int = 250) -> list[dict]:
        resp = await self._client.get("/products.json", params={"limit": limit})
        resp.raise_for_status()
        return resp.json().get("products", [])

    async def get_order(self, order_id: str) -> dict:
        resp = await self._client.get(f"/orders/{order_id}.json")
        resp.raise_for_status()
        return resp.json()["order"]

    async def add_tracking(self, order_id: str, tracking_number: str) -> None:
        """Vytvorí fulfillment s tracking číslom."""
        resp = await self._client.get(f"/orders/{order_id}/fulfillment_orders.json")
        resp.raise_for_status()
        fulfillment_orders = resp.json().get("fulfillment_orders", [])
        if not fulfillment_orders:
            logger.warning("Objednávka %s nemá fulfillment orders", order_id)
            return

        fo_id = fulfillment_orders[0]["id"]
        resp = await self._client.post("/fulfillments.json", json={
            "fulfillment": {
                "line_items_by_fulfillment_order": [{"fulfillment_order_id": fo_id}],
                "tracking_info": {
                    "number": tracking_number,
                    "company": "CJPacket",
                },
                "notify_customer": True,
            }
        })
        resp.raise_for_status()
        logger.info("Tracking %s pridaný k objednávke %s", tracking_number, order_id)

    async def close(self) -> None:
        await self._client.aclose()


if settings.mock_mode:
    from trendoris.services.mocks import MockShopifyClient
    shopify_client = MockShopifyClient()
else:
    shopify_client = ShopifyClient()
