"""Authorization gates and page protection (src/c1/auth.py)."""

from __future__ import annotations

from pipeline import Run

GUARDED = """<?php
session_start();
if (!isset($_SESSION['uid'])) {
    header('Location: login.php');
    exit();
}
$r = mysqli_query($con, 'SELECT * FROM orders');
echo 'ok';
"""


def test_terminating_guard_protects_the_page():
    r = Run(GUARDED)
    [c] = r.auth["checks"]
    assert c["kind"] == "authentication" and c["effect"] == "terminating-guard"
    assert c["serverSide"] is True and c["failSide"] == "true" and c["sessionKeys"] == ["uid"]
    p = r.page("p.php")
    assert p["status"] == "guarded" and p["protected"] == 1 and "needsDecision" not in p


def test_redirect_without_exit_is_a_broken_guard_and_ear():
    # Doupe et al. 2011 (EAR): header('Location') alone does not stop the script
    r = Run(GUARDED.replace("    exit();\n", ""))
    [c] = r.auth["checks"]
    assert c["effect"] == "broken-guard" and "EAR" in c["reason"]
    assert r.page("p.php")["status"] == "unguarded"
    [ear] = r.auth["redirectWithoutExit"]
    assert ear["dataAccessAfter"] == 1


def test_role_check_that_gates_a_query_is_authorization_server_side():
    src = "<?php\nif ($_SESSION['role'] == 'admin') {\n  $s = mysqli_query($c, 'SELECT COUNT(*) FROM p');\n}\n"
    [c] = Run(src).auth["checks"]
    assert c["kind"] == "authorization:role" and c["effect"] == "gates-data" and c["serverSide"] is True


def test_role_check_that_only_outputs_is_display_only():
    src = "<?php\nif ($_SESSION['role'] == 'admin') {\n  echo \"<a href='edit.php'>Edit</a>\";\n}\n"
    [c] = Run(src).auth["checks"]
    assert c["effect"] == "display-only" and c["serverSide"] is False


def test_ownership_check():
    src = "<?php\n$row = mysqli_fetch_assoc($res);\nif ($row['owner_id'] != $_SESSION['uid']) { die('no'); }\necho $row['body'];\n"
    [c] = Run(src).auth["checks"]
    assert c["kind"] == "authorization:ownership" and c["effect"] == "terminating-guard"


def test_presence_test_is_authentication():
    # HMS include/checklogin.php: if(strlen($_SESSION['login'])==0) { header(...); }
    src = "<?php\nif (strlen($_SESSION['login']) == 0) { header('location:index.php'); exit; }\n"
    [c] = Run(src).auth["checks"]
    assert c["kind"] == "authentication"


def test_or_die_guard():
    src = "<?php\nisset($_SESSION['uid']) or die('login first');\n$r = mysqli_query($c, 'SELECT 1');\n"
    r = Run(src)
    [c] = r.auth["checks"]
    assert c["effect"] == "terminating-guard"
    assert r.page("p.php")["status"] == "guarded"


def test_guard_helper_through_control_dependence():
    # WackoPicko: require_login() -> !Users::is_logged_in() -> current_user() reads the session,
    # and is_logged_in returns literals (the session decides which: control dependence)
    files = {
        "lib.php": """<?php
class Users {
  static function current_user() { if (isset($_SESSION['userid'])) { return Users::get(1); } return False; }
  static function get($id) { return mysqli_fetch_assoc(mysqli_query($GLOBALS['c'], "SELECT * FROM u WHERE id=$id")); }
  static function is_logged_in() { if (Users::current_user()) { return True; } else { return False; } }
}
function http_redirect($u) { header("Location: $u"); exit(0); }
function require_login() { if (!Users::is_logged_in()) { http_redirect('/login.php'); } }
""",
        "home.php": "<?php\nrequire_once('lib.php');\nrequire_login();\n$r = mysqli_query($c, 'SELECT * FROM pics');\n",
        "public.php": "<?php\nrequire_once('lib.php');\n$r = mysqli_query($c, 'SELECT * FROM pics');\n",
    }
    r = Run(files)
    assert [g["name"] for g in r.auth["guardHelpers"]] == ["require_login"]
    home = r.page("home.php")
    assert home["status"] == "guarded" and home["guards"][0]["via"] == "helper"
    pub = r.page("public.php")
    assert pub["status"] == "unguarded" and pub["needsDecision"] is True


def test_guard_file_included_at_the_top():
    files = {"auth.php": "<?php\nsession_start();\nif (empty($_SESSION['uid'])) { header('Location: /'); exit; }\n",
             "page.php": "<?php\ninclude 'auth.php';\n$r = mysqli_query($c, 'SELECT 1');\n"}
    r = Run(files)
    p = r.page("page.php")
    assert p["status"] == "guarded" and p["guards"][0]["via"] == "include"


def test_cookie_session_counts_as_identity():
    # WackoPicko admin area keeps its own session id in a cookie
    src = "<?php\nif (!isset($_COOKIE['session'])) { exit; }\n$r = mysqli_query($c, 'SELECT 1');\n"
    [c] = Run(src).auth["checks"]
    assert c["sessionKeys"] == ["cookie:session"] and c["effect"] == "terminating-guard"


def test_unguarded_writes_are_counted_and_need_a_decision():
    src = "<?php\n$q = \"delete from doctb where email='\" . $_POST['e'] . \"'\";\n$r = mysqli_query($con, $q);\n"
    p = Run(src).page("p.php")
    assert p["status"] == "unguarded" and p["needsDecision"] and p["unguardedWrites"] == 1


def test_data_access_before_the_guard_is_not_protected():
    src = "<?php\n$a = mysqli_query($c, 'SELECT 1');\nif (!isset($_SESSION['u'])) { exit; }\n$b = mysqli_query($c, 'SELECT 2');\n"
    p = Run(src).page("p.php")
    assert p["status"] == "partially-guarded" and p["protected"] == 1 and len(p["unprotected"]) == 1


def test_auth_output_is_deterministic():
    assert Run(GUARDED).auth == Run(GUARDED).auth


def test_possible_orm_access_counts_as_data_access_for_protection():
    # conservative: a page that may read data through a query builder needs a guard decision
    p = Run("<?php\n$db = getDbInstance();\n$rows = $db->get('customers');\n").page("p.php")
    assert p["status"] == "unguarded" and p["needsDecision"] is True
