"""Value extraction, checked on small pieces of HTML written for the purpose."""

from c4.extract import extract_tables


def records(html):
    (table,) = extract_tables(html)
    return list(table.records)


def test_header_text_labels_each_column():
    html = """
        <table>
          <thead><tr><th scope='col'>First Name</th><th scope='col'>Email</th></tr></thead>
          <tbody><tr><td>Ram</td><td>ram@example.test</td></tr></tbody>
        </table>"""

    assert records(html) == [{"First Name": "Ram", "Email": "ram@example.test"}]


def test_every_row_becomes_a_record_in_page_order():
    html = """
        <table><tr><th>Name</th><th>Fee</th></tr>
               <tr><td>Ganesh</td><td>550</td></tr>
               <tr><td>Ashok</td><td>500</td></tr></table>"""

    assert records(html) == [{"Name": "Ganesh", "Fee": "550"}, {"Name": "Ashok", "Fee": "500"}]


def test_markup_inside_a_cell_is_discarded():
    html = "<table><tr><th>Name</th></tr><tr><td><b>Ga</b>nesh <a href='x'>(edit)</a></td></tr></table>"

    assert records(html) == [{"Name": "Ganesh (edit)"}]


def test_layout_whitespace_collapses_to_single_spaces():
    html = "<table><tr><th>Message</th></tr><tr><td>\n   Hey&nbsp;&nbsp;Admin <br> please\tcall  </td></tr></table>"

    assert records(html) == [{"Message": "Hey Admin please call"}]


def test_values_stay_text_exactly_as_displayed():
    html = "<table><tr><th>Fee</th><th>Date</th></tr><tr><td>0550.00</td><td>2020-02-14</td></tr></table>"

    assert records(html) == [{"Fee": "0550.00", "Date": "2020-02-14"}]


def test_an_empty_cell_is_an_empty_value():
    html = "<table><tr><th>Name</th><th>Allergy</th></tr><tr><td>Ram</td><td></td></tr></table>"

    assert records(html) == [{"Name": "Ram", "Allergy": ""}]


def test_a_table_without_headers_is_labelled_by_position():
    html = "<table><tr><td>Ram</td><td>Kumar</td></tr></table>"

    assert records(html) == [{"column 1": "Ram", "column 2": "Kumar"}]


def test_a_header_row_of_plain_cells_inside_thead_is_still_a_header():
    html = "<table><thead><tr><td>Name</td></tr></thead><tbody><tr><td>Ram</td></tr></tbody></table>"

    assert records(html) == [{"Name": "Ram"}]


def test_repeated_header_text_gives_distinct_labels():
    html = "<table><tr><th>Name</th><th>Name</th><th>Name</th></tr><tr><td>a</td><td>b</td><td>c</td></tr></table>"

    assert records(html) == [{"Name": "a", "Name (2)": "b", "Name (3)": "c"}]


def test_a_blank_header_is_labelled_by_position():
    html = "<table><tr><th>Name</th><th></th></tr><tr><td>Ram</td><td>Cancel</td></tr></table>"

    assert records(html) == [{"Name": "Ram", "column 2": "Cancel"}]


def test_a_row_longer_than_the_header_keeps_its_extra_values():
    html = "<table><tr><th>Name</th></tr><tr><td>Ram</td><td>extra</td></tr></table>"

    assert records(html) == [{"Name": "Ram", "column 2": "extra"}]


def test_a_row_shorter_than_the_header_has_only_its_own_values():
    html = "<table><tr><th>Name</th><th>Fee</th></tr><tr><td>Ram</td></tr></table>"

    assert records(html) == [{"Name": "Ram"}]


def test_a_table_with_headers_and_no_rows_has_no_records():
    (table,) = extract_tables("<table><tr><th>Name</th><th>Fee</th></tr></table>")

    assert table.labels == ("Name", "Fee")
    assert table.records == ()


def test_each_table_of_a_page_is_returned_separately_in_order():
    html = """
        <table><tr><th>Doctor</th></tr><tr><td>Ganesh</td></tr></table>
        <p>between</p>
        <table><tr><th>Patient</th></tr><tr><td>Ram</td></tr><tr><td>Alia</td></tr></table>"""

    doctors, patients = extract_tables(html)

    assert list(doctors.records) == [{"Doctor": "Ganesh"}]
    assert list(patients.records) == [{"Patient": "Ram"}, {"Patient": "Alia"}]


def test_a_nested_table_is_its_own_table_not_extra_rows():
    html = """
        <table><tr><th>Name</th><th>Detail</th></tr>
               <tr><td>Ram</td><td><table><tr><td>inner</td></tr></table></td></tr></table>"""

    outer, inner = extract_tables(html)

    assert len(outer.records) == 1 and outer.records[0]["Name"] == "Ram"
    assert list(inner.records) == [{"column 1": "inner"}]


def test_unbalanced_legacy_markup_is_still_read():
    html = "<div><table><tr><th>Name<th>Fee<tr><td>Ram<td>550</table></center></div></div>"

    assert records(html) == [{"Name": "Ram", "Fee": "550"}]


def test_a_page_without_tables_yields_nothing():
    assert extract_tables("<html><body><script>alert('No entries found!')</script></body></html>") == []


def test_bytes_are_decoded_with_the_encoding_the_page_declares():
    page = "<html><head><meta charset='iso-8859-1'></head><table><tr><th>Name</th></tr><tr><td>Renée</td></tr></table>"

    assert records(page.encode("iso-8859-1")) == [{"Name": "Renée"}]
