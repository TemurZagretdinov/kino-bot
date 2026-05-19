from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, Integer, String, false, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    telegram_id: Mapped[int] = mapped_column(BigInteger, unique=True, index=True)
    username: Mapped[str | None] = mapped_column(String(255), nullable=True)
    full_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    joined_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )
    last_active: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )
    is_blocked: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())
    last_movie_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    last_movie_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    last_search_type: Mapped[str | None] = mapped_column(String(32), nullable=True)

    searches = relationship("Search", back_populates="user")
