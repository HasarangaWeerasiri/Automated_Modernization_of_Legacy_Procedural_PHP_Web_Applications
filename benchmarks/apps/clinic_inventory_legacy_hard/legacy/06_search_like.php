<?php
require_once __DIR__ . '/db.php';
// S06 LIKE wildcard value
$q = $_GET['q'] ?? 'Ali';
$result = mysqli_query($conn, "SELECT id, full_name FROM patients WHERE full_name LIKE '%$q%'");
