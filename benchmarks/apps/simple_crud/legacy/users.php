<?php

$sql = "SELECT id, name, email, status FROM users";
$result = mysqli_query($conn, $sql);