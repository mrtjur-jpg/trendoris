"""Product Matching Agent Ã¢ÂÂ Gemini AI vyberÃÂ¡ najlepÃÂ¡ÃÂ­ produkt a pÃÂ­ÃÂ¡e copy.

Tok:
  1. Trend keyword -> CJ search -> kandidÃÂ¡ti
  2. Gemini vyberie najvhodnejÃÂ¡ieho kandidÃÂ¡ta (alebo zamietne vÃÂ¡etkÃÂ½ch)
  3. Gemini vygeneruje predajnÃÂ½ titulok, popis (HTML) a odporuÃÂÃÂ­ cenu
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

# MinimÃÂ¡lna marÃÂ¾a: predajnÃÂ¡ cena = nÃÂ¡kupnÃÂ¡ * MARKUP, zaokrÃÂºhlenÃÂ© na .99
MARKUP = 2.8


class ProductSelection(BaseModel):
    """ÃÂ truktÃÂºrovanÃÂ½ vÃÂ½stup vÃÂ½beru produktu."""
    selected_pid: str | None  # None = ÃÂ¾iadny kandidÃÂ¡t nie je vhodnÃÂ½
    reasoning: str


class ProductCopy(BaseModel):
    """ÃÂ truktÃÂºrovanÃÂ½ vÃÂ½stup copywritingu."""
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
    image_urls: list = []  # min 3 obrÃÂ¡zky z CJ


def _client() -> genai.Client:
    return genai.Client(api_key=settings.gemini_api_key)


async def _select_best(keyword: str, candidates: list[CJProduct]) -> CJProduct | None:
    """Gemini vyberie najvhodnejÃÂ¡ÃÂ­ produkt pre danÃÂ½ trend."""
    catalog = "\n".join(
        f"- pid={c.pid} | {c.name} | cena ${c.sell_price:.2f} | listingov: {c.list_count}"
        for c in candidates
    )
    prompt = (
        f"Trending vyhÃÂ¾adÃÂ¡vanie: \"{keyword}\"\n\n"
        f"KandidÃÂ¡ti od dodÃÂ¡vateÃÂ¾a (CJ Dropshipping):\n{catalog}\n\n"
        "Vyber JEDEN produkt ktorÃÂ½ najlepÃÂ¡ie zodpovedÃÂ¡ trendu a mÃÂ¡ najvÃÂ¤ÃÂÃÂ¡ÃÂ­ "
        "predajnÃÂ½ potenciÃÂ¡l pre eurÃÂ³psky dropshipping e-shop (zvÃÂ¡ÃÂ¾ cenu, "
        "popularitu = listingov, a relevanciu k trendu). "
        "Ak ÃÂ¾iadny kandidÃÂ¡t nezodpovedÃÂ¡ trendu, vrÃÂ¡ÃÂ¥ selected_pid ako null.\n\n"
        'OdpoveÃÂ musÃÂ­ byÃÂ¥ JSON: {"selected_pid": "...", "reasoning": "..."}'
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
        logger.info("Gemini zamietol vÃÂ¡etkÃÂ½ch kandidÃÂ¡tov pre '%s'", keyword)
        return None
    chosen = next((c for c in candidates if c.pid == selection.selected_pid), None)
    if chosen:
        logger.info("VybranÃÂ½ produkt %s: %s", chosen.pid, selection.reasoning[:120])
    return chosen


async def _generate_copy(product: CJProduct, keyword: str) -> ProductCopy:
    """Gemini napÃÂ­ÃÂ¡e predajnÃÂ½ titulok + HTML popis + cenu."""
    floor_price = product.sell_price * MARKUP
    prompt = (
        f"Produkt z trendu \"{keyword}\":\n"
        f"NÃÂ¡zov dodÃÂ¡vateÃÂ¾a: {product.name}\n"
        f"Popis dodÃÂ¡vateÃÂ¾a: {product.description[:2000]}\n"
        f"NÃÂ¡kupnÃÂ¡ cena: ${product.sell_price:.2f}\n\n"
        "NapÃÂ­ÃÂ¡ pre e-shop Trendoriuso (modernÃÂ½ EU dropshipping obchod):\n"
        "OdpoveÃÂ musÃÂ­ byÃÂ¥ JSON s tÃÂ½mito poÃÂ¾ami:\n"
        "- title: chytÃÂ¾avÃÂ½ anglickÃÂ½ titulok, max 70 znakov, bez emoji\n"
        "- description_html: predajnÃÂ½ popis v HTML (h3 nadpisy, ul benefity, "
        "p odseky), 150-250 slov, anglicky, dÃÂ´raz na benefity nie parametre\n"
        f"- suggested_price_eur: psychologickÃÂ¡ cena konÃÂiaca .99, minimÃÂ¡lne {floor_price:.2f} EUR"
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
    """Mock reÃÂ¾im Ã¢ÂÂ bez Gemini: vyber najpopulÃÂ¡rnejÃÂ¡ÃÂ­ produkt + ÃÂ¡ablÃÂ³novÃÂ© copy."""
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
    """DoplnÃÂ­ obrÃÂ¡zky z detail endpointu ak ich je menej ako min_count."""
    imgs = list(chosen.image_urls)
    if len(imgs) < min_count:
        try:
            detail_imgs = await cj_client.get_product_images(chosen.pid)
            if detail_imgs:
                imgs = detail_imgs
        except Exception:
            logger.warning("Nepodarilo sa doplniÃÂ¥ obrÃÂ¡zky pre %s", chosen.pid)
    return imgs if imgs else [chosen.image_url]


async def match_trend_to_product(candidate: TrendCandidate) -> MatchedProduct | None:
    """CelÃÂ½ pipeline pre jeden trend: search -> select -> copy."""
    cj_products = await cj_client.search_products(candidate.keyword, limit=10)
    if not cj_products:
        logger.info("CJ nemÃÂ¡ produkty pre '%s'", candidate.keyword)
        return None

    if settings.mock_mode:
        return _mock_match(candidate, cj_products)

    try:
        chosen = await _select_best(candidate.keyword, cj_products)
    except Exception as e:
        if "RESOURCE_EXHAUSTED" in str(e) or "429" in str(e):
            logger.warning("Gemini 429 — template fallback pre '%s'", candidate.keyword)
            return _mock_match(candidate, cj_products)
        raise
    if chosen is None:
        return None

    image_urls = await _ensure_min_images(chosen)
    try:
        copy = await _generate_copy(chosen, candidate.keyword)
    except Exception as e:
        if "RESOURCE_EXHAUSTED" in str(e) or "429" in str(e):
            logger.warning("Gemini 429 (copy) — template fallback pre '%s'", candidate.keyword)
            return _mock_match(candidate, cj_products)
        raise
    return MatchedProduct(
        cj_product=chosen.__dict__,
        title=copy.title,
        description_html=copy.description_html,
        price=copy.suggested_price_eur,
        trend_keyword=candidate.keyword,
        trend_score=candidate.score,
        image_urls=image_urls,
    )
