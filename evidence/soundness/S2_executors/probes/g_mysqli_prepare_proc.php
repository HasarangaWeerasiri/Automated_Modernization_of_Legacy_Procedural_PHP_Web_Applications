<?php
$stmt = mysqli_prepare($conn, "SELECT id, name FROM users WHERE id = ?");
mysqli_stmt_bind_param($stmt, 'i', $id);
mysqli_stmt_execute($stmt);
