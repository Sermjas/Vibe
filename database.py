"""Асинхронный слой доступа к данным (SQLAlchemy 2.0 + aiosqlite)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import BigInteger, DateTime, ForeignKey, Numeric, String, func, select
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    """Базовый класс SQLAlchemy моделей."""


class User(Base):
    """Пользователь Telegram."""

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    telegram_id: Mapped[int] = mapped_column(
        BigInteger, unique=True, index=True, nullable=False
    )
    username: Mapped[str | None] = mapped_column(String(255), nullable=True)
    reg_date: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    transactions: Mapped[list["Transaction"]] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
    )


class Transaction(Base):
    """Транзакция (расход), распознанная по чеку."""

    __tablename__ = "transactions"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    telegram_file_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    user: Mapped[User] = relationship(back_populates="transactions")


@dataclass(frozen=True)
class UserUpsertResult:
    """Результат поиска/создания пользователя."""

    user: User
    created: bool


class Database:
    """Сервис работы с БД."""

    def __init__(self, database_url: str) -> None:
        self._engine: AsyncEngine = create_async_engine(database_url, echo=False)
        self._session_factory: async_sessionmaker[AsyncSession] = async_sessionmaker(
            self._engine, expire_on_commit=False
        )

    async def init_models(self) -> None:
        """Создаёт таблицы, если их ещё нет."""
        async with self._engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    async def get_or_create_user(
        self, telegram_id: int, username: str | None
    ) -> UserUpsertResult:
        """Возвращает пользователя по telegram_id или создаёт нового."""
        async with self._session_factory() as session:
            stmt = select(User).where(User.telegram_id == telegram_id)
            existing = await session.scalar(stmt)
            if existing is not None:
                if existing.username != username:
                    existing.username = username
                    await session.commit()
                return UserUpsertResult(user=existing, created=False)

            user = User(telegram_id=telegram_id, username=username)
            session.add(user)
            await session.commit()
            await session.refresh(user)
            return UserUpsertResult(user=user, created=True)

    async def add_transaction(
        self,
        user_id: int,
        amount: Decimal,
        telegram_file_id: str | None,
    ) -> None:
        """Сохраняет транзакцию пользователя."""
        async with self._session_factory() as session:
            tx = Transaction(
                user_id=user_id,
                amount=amount,
                telegram_file_id=telegram_file_id,
            )
            session.add(tx)
            await session.commit()

    async def get_total_spent(self, user_id: int) -> Decimal:
        """Сумма трат пользователя за всё время."""
        async with self._session_factory() as session:
            stmt = select(func.coalesce(func.sum(Transaction.amount), 0)).where(
                Transaction.user_id == user_id
            )
            value = await session.scalar(stmt)
            return Decimal(str(value or 0))

    async def get_month_spent(self, user_id: int, month_start: datetime) -> Decimal:
        """Сумма трат пользователя с начала месяца."""
        async with self._session_factory() as session:
            stmt = select(func.coalesce(func.sum(Transaction.amount), 0)).where(
                Transaction.user_id == user_id,
                Transaction.created_at >= month_start,
            )
            value = await session.scalar(stmt)
            return Decimal(str(value or 0))

