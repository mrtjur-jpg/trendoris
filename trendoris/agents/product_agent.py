"""Product Matching Agent — Claude AI vyberá najlepší produkt a píše copy.

Tok:
  1. Trend keyword -> CJ search -> kandidáti
  2. Claude vyberie najlepšieho kandidáta (alebo zamietne všetkých)
  3. Claude vygeneruje predajný titulok, popis (HTML) a odporučí cenu
"""
import logging

import anthropic
from pydantic import BaseModel

from trendoris.agents.trend_agent import TrendCandidate
from trendoris.config import settings
from trendoris.services.cj_client import CJProduct, cj_client

logger = logging.getLogger(__name__)

MODEL = "claude-opus-4-8"

# Minimálna marža: predajná cena = nákupná * MARKUP, zaokrúhlené na .99
MARKUP = 2.8


class ProductSelection(BaseModel):
    """Štruktúrovaný výstup výberu produktu."""
    selected_pid: str | None  # None = žiadny kandidát nie je vhodný
    reasoning: str


class ProductCopy(BaseModel):
    """Štruktúrovaný výstup copywritingu."""
    title: str
    description_html: str
    suggested_price_eur: float


class MatchedProduct(BaseModel):
    cj_product: dict  # CJProduct as dict
    title: str
    description_html: str
    price: float
    trend_keyword: str
    trend_score: float
    image_urls: list = []  # min 3 obrázky z CJ


_client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)


async def _select_best(keyword: str, candidates: list[CJProduct]) -> CJProduct | None:
    """Claude vyberie najvhodnejší produkt pre daný trend."""
    catalog = "\n".join(
        f"- pid={c.pid} | {c.name} | cena ${c.sell_price:.2f} | listingov: {c.list_count}"
        for c in candidates
    )
    response = await _client.messages.parse(
        model=MODEL,
        max_tokens=2048,
        thinking={"type": "adaptive"},
        messages=[{
            "role": "user",
            "content": (
                f"Trending vyhľadávanie: \"{keyword}\"\n\n"
                f"Kandidáti od dodávateľa (CJ Dropshipping):\n{catalog}\n\n"
                "Vyber JEDEN produkt ktorý najlepšie zodpovedá trendu a má najväčší "
                "predajný potenciál pre európsky dropshipping e-shop (zváž cenu, "
                "popularitu = listingov, a relevanciu k trendu). "
                "Ak žiadny kandidát nezodpovedá trendu alebo všetky vyzerajú nekvalitne, "
                "vráť selected_pid=null."
            ),
        }],
        output_format=ProductSelection,
    )
    selection = response.parsed_output
    if selection is None or selection.selected_pid is None:
        logger.info("Claude zamietol všetkých kandidátov pre '%s'", keyword)
        return None
    chosen = next((c for c in candidates if c.pid == selection.selected_pid), None)
    if chosen:
        logger.info("Vybraný produkt %s: %s", chosen.pid, selection.reasoning[:120])
    return chosen


async def _generate_copy(product: CJProduct, keyword: str) -> ProductCopy:
    """Claude napíše predajný titulok + HTML popis + cenu."""
    floor_price = product.sell_price * MARKUP
    response = await _client.messages.parse(
        model=MODEL,
        max_tokens=4096,
        thinking={"type": "adaptive"},
        messages=[{
            "role": "user",
            "content": (
                f"Produkt z trendu \"{keyword}\":\n"
                f"Názov dodávateľa: {product.name}\n"
                f"Popis dodávateľa: {product.description[:2000]}\n"
                f"Nákupná cena: ${product.sell_price:.2f}\n\n"
                "Napíš pre e-shop Trendoriuso (moderný EU dropshipping obchod):\n"
                "1. title — chytľavý anglický titulok, max 70 znakov, bez emoji\n"
                "2. description_html — predajný popis v HTML (h3 nadpisy, ul benefity, "
                "p odseky), 150-250 slov, anglicky, dôraz na benefity nie parametre\n"
                f"3. suggested_price_eur — psychologická cena končiaca .99, "
                f"minimálne {floor_price:.2f} EUR (pokrýva nákup + maržu)"
            ),
        }],
        output_format=ProductCopy,
    )
    copy = response.parsed_output
    if copy is None:
        raise RuntimeError(f"Copywriting zlyhal pre {product.pid}")
    # Bezpečnostná poistka na maržu — Claude mohol navrhnúť nižšiu cenu
    if copy.suggested_price_eur < floor_price:
        copy.suggested_price_eur = round(floor_price) + 0.99
    return copy


def _mock_match(candidate: TrendCandidate, cj_products: list[CJProduct]) -> MatchedProduct:
    """Mock režim — bez Claude: vyber najpopulárnejší produkt + šablónové copy."""
    chosen = max(cj_products, key=lambda c: c.list_count)
    price = round(chosen.sell_price * MARKUP) + 0.99
    return MatchedProduct(
        cj_product=chosen.__dict__,
        title=chosen.name[:70],
        description_html=(
            f"<h3>Trending: {candidate.keyword.title()}</h3>"
            f"<p>{chosen.description}</p>"
            "<ul><li>Fast EU shipping</li><li>30-day returns</li>"
            "<li>As seen on social media</li></ul>"
        ),
        price=price,
        trend_keyword=candidate.keyword,
        trend_score=candidate.score,
        image_urls=chosen.image_urls,
    )


async def _ensure_min_images(chosen: CJProduct, min_count: int = 3) -> list:
    """Doplní obrázky z detail endpointu ak ich je menej ako min_count."""
    imgs = list(chosen.image_urls)
    if len(imgs) < min_count:
        try:
            detail_imgs = await cj_client.get_product_images(chosen.pid)
            if detail_imgs:
                imgs = detail_imgs
        except Exception:
            logger.warning("Nepodarilo sa doplniť obrázky pre %s", chosen.pid)
    return imgs if imgs else [chosen.image_url]


async def match_trend_to_product(candidate: TrendCandidate) -> MatchedProduct | None:
    """Celý pipeline pre jeden trend: search -> select -> copy."""
    cj_products = await cj_client.search_products(candidate.keyword, limit=10)
    if not cj_products:
        logger.info("CJ nemá produkty pre '%s'", candidate.keyword)
        return None

    if settings.mock_mode:
        return _mock_match(candidate, cj_products)

    chosen = await _select_best(candidate.keyword, cj_products)
    if chosen is None:
        return None

    image_urls = await _ensure_min_images(chosen)
    copy = await _generate_copy(chosen, candidate.keyword)
    return MatchedProduct(
        cj_product=chosen.__dict__,
        title=copy.title,
        description_html=copy.description_html,
        price=copy.suggested_price_eur,
        trend_keyword=candidate.keyword,
        trend_score=candidate.score,
        image_urls=image_urls,
    )
