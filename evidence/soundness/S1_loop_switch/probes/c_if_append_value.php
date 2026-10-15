<?php
$sql = "SELECT id FROM users WHERE 1=1";
if (!empty($_GET['name'])) { $sql .= " AND name = '" . $_GET['name'] . "'"; }
$r = mysqli_query($conn, $sql);
