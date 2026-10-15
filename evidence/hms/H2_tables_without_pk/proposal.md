# H2: HMS tables without a primary key (proposal, 2026-10-15)

**No key is invented in the generated code.** The schema dump `myhmsdb.sql` defines a PK only for `appointmenttb(ID)` and `patreg(pid)`. The 4 tables below have none, so C3 emits them as SQLAlchemy Core `Table` objects (`admintb_table`, `contact_table`, `doctb_table`, `prestb_table`) and lists them in `migration_notes.md`.

## Facts

The seed data comes from the dump's INSERT rows; "distinct / rows" counts the distinct values of each column.

| Table | Columns | Seed rows | Columns unique in the seed | Used by (HMS functions) | How the app identifies a row |
|---|---|---|---|---|---|
| `admintb` | username, password | 1 | both (trivial) | Q032 SELECT (login) | `username` + `password` filter |
| `contact` | name, email, contact, message | 9 | all 4 (no business meaning) | Q015, Q037 SELECT; Q017 INSERT | none (append-only messages) |
| `doctb` | username, password, email, spec, docFees | 8 | username, password, email, docFees | 10 SELECT, 6 INSERT, Q010 **DELETE by `email`** | `username` (login) and `email` (delete, search) |
| `prestb` | doctor, pid, ID, fname, lname, appdate, apptime, disease, allergy, prescription | 4 | doctor, ID, apptime, prescription | Q004, Q008, Q013, Q020 SELECT; Q043 INSERT | `ID` (the appointment ID, joined to `appointmenttb.ID`) |

Uniqueness in 1–9 seed rows does not show that a column is a key. Production data may contain duplicates: nothing in the legacy app prevents a second doctor with the same email.

## Options

| Option | What changes | Effect on the 44 generated functions | Risk |
|---|---|---|---|
| **A. Keep as Core `Table` (current, recommended for the 19 Oct handoff)** | nothing | **None.** Every HMS function is `text()` SQL with bind parameters, and none uses ORM identity. Signatures, SQL and commit behaviour are unchanged. `UPDATE`/`DELETE` affect every matching row, exactly as in PHP (Q010 deletes every doctor with that email). | No ORM class or relationship for these 4 tables. Teammates use `doctb_table` etc. with Core `select()`/`insert()` if they need them. |
| **B. Mapper-only identity** (`__mapper_args__ = {"primary_key": [...]}`), no DDL change: `admintb.username`, `doctb.username` (or `email`), `prestb.ID`; `contact` stays Core | models.py only (ORM classes for 3 tables) | **None** (functions do not use the ORM). | Wrong if duplicates exist: the ORM identity map would merge two rows into one object. Needs the owner's confirmation per table, and a duplicate check on the real data (query below). |
| **C. Real constraint via a separate opt-in Alembic revision `0002_add_keys`** (PK or UNIQUE on the same columns; `contact` would need a surrogate `id`) | database schema | Function SQL unchanged. INSERTs that create duplicates would start to fail (a behaviour change from PHP), and E1 equivalence would differ on those cases. | Schema change to a legacy database; the owner decides. Must run the duplicate check first. |

Duplicate check before B or C (run on the real database, read-only):

```sql
SELECT username, COUNT(*) FROM doctb GROUP BY username HAVING COUNT(*) > 1;
SELECT email, COUNT(*) FROM doctb GROUP BY email HAVING COUNT(*) > 1;
SELECT ID, COUNT(*) FROM prestb GROUP BY ID HAVING COUNT(*) > 1;
SELECT username, COUNT(*) FROM admintb GROUP BY username HAVING COUNT(*) > 1;
```

**Recommendation:** use A for `hms-handoff-v1`. It keeps the legacy semantics, the function signatures stay stable, and nothing is invented. B or C only after the owner confirms the keys and the duplicate check returns no rows. That would be a models.py or migration change and would not touch any `data_access.py` signature.
