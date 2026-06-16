"""CJ Dropshipping API klient (https://developers.cjdropshipping.com).

AutentifikÃÂ¡cia: API key -> access token (platÃÂ­ 15 dnÃÂ­, refreshujeme pri 401).
"""
import logging
from dataclasses import dataclass

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential

from trendoris.config import settings

logger = logging.getLogger(__name__)

BASE_URL = "https://developers.cjdropshipping.com/api2.0/v1"


@dataclass
class CJProduct:
    pid: str
    name: str
    sell_price: float
    image_url: str
    image_urls: list  # vÃÂ¡etky obrÃÂ¡zky produktu (min 1, ideÃÂ¡lne 3+)
    description: str
    list_count: int  # poÃÂet listingov = proxy popularity


def _parse_price(value) -> float:
    """CJ niekedy vracia cenu ako rozsah '2.02 -- 2.72' — berieme minimum."""
    if not value:
        return 0.0
    s = str(value).split("--")[0].strip()
    try:
        return float(s)
    except ValueError:
        return 0.0


class CJClient:
    def __init__(self) -> None:
        self._token: str | None = None
        self._client = httpx.AsyncClient(base_url=BASE_URL, timeout=30)

    async def _ensure_token(self) -> str:
        if self._token:
            return self._token
        resp = await self._client.post("/authentication/getAccessToken", json={
            "apiKey": settings.cj_api_key,
        })
        resp.raise_for_status()
        data = resp.json()
        if not data.get("result"):
            raise RuntimeError(f"CJ auth zlyhal: {data.get('message')}")
        self._token = data["data"]["accessToken"]
        return self._token

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=10))
    async def _get(self, path: str, params: dict) -> dict:
        token = await self._ensure_token()
        resp = await self._client.get(path, params=params, headers={"CJ-Access-Token": token})
        if resp.status_code == 401:
            self._token = None  # token expiroval Ã¢ÂÂ ÃÂalÃÂ¡ÃÂ­ retry si vypÃÂ½ta novÃÂ½
            resp.raise_for_status()
        resp.raise_for_status()
        return resp.json()

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=10))
    async def _post(self, path: str, body: dict) -> dict:
        token = await self._ensure_token()
        resp = await self._client.post(path, json=body, headers={"CJ-Access-Token": token})
        if resp.status_code == 401:
            self._token = None
            resp.raise_for_status()
        resp.raise_for_status()
        return resp.json()

    async def search_products(self, keyword: str, limit: int = 10) -> list[CJProduct]:
        """VyhÃÂ¾adÃÂ¡ produkty podÃÂ¾a keywordu, zoradenÃÂ© podÃÂ¾a popularity."""
        data = await self._get("/product/list", {
            "productNameEn": keyword,
            "pageSize": limit,
            "pageNum": 1,
        })
        products = []
        for item in data.get("data", {}).get("list", []):
            image_url = item.get("productImage", "")
            img_set = item.get("productImageSet", "") or ""
            if isinstance(img_set, list):
                image_urls = [u for u in img_set if u]
            elif isinstance(img_set, str) and img_set:
                image_urls = [u.strip() for u in img_set.split(",") if u.strip()]
            else:
                image_urls = []
            if image_url and image_url not in image_urls:
                image_urls = [image_url] + image_urls
            elif not image_urls and image_url:
                image_urls = [image_url]
            products.append(CJProduct(
                pid=item["pid"],
                name=item.get("productNameEn", ""),
                sell_price=_parse_price(item.get("sellPrice", 0)),
                image_url=image_url,
                image_urls=image_urls,
                description=item.get("description", "") or "",
                list_count=int(item.get("listedNum", 0) or 0),
            ))
        return products

    async def get_product_detail(self, pid: str) -> dict:
        data = await self._get("/product/query", {"pid": pid})
        return data.get("data", {})

    async def get_product_images(self, pid: str) -> list:
        """VrÃÂ¡ti vÃÂ¡etky obrÃÂ¡zky produktu (min 3) z detail endpointu CJ."""
        try:
            detail = await self.get_product_detail(pid)
            img_set = detail.get("productImageSet", [])
            if isinstance(img_set, str):
                imgs = [u.strip() for u in img_set.split(",") if u.strip()]
            elif isinstance(img_set, list):
                imgs = [u for u in img_set if u]
            else:
                imgs = []
            main = detail.get("productImage", "")
            if main and main not in imgs:
                imgs = [main] + imgs
            return imgs[:8]
        except Exception:
            logger.warning("Nepodarilo sa zÃÂ­skaÃÂ¥ obrÃÂ¡zky pre pid=%s", pid)
            return []

    async def create_order(
        self,
        order_number: str,
        shipping_name: str,
        shipping_address: str,
        shipping_city: str,
        shipping_country_code: str,
        shipping_zip: str,
        shipping_phone: str,
        vid: str,
        quantity: int,
    ) -> str:
        """VytvorÃÂ­ objednÃÂ¡vku u CJ. VracÃÂ­a CJ order ID."""
        data = await self._post("/shopping/order/createOrderV2", {
            "orderNumber": order_number,
            "shippingCountryCode": shipping_country_code,
            "shippingProvince": "",
            "shippingCity": shipping_city,
            "shippingAddress": shipping_address,
            "shippingCustomerName": shipping_name,
            "shippingZip": shipping_zip,
            "shippingPhone": shipping_phone,
            "logisticName": "CJPacket Ordinary",
            "fromCountryCode": "CN",
            "products": [{"vid": vid, "quantity": quantity}],
        })
        if not data.get("result"):
            raise RuntimeError(f"CJ order zlyhal: {data.get('message')}")
        return data["data"]["orderId"]

    async def get_tracking(self, cj_order_id: str) -> str | None:
        data = await self._get("/logistic/getTrackInfo", {"orderId": cj_order_id})
        info = data.get("data") or []
        if info:
            return info[0].get("trackingNumber")
        return None

    async def close(self) -> None:
        await self._client.aclose()


if settings.mock_mode:
    from trendoris.services.mocks import MockCJClient
    cj_client = MockCJClient()
else:
    cj_client = CJClient()
