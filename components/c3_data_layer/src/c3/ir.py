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
    unique: bool = False
    on_update: Optional[str] = None


@dataclass
class ForeignKey:
    """Represents a foreign-key relationship."""

    column: str
    referenced_table: str
    referenced_column: str
    name: Optional[str] = None
    on_delete: Optional[str] = None
    on_update: Optional[str] = None


@dataclass
class Table:
    """Represents a database table."""

    name: str
    columns: list[Column] = field(default_factory=list)
    foreign_keys: list[ForeignKey] = field(default_factory=list)
    # {"name": str | None, "columns": [str, ...]}
    unique_constraints: list[dict] = field(default_factory=list)
    # {"name": str | None, "columns": [str, ...]}
    indexes: list[dict] = field(default_factory=list)
    # {"name": str | None, "expression": str}
    check_constraints: list[dict] = field(default_factory=list)
    # Features recognised but not migrated (listed in migration_notes.md).
    unsupported_features: list[str] = field(default_factory=list)


@dataclass
class DatabaseSchema:
    """Represents the complete recovered database schema."""

    tables: list[Table] = field(default_factory=list)
    # Views, triggers, routines, events found in the dump (not migrated).
    unsupported_objects: list[str] = field(default_factory=list)


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