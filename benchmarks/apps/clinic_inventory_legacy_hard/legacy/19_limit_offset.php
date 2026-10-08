<?php
require_once __DIR__ . '/db.php';
// S19 LIMIT OFFSET placeholders, parser compatibility check
$limit = $_GET['limit'] ?? '10';
$offset = $_GET['offset'] ?? '0';
$result = mysqli_query($conn, "SELECT id, full_name FROM patients LIMIT $limit OFFSET $offset");
