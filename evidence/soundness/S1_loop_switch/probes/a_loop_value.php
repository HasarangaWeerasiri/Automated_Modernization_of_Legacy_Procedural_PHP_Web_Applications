<?php
$sql = "SELECT id FROM users WHERE 1=0";
foreach ($_GET['names'] as $n) { $sql .= " OR name = '$n'"; }
$r = mysqli_query($conn, $sql);
