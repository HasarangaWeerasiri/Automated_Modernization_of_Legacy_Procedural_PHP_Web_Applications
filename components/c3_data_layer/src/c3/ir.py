"""
Intermediate Representation (IR) models
for Component 3 - Data Layer Migration.

The IR provides common data structures between:

Mapping -> Conversion -> Generation
"""

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class Column:
    """Represents a database column."""

    name: str
    data_type: str
    nullable: bool = True
    primary_key: bool = False
    auto_increment: bool = False
    default: Optional[str] = None


@dataclass
class ForeignKey:
    """Represents a foreign-key relationship."""

    column: str
    referenced_table: str
    referenced_column: str


@dataclass
class Table:
    """Represents a database table."""

    name: str
    columns: list[Column] = field(default_factory=list)
    foreign_keys: list[ForeignKey] = field(default_factory=list)


@dataclass
class DatabaseSchema:
    """Represents the complete recovered database schema."""

    tables: list[Table] = field(default_factory=list)


@dataclass
class QueryPart:
    """
    Represents one part of a recovered SQL query.

    kind:
        literal  - fixed SQL text
        variable - dynamic PHP value
    """

    kind: str
    value: str
    source: Optional[str] = None


@dataclass
class QueryTemplate:
    """Represents a reconstructed SQL query."""

    parts: list[QueryPart]
    source_file: str
    source_line: int


@dataclass
class RecoveredQuery:
    """Represents a SQL operation recovered from PHP."""

    query_id: str
    source_file: str
    source_line: int
    executor: str
    raw_sql: Optional[str] = None
    operation: Optional[str] = None
    status: str = "RECOVERED"


@dataclass
class Flag:
    """Represents a query that cannot be safely resolved."""

    source_file: str
    source_line: int
    reason: str
    expression: Optional[str] = None