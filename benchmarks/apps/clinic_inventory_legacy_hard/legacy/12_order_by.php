<?php
require_once __DIR__ . '/db.php';
// S12 structural ORDER BY identifier, cannot bind as a value
$sort = $_GET['sort'] ?? 'full_name';
$result = mysqli_query($conn, "SELECT id, full_name FROM patients ORDER BY $sort");
