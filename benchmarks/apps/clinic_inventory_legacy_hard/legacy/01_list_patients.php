<?php
require_once __DIR__ . '/db.php';
// S01 simple read, static shape
$result = mysqli_query($conn, "SELECT id, full_name, email FROM patients");
