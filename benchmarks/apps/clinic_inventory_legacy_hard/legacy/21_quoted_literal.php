<?php
require_once __DIR__ . '/db.php';
// S21 SQL quote escaping/static literal preservation
$result = mysqli_query($conn, "SELECT id, full_name FROM patients WHERE full_name = 'O''Brien'");
