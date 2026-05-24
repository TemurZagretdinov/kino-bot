from datetime import datetime

from sqlalchemy import DateTime, Index, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class Movie(Base):
    __tablename__ = "movies"

    __table_args__ = (Index("ix_movies_code", "code"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(64))
    title: Mapped[str] = mapped_column(String(255))
    content_type: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default="movie",
        server_default="movie",
    )
    episode: Mapped[int | None] = mapped_column(Integer, nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    movie_link: Mapped[str | None] = mapped_column(Text, nullable=True)
    archive_chat_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    archive_message_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    views_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )

    searches = relationship("Search", back_populates="movie")
