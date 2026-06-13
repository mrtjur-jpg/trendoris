import logging

import anthropic
from pydantic import BaseModel

from trendoris.agents.trend_agent import TrendCandidate
from trendoris.config import settings
from trendoris.services.cj_client import CJProduct, cj_client

logger = logging.getLogger(__name__)

MODEL = "claude-opus-4-8"
MARKUP = 2.8


class ProductSelection(BaseModel):
    selected_pid: str | None
    reasoning: str


class ProductCopy(BaseModel):
    title: str
    description_html: str
    suggested_price_eur: float


class MatchedProduct(BaseModel):
    cj_product: dict
    title: str
    description_html: str
    price: float
    trend_keyword: str
    trend_score: float


_client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)


async def _select_best(keyword: str, candidates: list[CJProduct]) -> CJProduct | None:
    catalog = "\n".join(
        f"- pid={c.pid} | {c.name} | cena ${c.sell_price:.2f} | listingov: {c.list_count}"
        for c in candidates
    )
    response = await _client.messages.parse(
        model=MODEL,
        max_tokens=2048,
        thinking={"type": "adaptive"},
        messages=[{"role": "user", "content": (
            f"Trending: \"{keyword}\"\n\nKandidati:\n{catalog}\n\n"
            "Vyber JEDEN produkt s najvacsim predajnym potencialom pre EU dropshipping."
        )}],
        output_format=ProductSelection,
    )
    selection = response.parsed_output
    if selection is None or selection.selected_pid is None:
        return None
    return next((c for c in candidates if c.pid == selection.selected_pid), None)


async def _generate_copy(product: CJProduct, keyword: str) -> ProductCopy:
    floor_price = product.sell_price * MARKUP
    response = await _client.messages.parse(
        model=MODEL,
        max_tokens=4096,
        thinking={"type": "adaptive"},
        messages=[{"role": "user", "content": (
            f"Produkt: {product.name}\nPopis: {product.description[:2000]}\n"
            f"Nakupna cena: ${product.sell_price:.2f}\n\n"
            "Napis pre Trendoriuso e-shop:\n"
            "1. title (max 70 znakov)\n"
            "2. description_html (HTML, 150-250 slov)\n"
            f"3. suggested_price_eur (min {floor_price:.2f} EUR, konciaca .99)"
        )}],
        output_format=ProductCopy,
    )
    copy = response.parsed_output
    if copy is None:
        raise RuntimeError(f"Copywriting zlyhal pre {product.pid}")
    if copy.suggested_price_eur < floor_price:
        copy.suggested_price_eur = round(floor_price) + 0.99
    return copy


def _mock_match(candidate: TrendCandidate, cj_products: list[CJProduct]) -> MatchedProduct:
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
    )


async def match_trend_to_product(candidate: TrendCandidate) -> MatchedProduct | None:
    cj_products = await cj_client.search_products(candidate.keyword, limit=10)
    if not cj_products:
        return None
    if settings.mock_mode:
        return _mock_match(candidate, cj_products)
    chosen = await _select_best(candidate.keyword, cj_products)
    if chosen is None:
        return None
    copy = await _generate_copy(chosen, candidate.keyword)
    return MatchedProduct(
        cj_product=chosen.__dict__,
        title=copy.title,
        description_html=copy.description_html,
        price=copy.suggested_price_eur,
        trend_keyword=candidate.keyword,
        trend_score=candidate.score,
    )
