"""Value extraction, checked on small pieces of HTML written for the purpose."""

from c4.extract import extract_messages, extract_tables


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


def test_code_and_styling_inside_a_cell_are_not_part_of_its_value():
    html = """<table><tr><th>Name</th></tr>
              <tr><td>Ram<script>alert("hi")</script><style>.x{color:red}</style></td></tr></table>"""

    assert records(html) == [{"Name": "Ram"}]


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


# ----------------------------------------------------------------------- messages


def test_a_pop_up_message_is_read_from_the_script_that_shows_it():
    page = """<script type="text/javascript">alert("Message sent successfully!");window.location.href = "contact.html";</script>"""

    assert extract_messages(page) == ["Message sent successfully!"]


def test_single_quotes_and_line_breaks_inside_the_call_are_read():
    page = "<script> alert('No entries found! Please enter valid details'); \n window.location.href = 'x.php';</script>"

    assert extract_messages(page) == ["No entries found! Please enter valid details"]


def test_every_message_of_a_page_is_returned_in_order():
    page = "<script>alert('first');</script><p>text</p><script>alert(\"second\"); alert('third');</script>"

    assert extract_messages(page) == ["first", "second", "third"]


def test_a_quote_of_the_other_kind_or_an_escaped_one_stays_in_the_message():
    page = r"""<script>alert("Patient's record saved"); alert('It\'s done');</script>"""

    assert extract_messages(page) == ["Patient's record saved", "It's done"]


def test_text_that_only_mentions_alert_outside_a_script_is_not_a_message():
    assert extract_messages("<p>Call alert('x') to show a box.</p>") == []


def test_a_message_built_at_run_time_is_not_read():
    assert extract_messages("<script>alert(message); alert('Saved ' + name);</script>") == []


def test_a_page_without_pop_ups_has_no_messages():
    assert extract_messages("<table><tr><td>Ram</td></tr></table>") == []
    assert extract_messages("") == []


# ------------------------------------------------------ tables that line up a form

LOGIN_FORM = """
    <table style="width:320px"><form action="/login.php" method="POST">
      <tr><td>Username :</td><td> <input type="text" name="username" /></td></tr>
      <tr><td>Password :</td><td> <input type="password" name="password" /></td></tr>
      <tr><td><input type="submit" value="login" /></td><td><a href="/register.php">Register</a></td></tr>
    </form></table>"""


def test_a_table_that_lines_up_a_form_is_marked_and_gives_no_records():
    (table,) = extract_tables(LOGIN_FORM)

    assert table.form_layout and table.records == ()


def test_an_input_without_a_type_and_a_text_area_are_typing_fields():
    html = "<table><tr><td>Name</td><td><input name='n'></td></tr><tr><td>Note</td><td><textarea></textarea></td></tr></table>"

    assert extract_tables(html)[0].form_layout


def test_choices_checkboxes_buttons_and_hidden_fields_do_not_make_a_form_layout():
    html = """<table><tr><td>Ram</td><td><select><option>A</option></select></td></tr>
              <tr><td>Alia</td><td><input type="checkbox"><input type="radio"><input type="hidden" value="2"></td></tr>
              <tr><td>Anu</td><td><input type="submit" value="Go"><button>Cancel</button></td></tr></table>"""

    (table,) = extract_tables(html)

    assert not table.form_layout
    assert [record["column 1"] for record in table.records] == ["Ram", "Alia", "Anu"]


def test_a_table_with_a_header_row_is_data_even_with_a_text_box_in_every_row():
    html = "<table><tr><th>Doctor</th><th>Note</th></tr><tr><td>Dinesh</td><td><input type='text'></td></tr></table>"

    (table,) = extract_tables(html)

    assert not table.form_layout and table.records == ({"Doctor": "Dinesh", "Note": ""},)


def test_one_typing_row_among_rows_of_data_does_not_make_a_form_layout():
    html = """<table><tr><td>Ram</td><td>550</td></tr><tr><td>Alia</td><td>700</td></tr>
              <tr><td>Anu</td><td>300</td></tr><tr><td>Search</td><td><input type="search"></td></tr></table>"""

    assert not extract_tables(html)[0].form_layout


def test_a_field_in_a_nested_table_belongs_to_the_nested_table():
    html = f"<table><tr><td>Ram</td></tr><tr><td>Alia</td></tr><tr><td>{LOGIN_FORM}</td></tr></table>"

    outer, inner = extract_tables(html)

    assert not outer.form_layout and inner.form_layout
