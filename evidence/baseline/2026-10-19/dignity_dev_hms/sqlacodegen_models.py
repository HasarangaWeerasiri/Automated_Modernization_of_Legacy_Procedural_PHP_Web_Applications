from typing import Optional

from sqlalchemy import Column, Index, Integer, String, Table, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

class Base(DeclarativeBase):
    pass


t_admin = Table(
    'admin', Base.metadata,
    Column('email', String(30), nullable=False),
    Column('password', String(30), nullable=False)
)


class Appointments(Base):
    __tablename__ = 'appointments'

    appointment_no: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    patient_id: Mapped[int] = mapped_column(Integer, nullable=False)
    speciality: Mapped[str] = mapped_column(String(30), nullable=False)
    medical_condition: Mapped[Optional[str]] = mapped_column(Text)
    doctors_suggestion: Mapped[Optional[str]] = mapped_column(String(30))
    payment_amount: Mapped[Optional[int]] = mapped_column(Integer)
    case_closed: Mapped[Optional[str]] = mapped_column(String(10))


t_clerks = Table(
    'clerks', Base.metadata,
    Column('email', String(30), nullable=False),
    Column('password', String(30), nullable=False),
    Column('fullname', String(30), nullable=False),
    Index('email', 'email', unique=True),
    comment='stores information about clerk'
)


t_doctors = Table(
    'doctors', Base.metadata,
    Column('email', String(30), nullable=False),
    Column('password', String(30), nullable=False),
    Column('fullname', String(30), nullable=False),
    Column('speciality', String(30), nullable=False),
    Index('email', 'email', unique=True)
)


class PatientInfo(Base):
    __tablename__ = 'patient_info'
    __table_args__ = {'comment': 'patient'}

    patient_Id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    full_name: Mapped[str] = mapped_column(String(20), nullable=False)
    DOB: Mapped[int] = mapped_column(Integer, nullable=False)
    weight: Mapped[int] = mapped_column(Integer, nullable=False)
    phone_no: Mapped[str] = mapped_column(String(30), nullable=False)
    address: Mapped[str] = mapped_column(String(260), nullable=False)


t_users = Table(
    'users', Base.metadata,
    Column('email', String(30), nullable=False),
    Column('password', String(30), nullable=False),
    Column('fullname', String(30), nullable=False),
    Index('username', 'email', unique=True)
)
