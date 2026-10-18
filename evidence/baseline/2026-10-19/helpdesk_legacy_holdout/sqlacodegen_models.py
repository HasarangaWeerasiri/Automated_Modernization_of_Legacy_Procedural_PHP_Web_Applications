import datetime

from sqlalchemy import ForeignKeyConstraint, Index, Integer, String, TIMESTAMP, Text, text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

class Base(DeclarativeBase):
    pass


class Teams(Base):
    __tablename__ = 'teams'
    __table_args__ = (
        Index('team_name', 'team_name', unique=True),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    team_name: Mapped[str] = mapped_column(String(80), nullable=False)

    agents: Mapped[list['Agents']] = relationship('Agents', back_populates='team')


class Agents(Base):
    __tablename__ = 'agents'
    __table_args__ = (
        ForeignKeyConstraint(['team_id'], ['teams.id'], name='fk_agents_team'),
        Index('fk_agents_team', 'team_id')
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    team_id: Mapped[int] = mapped_column(Integer, nullable=False)
    full_name: Mapped[str] = mapped_column(String(120), nullable=False)

    team: Mapped['Teams'] = relationship('Teams', back_populates='agents')
    tickets: Mapped[list['Tickets']] = relationship('Tickets', back_populates='agent')


class Tickets(Base):
    __tablename__ = 'tickets'
    __table_args__ = (
        ForeignKeyConstraint(['agent_id'], ['agents.id'], name='fk_tickets_agent'),
        Index('fk_tickets_agent', 'agent_id')
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    agent_id: Mapped[int] = mapped_column(Integer, nullable=False)
    subject: Mapped[str] = mapped_column(String(160), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    priority: Mapped[str] = mapped_column(String(12), nullable=False, server_default=text("'normal'"))
    status: Mapped[str] = mapped_column(String(16), nullable=False, server_default=text("'open'"))
    created_at: Mapped[datetime.datetime] = mapped_column(TIMESTAMP, nullable=False, server_default=text('CURRENT_TIMESTAMP'))

    agent: Mapped['Agents'] = relationship('Agents', back_populates='tickets')
