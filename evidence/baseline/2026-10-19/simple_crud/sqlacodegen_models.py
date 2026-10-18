from typing import Optional
import datetime

from sqlalchemy import Date, ForeignKeyConstraint, Index, Integer, String
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

class Base(DeclarativeBase):
    pass


class Users(Base):
    __tablename__ = 'users'

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    email: Mapped[str] = mapped_column(String(150), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)

    appointments: Mapped[list['Appointments']] = relationship('Appointments', back_populates='user')


class Appointments(Base):
    __tablename__ = 'appointments'
    __table_args__ = (
        ForeignKeyConstraint(['user_id'], ['users.id'], name='appointments_ibfk_1'),
        Index('user_id', 'user_id')
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, nullable=False)
    appointment_date: Mapped[datetime.date] = mapped_column(Date, nullable=False)
    status: Mapped[Optional[str]] = mapped_column(String(20))

    user: Mapped['Users'] = relationship('Users', back_populates='appointments')
