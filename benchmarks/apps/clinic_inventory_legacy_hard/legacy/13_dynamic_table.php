<?php
require_once __DIR__ . '/db.php';
// S13 dynamic table identifier: requires structural allowlist
$table = $_GET['table'] ?? 'patients';
$result = mysqli_query($conn, "SELECT id FROM $table WHERE id = 1");
