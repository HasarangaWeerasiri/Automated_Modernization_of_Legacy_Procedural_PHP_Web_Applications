<?php
require_once __DIR__ . '/db.php';
// S05 static DELETE with dynamic value
$id = $_POST['id'] ?? '2';
$result = mysqli_query($conn, "DELETE FROM payments WHERE id = $id");
