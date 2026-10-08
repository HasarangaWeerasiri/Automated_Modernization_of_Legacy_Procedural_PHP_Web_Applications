<?php
require_once __DIR__ . '/db.php';
// S02 interpolated value (legacy unsafe, migration should bind)
$id = $_GET['id'] ?? '1';
$result = mysqli_query($conn, "SELECT id, full_name, email FROM patients WHERE id = '$id'");
