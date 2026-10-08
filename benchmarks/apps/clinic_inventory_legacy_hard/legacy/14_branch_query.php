<?php
require_once __DIR__ . '/db.php';
// S14 multiple assignments across branches: conservative abstention
$activeOnly = isset($_GET['active']);
if ($activeOnly) {
    $sql = "SELECT id, full_name FROM doctors WHERE active = 1";
} else {
    $sql = "SELECT id, full_name FROM doctors";
}
$result = mysqli_query($conn, $sql);
