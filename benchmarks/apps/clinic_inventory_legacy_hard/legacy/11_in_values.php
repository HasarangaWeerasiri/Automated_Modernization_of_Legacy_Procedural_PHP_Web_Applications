<?php
require_once __DIR__ . '/db.php';
// S11 dynamic IN list should not be naively bound as one SQL scalar
$ids = $_GET['ids'] ?? '1,2';
$result = mysqli_query($conn, "SELECT id, full_name FROM patients WHERE id IN ($ids)");
