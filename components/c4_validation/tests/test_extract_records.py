"""Records a page shows outside tables, found as the database rows it shows."""

from c4.extract import extract_records

GUESTBOOK = {"guestbook": [
    {"id": 1, "name": "adam", "comment": "Hi, I love your site!", "created_on": "2008-12-02 19:32:53"},
    {"id": 20, "name": "Erica Nichols", "comment": "Partner even floor should", "created_on": "2026-10-18 10:00:00"},
]}

PICTURES = {
    "pictures": [
        {"id": 10, "width": 128, "tag": "flowers", "filename": "flowers/flowers"},
        {"id": 15, "width": 128, "tag": "flowers", "filename": "flowers/flweofoee"},
        {"id": 7, "width": 128, "tag": "doggie", "filename": "doggie/Dog.jpg"},
    ],
    "users": [{"id": 10, "login": "calvinwatters", "firstname": "Calvin"}],
}


def picture(number, filename):
    return f'<li><a href="/pictures/view.php?picid={number}"><img src="/upload/{filename}.128_128.jpg"></a></li>'


def only_place(html, tables):
    (place,) = extract_records(html, tables)
    return list(place.records)


def test_entries_written_as_loose_paragraphs_are_records_without_the_words_around_them():
    html = """<body><div class="column"><h2>Guestbook</h2>
        <p class="comment">Partner even floor should</p><p> - by Erica Nichols </p>
        <p class="comment">Hi, I love your site!</p><p> - by adam </p></div></body>"""

    assert only_place(html, GUESTBOOK) == [
        {"p.comment": "Partner even floor should", "p": "Erica Nichols"},
        {"p.comment": "Hi, I love your site!", "p": "adam"},
    ]


def test_a_list_split_over_several_rows_is_one_list_read_from_its_addresses():
    html = (f'<body><div class="c"><ul class="pics">{picture(10, "flowers/flowers")}{picture(15, "flowers/flweofoee")}</ul></div>'
            f'<div class="c"><ul class="pics">{picture(7, "doggie/Dog.jpg")}</ul></div></body>')

    records = only_place(html, PICTURES)

    # The id from the link, the file name and width from the image; the tag "flowers"
    # is seen only inside the file name, so it is not one more value.
    assert records == [
        {"link": "10", "image": "128", "image (2)": "flowers/flowers"},
        {"link": "15", "image": "128", "image (2)": "flowers/flweofoee"},
        {"link": "7", "image": "128", "image (2)": "doggie/Dog.jpg"},
    ]


def test_menus_and_a_heading_that_shows_one_value_are_not_records():
    html = """<body><ul class="menu"><li><a href="/home.php">Home</a></li><li><a href="/guestbook.php">Guestbook</a></li></ul>
        <h2>These are calvinwatters's Pictures:</h2><p>Nothing here</p></body>"""

    assert extract_records(html, PICTURES) == []


def test_values_that_many_rows_share_do_not_make_a_record_on_their_own():
    people = {"people": [{"id": n, "gender": "Male", "age": 21} for n in range(1, 4)]}

    assert extract_records("<body><p>Male</p><p>21</p></body>", people) == []


def test_a_value_counts_only_as_a_whole_piece():
    html = "<body><div><p>Hi, I love your site!</p><p>by adamant</p></div></body>"

    assert extract_records(html, GUESTBOOK) == []


def test_the_value_of_a_form_field_is_not_data():
    html = '<body><div><p>Hi, I love your site!</p><input name="name" value="adam"></div></body>'

    assert extract_records(html, GUESTBOOK) == []


def test_a_row_shown_in_two_places_gives_two_places():
    html = """<body><div class="top"><b>Hi, I love your site!</b> <i>adam</i></div>
        <ul><li><span>Partner even floor should</span> <span>Erica Nichols</span></li></ul></body>"""

    assert len(extract_records(html, GUESTBOOK)) == 2


def test_rows_of_different_tables_shown_in_one_element_are_one_record():
    html = '<body><ul><li><a href="/view.php?picid=7"><img src="/upload/doggie/Dog.jpg.128_128.jpg"></a> by <b>calvinwatters</b> <i>Calvin</i></li></ul></body>'

    (record,) = only_place(html, PICTURES)

    assert set(record.values()) >= {"7", "doggie/Dog.jpg", "calvinwatters", "Calvin"}


def test_an_address_is_read_as_the_reader_would_follow_it():
    html = '<body><div><a href="/search.php?q=Hi%2C%20I%20love%20your%20site%21">more</a><p>adam</p></div></body>'

    (record,) = only_place(html, GUESTBOOK)

    assert record == {"link": "Hi, I love your site!", "p": "adam"}


def test_without_database_rows_nothing_is_a_record():
    assert extract_records("<body><p>Hi, I love your site!</p><p>adam</p></body>", {}) == []


def test_a_record_around_a_whole_list_does_not_swallow_the_list():
    # The owner's name and id around the list of their pictures: two places, not one record.
    html = (f'<body><div class="profile"><h2>calvinwatters</h2><a href="/user.php?name=Calvin">Calvin</a>'
            f'<ul>{picture(10, "flowers/flowers")}{picture(7, "doggie/Dog.jpg")}</ul></div></body>')

    places = extract_records(html, PICTURES)

    assert [len(place.records) for place in places] == [1, 2]


def test_a_value_seen_only_inside_a_longer_value_of_the_row_is_not_one_more_value():
    users = {"users": [{"id": 1, "login": "Sample User", "firstname": "Sample", "lastname": "User"}]}

    assert extract_records("<body><h2>These are Sample User's Pictures:</h2></body>", users) == []
    (record,) = only_place("<body><p>Sample User</p><p>Sample</p></body>", users)
    assert sorted(record.values()) == ["Sample", "Sample User"]
