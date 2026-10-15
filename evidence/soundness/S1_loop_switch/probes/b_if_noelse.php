<?php
$sql = "SELECT id FROM users";
if (isset($_GET['active'])) { $sql = "SELECT id FROM users WHERE status = 'active'"; }
$r = mysqli_query($conn, $sql);
