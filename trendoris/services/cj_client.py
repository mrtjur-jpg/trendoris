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
    description: str
    list_count: int


class CJClient:
    def __init__(self) -> None:
        self._token: str | None = None
        self._client = httpx.AsyncClient(base_url=BASE_URL, timeout=30)

    async def _ensure_token(self) -> str:
        if self._token:
            return self._token
        resp = await self._client.post("/authentication/getAccessToken", json={
            "email": settings.cj_email,
            "password": settings.cj_api_key,
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
            self._token = None
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
        data = await self._get("/product/list", {
            "productNameEn": keyword,
            "pageSize": limit,
            "pageNum": 1,
        })
        products = []
        for item in data.get("data", {}).get("list", []):
            products.append(CJProduct(
                pid=item["pid"],
                name=item.get("productNameEn", ""),
                sell_price=float(item.get("sellPrice", 0) or 0),
                image_url=item.get("productImage", ""),
                description=item.get("description", "") or "",
                list_count=int(item.get("listedNum", 0) or 0),
            ))
        return products

    async def get_product_detail(self, pid: str) -> dict:
        data = await self._get("/product/query", {"pid": pid})
        return data.get("data", {})

    async def create_order(self, order_number, shipping_name, shipping_address,
                           shipping_city, shipping_country_code, shipping_zip,
                           shipping_phone, vid, quantity) -> str:
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
