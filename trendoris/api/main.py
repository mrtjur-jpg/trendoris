"""FastAPI server — webhooky, manualne spustanie jobov, health check."""
import base64
import hashlib
import hmac
import html
import logging
import os
from contextlib import asynccontextmanager

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
import httpx
from fastapi import BackgroundTasks, Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse
from sqlalchemy import select

from trendoris.agents import catalog_manager, order_agent
from trendoris.config import settings
from trendoris.db.base import AsyncSessionLocal, Base, engine
from trendoris.db.models import Order, Product, TrendSignal

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)

scheduler = AsyncIOScheduler()


@asynccontextmanager
async def lifespan(app: FastAPI):
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    scheduler.add_job(catalog_manager.daily_refresh, CronTrigger(hour=settings.cron_hour, minute=settings.cron_minute),
                      id="daily_refresh", misfire_grace_time=3600, replace_existing=True)
    scheduler.add_job(order_agent.sync_tracking, IntervalTrigger(hours=4),
                      id="tracking_sync", replace_existing=True)
    scheduler.start()
    logger.info("Scheduler bezi o %02d:%02d", settings.cron_hour, settings.cron_minute)
    yield
    scheduler.shutdown()


app = FastAPI(title="Trendoriuso AI Backend", lifespan=lifespan)


def _verify_shopify_hmac(body: bytes, hmac_header: str) -> bool:
    secret = settings.shopify_webhook_secret
    if not secret:
        if settings.mock_mode:
            return True
        raise HTTPException(status_code=503, detail="SHOPIFY_WEBHOOK_SECRET nie je nastaveny")
    digest = hmac.new(secret.encode(), body, hashlib.sha256).digest()
    expected = base64.b64encode(digest).decode()
    return hmac.compare_digest(expected, hmac_header)


def require_admin(authorization: str = Header(default="")) -> None:
    if settings.admin_token and authorization != f"Bearer {settings.admin_token}":
        raise HTTPException(status_code=401, detail="Unauthorized")


@app.get("/health")
async def health() -> dict:
    return {"status": "ok", "scheduler_running": scheduler.running}


@app.get("/", response_class=HTMLResponse)
async def dashboard() -> str:
    async with AsyncSessionLocal() as db:
        products = (await db.execute(
            select(Product).where(Product.active == True).order_by(Product.trend_score.desc())
        )).scalars().all()
        trends = (await db.execute(
            select(TrendSignal).order_by(TrendSignal.detected_at.desc()).limit(15)
        )).scalars().all()
        orders = (await db.execute(
            select(Order).order_by(Order.created_at.desc()).limit(15)
        )).scalars().all()

    e = html.escape
    product_rows = "".join(
        f"<tr><td>{e(p.title)}</td><td>{e(p.trend_keyword)}</td>"
        f"<td>{p.trend_score:.0f}</td><td>\u20ac{p.price:.2f}</td></tr>"
        for p in products
    ) or "<tr><td colspan='4' class='empty'>Katalog je prazdny</td></tr>"

    trend_rows = "".join(
        f"<tr><td>{e(t.keyword)}</td><td>{t.score:.0f}</td><td>{t.detected_at:%d.%m. %H:%M}</td></tr>"
        for t in trends
    ) or "<tr><td colspan='3' class='empty'>Ziadne trendy</td></tr>"

    order_rows = "".join(
        f"<tr><td>{e(o.shopify_order_id)}</td><td>{e(o.status)}</td>"
        f"<td>\u20ac{o.total_price:.2f}</td><td>{e(o.tracking_number or '-')}</td></tr>"
        for o in orders
    ) or "<tr><td colspan='4' class='empty'>Ziadne objednavky</td></tr>"

    mock_badge = "<span class='badge mock'>MOCK MODE</span>" if settings.mock_mode else ""

    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>Trendoriuso Dashboard</title></head><body>
<h1>Trendoriuso {mock_badge}</h1>
</body></html>"""


@app.post("/webhooks/shopify/orders-paid")
async def shopify_order_paid(request: Request, background: BackgroundTasks,
                              x_shopify_hmac_sha256: str = Header(default="")) -> dict:
    body = await request.body()
    if not _verify_shopify_hmac(body, x_shopify_hmac_sha256):
        raise HTTPException(status_code=401, detail="Invalid HMAC")
    payload = await request.json()
    background.add_task(order_agent.handle_new_order, payload)
    return {"ok": True}


@app.post("/admin/refresh-catalog", dependencies=[Depends(require_admin)])
async def manual_refresh() -> dict:
    return await catalog_manager.daily_refresh()


@app.post("/admin/sync-tracking", dependencies=[Depends(require_admin)])
async def manual_tracking_sync() -> dict:
    updated = await order_agent.sync_tracking()
    return {"updated": updated}


@app.get("/shopify/callback")
async def shopify_callback(
    code: str = Query(...),
    shop: str = Query(...),
    state: str = Query(default=""),
    timestamp: str = Query(default=""),
    host: str = Query(default=""),
) -> JSONResponse:
    """Shopify OAuth callback -- vymeni code za permanent access token."""
    client_id = os.environ.get("SHOPIFY_API_KEY", "")
    client_secret = os.environ.get("SHOPIFY_API_SECRET", "")
    if not client_id or not client_secret:
        raise HTTPException(status_code=503, detail="SHOPIFY_API_KEY/SECRET nie su nastavene")
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"https://{shop}/admin/oauth/access_token",
            json={"client_id": client_id, "client_secret": client_secret, "code": code},
        )
    if resp.status_code != 200:
        raise HTTPException(status_code=resp.status_code, detail=resp.text)
    data = resp.json()
    access_token = data.get("access_token", "")
    logger.info("Shopify token pre shop=%s: %s...", shop, access_token[:8])
    return JSONResponse({"access_token": access_token, "scope": data.get("scope", ""), "shop": shop})
