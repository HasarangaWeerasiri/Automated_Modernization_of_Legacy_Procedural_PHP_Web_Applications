from typing import Optional
import datetime
import decimal

from sqlalchemy import DECIMAL, DateTime, ForeignKeyConstraint, Index, Integer, String, text
from sqlalchemy.dialects.mysql import TINYINT
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

class Base(DeclarativeBase):
    pass


class Departments(Base):
    __tablename__ = 'departments'
    __table_args__ = (
        Index('uq_department_code', 'code', unique=True),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    code: Mapped[str] = mapped_column(String(20), nullable=False)

    doctors: Mapped[list['Doctors']] = relationship('Doctors', back_populates='department')
    patients: Mapped[list['Patients']] = relationship('Patients', back_populates='department')


class Doctors(Base):
    __tablename__ = 'doctors'
    __table_args__ = (
        ForeignKeyConstraint(['department_id'], ['departments.id'], name='fk_doctors_department'),
        Index('fk_doctors_department', 'department_id')
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    department_id: Mapped[int] = mapped_column(Integer, nullable=False)
    full_name: Mapped[str] = mapped_column(String(120), nullable=False)
    hourly_rate: Mapped[decimal.Decimal] = mapped_column(DECIMAL(10, 2), nullable=False)
    active: Mapped[int] = mapped_column(TINYINT(1), nullable=False, server_default=text("'1'"))

    department: Mapped['Departments'] = relationship('Departments', back_populates='doctors')
    appointments: Mapped[list['Appointments']] = relationship('Appointments', back_populates='doctor')


class Patients(Base):
    __tablename__ = 'patients'
    __table_args__ = (
        ForeignKeyConstraint(['department_id'], ['departments.id'], name='fk_patients_department'),
        Index('idx_patients_department', 'department_id')
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    department_id: Mapped[int] = mapped_column(Integer, nullable=False)
    full_name: Mapped[str] = mapped_column(String(120), nullable=False)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime, nullable=False)
    email: Mapped[Optional[str]] = mapped_column(String(160))
    phone: Mapped[Optional[str]] = mapped_column(String(25))

    department: Mapped['Departments'] = relationship('Departments', back_populates='patients')
    appointments: Mapped[list['Appointments']] = relationship('Appointments', back_populates='patient')


class Appointments(Base):
    __tablename__ = 'appointments'
    __table_args__ = (
        ForeignKeyConstraint(['doctor_id'], ['doctors.id'], name='fk_appointments_doctor'),
        ForeignKeyConstraint(['patient_id'], ['patients.id'], name='fk_appointments_patient'),
        Index('fk_appointments_doctor', 'doctor_id'),
        Index('idx_appointments_patient', 'patient_id')
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    patient_id: Mapped[int] = mapped_column(Integer, nullable=False)
    doctor_id: Mapped[int] = mapped_column(Integer, nullable=False)
    booked_at: Mapped[datetime.datetime] = mapped_column(DateTime, nullable=False)
    status: Mapped[str] = mapped_column(String(30), nullable=False, server_default=text("'pending'"))
    fee: Mapped[decimal.Decimal] = mapped_column(DECIMAL(10, 2), nullable=False)

    doctor: Mapped['Doctors'] = relationship('Doctors', back_populates='appointments')
    patient: Mapped['Patients'] = relationship('Patients', back_populates='appointments')
    payments: Mapped[list['Payments']] = relationship('Payments', back_populates='appointment')


class Payments(Base):
    __tablename__ = 'payments'
    __table_args__ = (
        ForeignKeyConstraint(['appointment_id'], ['appointments.id'], name='fk_payments_appointment'),
        Index('fk_payments_appointment', 'appointment_id')
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    appointment_id: Mapped[int] = mapped_column(Integer, nullable=False)
    amount: Mapped[decimal.Decimal] = mapped_column(DECIMAL(10, 2), nullable=False)
    method: Mapped[str] = mapped_column(String(30), nullable=False)
    paid_at: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime)

    appointment: Mapped['Appointments'] = relationship('Appointments', back_populates='payments')
