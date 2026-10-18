import datetime

from sqlalchemy import Date, Index, Integer, String, TIMESTAMP, Text, text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

class Base(DeclarativeBase):
    pass


class Posts(Base):
    __tablename__ = 'posts'

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    title: Mapped[str] = mapped_column(String(225), nullable=False)
    author: Mapped[str] = mapped_column(String(225), nullable=False)
    postdate: Mapped[datetime.date] = mapped_column(Date, nullable=False)
    image: Mapped[str] = mapped_column(Text, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    updated_on: Mapped[datetime.datetime] = mapped_column(TIMESTAMP, nullable=False, server_default=text('CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP'))
    status: Mapped[str] = mapped_column(String(225), nullable=False)
    tag: Mapped[str] = mapped_column(String(300), nullable=False)


class Users(Base):
    __tablename__ = 'users'
    __table_args__ = (
        Index('username', 'username', unique=True),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(String(225), nullable=False)
    firstname: Mapped[str] = mapped_column(String(225), nullable=False)
    lastname: Mapped[str] = mapped_column(String(225), nullable=False)
    email: Mapped[str] = mapped_column(String(225), nullable=False)
    password: Mapped[str] = mapped_column(String(225), nullable=False)
    role: Mapped[str] = mapped_column(String(225), nullable=False, server_default=text("'user'"))
