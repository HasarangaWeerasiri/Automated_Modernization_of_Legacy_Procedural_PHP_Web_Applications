import pytest

from c3.conversion.type_map import mysql_to_sqlalchemy


def test_integer_mapping():
    assert mysql_to_sqlalchemy("INT") == "Integer"


def test_bigint_mapping():
    assert mysql_to_sqlalchemy("BIGINT") == "BigInteger"


def test_varchar_preserves_length():
    assert mysql_to_sqlalchemy("VARCHAR(100)") == "String(100)"


def test_char_preserves_length():
    assert mysql_to_sqlalchemy("CHAR(10)") == "String(10)"


def test_decimal_preserves_precision_and_scale():
    assert mysql_to_sqlalchemy("DECIMAL(10,2)") == "Numeric(10, 2)"


def test_date_mapping():
    assert mysql_to_sqlalchemy("DATE") == "Date"


def test_datetime_mapping():
    assert mysql_to_sqlalchemy("DATETIME") == "DateTime"


def test_tinyint_one_maps_to_boolean():
    assert mysql_to_sqlalchemy("TINYINT(1)") == "Boolean"


def test_unsigned_integer_mapping():
    assert mysql_to_sqlalchemy("INT UNSIGNED") == "Integer"


def test_unsupported_type_is_not_guessed():
    with pytest.raises(
        ValueError,
        match="Unsupported MySQL type",
    ):
        mysql_to_sqlalchemy("GEOGRAPHY")