<?php
require_once __DIR__ . '/db.php';
if ($_SERVER['REQUEST_METHOD'] !== 'POST') { http_response_code(405); exit('Use POST'); }
$id = (int)($_POST['id'] ?? 0);
if ($id < 1) { http_response_code(400); exit('Invalid ID'); }
// H07 dynamic DELETE
$sql = "DELETE FROM tickets WHERE id = $id";
mysqli_query($conn, $sql);
header('Location: index.php');
exit;
