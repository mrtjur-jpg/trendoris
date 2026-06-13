from datetime import datetime
from sqlalchemy import String, Float, Integer, DateTime, Boolean, Text, ForeignKey, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from trendoris.db.base import Base


class TrendSignal(Base):
    __tablename__ = "trend_signals"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    keyword: Mapped[str] = mapped_column(String(255), index=True)
    score: Mapped[float] = mapped_column(Float)
    source: Mapped[str] = mapped_column(String(64))
    detected_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class Product(Base):
    __tablename__ = "products"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    shopify_id: Mapped[str | None] = mapped_column(String(64), unique=True, nullable=True)
    cj_pid: Mapped[str] = mapped_column(String(128), unique=True)
    title: Mapped[str] = mapped_column(String(512))
    description: Mapped[str] = mapped_column(Text)
    price: Mapped[float] = mapped_column(Float)
    cost: Mapped[float] = mapped_column(Float)
    image_url: Mapped[str] = mapped_column(Text)
    trend_keyword: Mapped[str] = mapped_column(String(255))
    trend_score: Mapped[float] = mapped_column(Float, default=0.0)
    added_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    removed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)

    orders: Mapped[list["Order"]] = relationship("Order", back_populates="product")


class Order(Base):
    __tablename__ = "orders"
    __table_args__ = (UniqueConstraint("shopify_order_id", "product_id", name="uq_order_product"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    shopify_order_id: Mapped[str] = mapped_column(String(64), index=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"))
    cj_order_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    customer_email: Mapped[str] = mapped_column(String(255))
    shipping_address: Mapped[str] = mapped_column(Text)
    quantity: Mapped[int] = mapped_column(Integer, default=1)
    total_price: Mapped[float] = mapped_column(Float)
    status: Mapped[str] = mapped_column(String(64), default="pending")
    tracking_number: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())

    product: Mapped["Product"] = relationship("Product", back_populates="orders")
