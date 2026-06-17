"""Product Matching Agent â Gemini AI vyberÃ¡ najlepÅ¡Ã­ produkt a pÃ­Å¡e copy.

Tok:
  1. Trend keyword -> CJ search -> kandidÃ¡ti
  2. Gemini vyberie najvhodnejÅ¡ieho kandidÃ¡ta (alebo zamietne vÅ¡etkÃ½ch)
  3. Gemini vygeneruje predajnÃ½ titulok, popis (HTML) a odporuÄÃ­ cenu
"""
import json
import logging

from google import genai
from google.genai import types
from pydantic import BaseModel

from trendoris.agents.trend_agent import TrendCandidate
from trendoris.config import settings
from trendoris.services.cj_client import CJProduct, cj_client

logger = logging.getLogger(__name__)

MODEL = "gemini-2.5-flash-lite"

# MinimÃ¡lna marÅ¾a: predajnÃ¡ cena = nÃ¡kupnÃ¡ * MARKUP, zaokrÃºhlenÃ© na .99
MARKUP = 2.8


class ProductSelection(BaseModel):
    """Å truktÃºrovanÃ½ vÃ½stup vÃ½beru produktu."""
    selected_pid: str | None  # None = Å¾iadny kandidÃ¡t nie je vhodnÃ½
    reasoning: str


class ProductCopy(BaseModel):
    """Å truktÃºrovanÃ½ vÃ½stup copywritingu."""
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
    image_urls: list = []  # min 3 obrÃ¡zky z CJ


def _client() -> genai.Client:
    return genai.Client(api_key=settings.gemini_api_key)


async def _select_best(keyword: str, candidates: list[CJProduct]) -> CJProduct | None:
    """Gemini vyberie najvhodnejÅ¡Ã­ produkt pre danÃ½ trend."""
    catalog = "\n".join(
        f"- pid={c.pid} | {c.name} | cena ${c.sell_price:.2f} | listingov: {c.list_count}"
        for c in candidates
    )
    prompt = (
        f"Trending vyhÄ¾adÃ¡vanie: \"{keyword}\"\n\n"
        f"KandidÃ¡ti od dodÃ¡vateÄ¾a (CJ Dropshipping):\n{catalog}\n\n"
        "Vyber JEDEN produkt ktorÃ½ najlepÅ¡ie zodpovedÃ¡ trendu a mÃ¡ najvÃ¤ÄÅ¡Ã­ "
        "predajnÃ½ potenciÃ¡l pre eurÃ³psky dropshipping e-shop (zvÃ¡Å¾ cenu, "
        "popularitu = listingov, a relevanciu k trendu). "
        "Ak Å¾iadny kandidÃ¡t nezodpovedÃ¡ trendu, vrÃ¡Å¥ selected_pid ako null.\n\n"
        'OdpoveÄ musÃ­ byÅ¥ JSON: {"selected_pid": "...", "reasoning": "..."}'
    )
    client = _client()
    response = await client.aio.models.generate_content(
        model=MODEL,
        contents=prompt,
        config=types.GenerateContentConfig(response_mime_type="application/json"),
    )
    data = json.loads(response.text)
    selection = ProductSelection(**data)

    if selection.selected_pid is None:
        logger.info("Gemini zamietol vÅ¡etkÃ½ch kandidÃ¡tov pre '%s'", keyword)
        return None
    chosen = next((c for c in candidates if c.pid == selection.selected_pid), None)
    if chosen:
        logger.info("VybranÃ½ produkt %s: %s", chosen.pid, selection.reasoning[:120])
    return chosen


async def _generate_copy(product: CJProduct, keyword: str) -> ProductCopy:
    """Gemini napÃ­Å¡e predajnÃ½ titulok + HTML popis + cenu."""
    floor_price = product.sell_price * MARKUP
    prompt = (
        f"Produkt z trendu \"{keyword}\":\n"
        f"NÃ¡zov dodÃ¡vateÄ¾a: {product.name}\n"
        f"Popis dodÃ¡vateÄ¾a: {product.description[:2000]}\n"
        f"NÃ¡kupnÃ¡ cena: ${product.sell_price:.2f}\n\n"
        "NapÃ­Å¡ pre e-shop Trendoriuso (modernÃ½ EU dropshipping obchod):\n"
        "OdpoveÄ musÃ­ byÅ¥ JSON s tÃ½mito poÄ¾ami:\n"
        "- title: chytÄ¾avÃ½ anglickÃ½ titulok, max 70 znakov, bez emoji\n"
        "- description_html: predajnÃ½ popis v HTML (h3 nadpisy, ul benefity, "
        "p odseky), 150-250 slov, anglicky, dÃ´raz na benefity nie parametre\n"
        f"- suggested_price_eur: psychologickÃ¡ cena konÄiaca .99, minimÃ¡lne {floor_price:.2f} EUR"
    )
    client = _client()
    response = await client.aio.models.generate_content(
        model=MODEL,
        contents=prompt,
        config=types.GenerateContentConfig(response_mime_type="application/json"),
    )
    data = json.loads(response.text)
    copy = ProductCopy(**data)
    if copy.suggested_price_eur < floor_price:
        copy.suggested_price_eur = round(floor_price) + 0.99
    return copy


def _mock_match(candidate: TrendCandidate, cj_products: list[CJProduct]) -> MatchedProduct:
    """Mock reÅ¾im â bez Gemini: vyber najpopulÃ¡rnejÅ¡Ã­ produkt + Å¡ablÃ³novÃ© copy."""
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
    """DoplnÃ­ obrÃ¡zky z detail endpointu ak ich je menej ako min_count."""
    imgs = list(chosen.image_urls)
    if len(imgs) < min_count:
        try:
            detail_imgs = await cj_client.get_product_images(chosen.pid)
            if detail_imgs:
                imgs = detail_imgs
        except Exception:
            logger.warning("Nepodarilo sa doplniÅ¥ obrÃ¡zky pre %s", chosen.pid)
    return imgs if imgs else [chosen.image_url]


async def match_trend_to_product(candidate: TrendCandidate) -> MatchedProduct | None:
    """CelÃ½ pipeline pre jeden trend: search -> select -> copy."""
    cj_products = await cj_client.search_products(candidate.keyword, limit=10)
    if not cj_products:
        logger.info("CJ nemÃ¡ produkty pre '%s'", candidate.keyword)
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
