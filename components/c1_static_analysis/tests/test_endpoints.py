"""SQL shape (src/c1/sqlshape.py) and endpoint recovery (src/c1/endpoints.py)."""

from __future__ import annotations

from c1.endpoints import endpoint_map, recover
from c1.sqlshape import json_type, read_schema_dump
from pipeline import Run

SCHEMA = read_schema_dump("""
CREATE TABLE `patreg` (`pid` int(11) NOT NULL, `fname` varchar(20) NOT NULL, `contact` varchar(10) NOT NULL,
  `password` varchar(30) NOT NULL) ENGINE=InnoDB;
CREATE TABLE `contact` (`name` varchar(30) NOT NULL, `email` text NOT NULL, `message` varchar(200) NOT NULL) ENGINE=InnoDB;
CREATE TABLE `appointmenttb` (`ID` int(11) NOT NULL, `pid` int(11) NOT NULL, `appdate` date NOT NULL,
  `userStatus` int(5) NOT NULL) ENGINE=InnoDB;
""")


def endpoints(files):
    r = Run(files)
    return recover(r.asts, r.cfgs, r.includes, r.dfgs, r.labels, SCHEMA,
                   {a.file_id: r.files[a.path].encode() for a in r.asts})


def ep(rec, eid):
    return next(e for e in rec["endpoints"] if e["id"] == eid)


def test_schema_dump_reader_and_types():
    assert list(SCHEMA["patreg"]) == ["pid", "fname", "contact", "password"]
    assert json_type(SCHEMA["patreg"]["pid"]["sqlType"]) == {"type": "integer"}
    assert json_type(SCHEMA["patreg"]["fname"]["sqlType"]) == {"type": "string", "maxLength": 20}
    assert json_type(SCHEMA["appointmenttb"]["appdate"]["sqlType"]) == {"type": "string", "format": "date"}


def test_insert_form_handler_becomes_post_with_column_mapping():
    # HMS contact.php: the C4 stand-in map lists exactly this mapping by hand
    src = """<?php
$con = mysqli_connect('h', 'u', 'p', 'db');
if (isset($_POST['btnSubmit'])) {
  $name = $_POST['txtName']; $email = $_POST['txtEmail']; $message = $_POST['txtMsg'];
  $query = "insert into contact(name,email,message) values('$name','$email','$message');";
  $result = mysqli_query($con, $query);
}
"""
    rec = endpoints({"contact.php": src})
    e = ep(rec, "POST /api/contact")
    [s] = e["sources"]
    assert s["legacy"]["method"] == "POST" and s["legacy"]["fixed"] == {"btnSubmit": "1"}
    got = {(p["name"], p["in"], p["legacy_name"], p["column"]) for p in s["parameters"]}
    assert got == {("name", "body", "txtName", "contact.name"), ("email", "body", "txtEmail", "contact.email"),
                   ("message", "body", "txtMsg", "contact.message")}
    assert e["auth"]["required"] is False and any(f["flag"] == "no-guard-in-legacy" for f in e["flags"])


def test_list_render_uses_only_columns_the_page_reads():
    # master reference step 6: the response holds the fields that reach output, not SELECT *
    src = """<?php
$result = mysqli_query($con, "select * from patreg");
while ($row = mysqli_fetch_array($result)) { echo $row['fname'] . $row['contact']; }
"""
    rec = endpoints({"list.php": src})
    e = ep(rec, "GET /api/patreg")
    assert e["response"]["kind"] == "list" and e["response"]["recordsAt"] == "patreg"
    assert e["response"]["fields"] == {"contact": {"type": "string", "maxLength": 10},
                                       "fname": {"type": "string", "maxLength": 20}}
    assert e["response"]["selectedStar"] is True


def test_search_form_becomes_get_with_query_parameter():
    src = """<?php
if (isset($_POST['patient_search_submit'])) {
  $contact = $_POST['patient_contact'];
  $res = mysqli_query($con, "select * from patreg where contact='$contact'");
  while ($row = mysqli_fetch_array($res)) { echo $row['fname']; }
}
"""
    e = ep(endpoints({"patientsearch.php": src}), "GET /api/patreg")
    [p] = e["params"]
    assert (p["name"], p["in"], p["legacy_name"], p["column"]) == ("contact", "query", "patient_contact", "patreg.contact")


def test_update_by_key_from_a_get_link_is_put_and_flagged_mutating_get():
    src = """<?php
if (isset($_GET['cancel'])) {
  $q = mysqli_query($con, "update appointmenttb set userStatus='0' where ID = '".$_GET['ID']."'");
}
"""
    e = ep(endpoints({"panel.php": src}), "PUT /api/appointmenttb/{ID}")
    assert [p["in"] for p in e["params"]] == ["path"]
    assert any(f["flag"] == "mutating-get" for f in e["flags"])


def test_session_filter_becomes_me_endpoint_with_auth():
    src = """<?php
session_start();
$pid = $_SESSION['pid'];
$res = mysqli_query($con, "select ID, appdate from appointmenttb where pid='$pid'");
while ($row = mysqli_fetch_array($res)) { echo $row['ID'] . $row['appdate']; }
"""
    e = ep(endpoints({"me.php": src}), "GET /api/me/appointmenttb")
    assert e["sessionBindings"] == [{"column": "appointmenttb.pid", "sessionKey": "pid", "role": "where"}]
    assert e["auth"]["required"] is True


def test_login_handler_becomes_auth_login():
    src = """<?php
session_start();
if (isset($_POST['patsub'])) {
  $email = $_POST['email']; $password = $_POST['password2'];
  $r = mysqli_query($con, "select * from patreg where contact='$email' and password='$password';");
  if (mysqli_num_rows($r) == 1) { $_SESSION['pid'] = 1; header('Location: admin-panel.php'); }
}
"""
    e = ep(endpoints({"func.php": src}), "POST /api/auth/login")
    assert e["kind"] == "login" and e["response"]["kind"] == "token"
    assert {p["legacy_name"] for p in e["params"]} == {"email", "password2"}


def test_query_inside_a_called_method_binds_call_site_arguments():
    # WackoPicko: Pictures::get_picture($_GET['picid'])
    files = {
        "lib.php": """<?php
class Pictures {
  static function get_picture($id) {
    $r = mysql_query(sprintf("SELECT * from pictures where id = '%d'", $id));
    return mysql_fetch_assoc($r);
  }
}
""",
        "view.php": "<?php\nrequire_once('lib.php');\n$pic = Pictures::get_picture($_GET['picid']);\necho $pic['title'];\n",
    }
    rec = endpoints(files)
    e = ep(rec, "GET /api/pictures/{id}")
    [p] = e["params"]
    assert (p["in"], p["legacy_name"], p["column"]) == ("path", "picid", "pictures.id")
    assert e["response"]["kind"] == "object" and "title" in e["response"]["fields"]


def test_sensitive_column_is_flagged():
    src = "<?php\n$r = mysqli_query($c, 'select * from patreg');\nwhile ($row = mysqli_fetch_array($r)) { echo $row['password']; }\n"
    e = ep(endpoints({"p.php": src}), "GET /api/patreg")
    assert any(f["flag"] == "sensitive-field" for f in e["flags"])


def test_dynamic_table_is_flagged_not_guessed():
    src = "<?php\n$t = $_GET['t'];\n$r = mysqli_query($c, \"select * from $t\");\nwhile ($row = mysqli_fetch_array($r)) { echo $row['a']; }\n"
    rec = endpoints({"p.php": src})
    flags = {f["flag"] for e in rec["endpoints"] for f in e["flags"]}
    assert "dynamic-sql-structure" in flags


def test_endpoint_map_has_the_c4_structure():
    src = "<?php\nif (isset($_POST['btnSubmit'])) {\n  $n = $_POST['txtName'];\n  mysqli_query($c, \"insert into contact(name) values('$n')\");\n}\n"
    em = endpoint_map(endpoints({"contact.php": src}))
    assert em["format"] == "c1-endpoint-map/1"
    [e] = em["endpoints"]
    assert set(e) >= {"endpoint_id", "method", "path", "records_at", "sources"}
    [s] = e["sources"]
    assert set(s) == {"source_id", "legacy", "parameters"}
    assert set(s["legacy"]) == {"method", "path", "fixed", "records"}
    assert s["parameters"] == [{"name": "name", "in": "body", "legacy_name": "txtName", "legacy_in": "body",
                                "column": "contact.name"}]
    assert em["pages"] == {"contact.php": ["POST /api/contact"]}


def test_endpoints_are_deterministic():
    src = "<?php\n$r = mysqli_query($c, 'select fname from patreg');\nwhile ($row = mysqli_fetch_array($r)) { echo $row['fname']; }\n"
    assert endpoints({"p.php": src}) == endpoints({"p.php": src})
