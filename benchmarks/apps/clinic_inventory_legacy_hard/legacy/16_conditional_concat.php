<?php
require_once __DIR__ . '/db.php';
// S16 SQL fragment built conditionally
$sql = "SELECT id, full_name FROM patients WHERE 1=1";
if (isset($_GET['department_id'])) {
    $sql .= " AND department_id = " . $_GET['department_id'];
}
$result = mysqli_query($conn, $sql);
