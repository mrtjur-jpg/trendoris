"""Mock klienti — celý pipeline beží bez API kľúčov (MOCK_MODE=true).

Deterministické falošné dáta: rovnaký keyword vždy vygeneruje rovnaké produkty,
takže sa dá testovať aj idempotencia katalógu.
"""
import hashlib
import logging

from trendoris.services.cj_client import CJProduct

logger = logging.getLogger(__name__)


def _seed(keyword: str) -> int:
    return int(hashlib.md5(keyword.encode()).hexdigest()[:6], 16)


class MockCJClient:
    """Falošný CJ Dropshipping — generuje produkty z keywordu."""

    async def search_products(self, keyword: str, limit: int = 10) -> list[CJProduct]:
        base = _seed(keyword)
        count = min(limit, 5)
        products = []
        for i, suffix in enumerate(["Pro", "Ultra", "Mini", "Max Edition", "2.0"][:count]):
            imgs = [
                f"https://picsum.photos/seed/{base + i}/600/600",
                f"https://picsum.photos/seed/{base + i + 100}/600/600",
                f"https://picsum.photos/seed/{base + i + 200}/600/600",
            ]
            products.append(CJProduct(
                pid=f"MOCK-{base}-{i}",
                name=f"{keyword.title()} {suffix}",
                sell_price=round(3.5 + (base % 17) + i * 1.85, 2),
                image_url=imgs[0],
                image_urls=imgs,
                description=f"Demo dodávateľský popis pre {keyword} (variant {i + 1}).",
                list_count=(base + i * 137) % 500,
            ))
        logger.info("[MOCK] CJ search '%s' -> %d produktov", keyword, len(products))
        return products

    async def get_product_detail(self, pid: str) -> dict:
        return {"variants": [{"vid": f"{pid}-V1"}]}

    async def get_product_images(self, pid: str) -> list:
        base = _seed(pid)
        return [
            f"https://picsum.photos/seed/{base}/600/600",
            f"https://picsum.photos/seed/{base + 100}/600/600",
            f"https://picsum.photos/seed/{base + 200}/600/600",
        ]

    async def create_order(self, order_number: str, **kwargs) -> str:
        logger.info("[MOCK] CJ objednávka vytvorená: %s", order_number)
        return f"CJMOCK-{order_number}"

    async def get_tracking(self, cj_order_id: str) -> str | None:
        return f"TRK{_seed(cj_order_id) % 10**9:09d}"

    async def close(self) -> None:
        pass


class MockShopifyClient:
    """Falošný Shopify — produkty si drží len v pamäti, loguje akcie."""

    def __init__(self) -> None:
        self._next_id = 9000
        self.products: dict[str, dict] = {}

    async def create_product(
        self, title: str, body_html: str, price: float,
        image_urls: list, tags: str = "trendoriuso-auto",
    ) -> str:
        self._next_id += 1
        product_id = str(self._next_id)
        self.products[product_id] = {"title": title, "price": price}
        logger.info("[MOCK] Shopify produkt vytvorený: %s (€%.2f) id=%s", title, price, product_id)
        return product_id

    async def delete_product(self, product_id: str) -> None:
        self.products.pop(product_id, None)
        logger.info("[MOCK] Shopify produkt zmazaný: %s", product_id)

    async def list_products(self, limit: int = 250) -> list[dict]:
        return list(self.products.values())[:limit]

    async def get_order(self, order_id: str) -> dict:
        return {"id": order_id}

    async def add_tracking(self, order_id: str, tracking_number: str) -> None:
        logger.info("[MOCK] Tracking %s -> objednávka %s", tracking_number, order_id)

    async def close(self) -> None:
        pass


# Falošné trendy pre mock režim — realistické dropshipping keywordy
MOCK_TRENDS = [
    "magnetic phone mount",
    "mini thermal printer",
    "led galaxy projector",
    "smart water bottle",
    "foldable laptop stand",
    "portable mini blender",
    "sunset projection lamp",
    "electric jar opener",
]
