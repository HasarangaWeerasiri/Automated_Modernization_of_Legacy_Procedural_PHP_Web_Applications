<?php

$q = $_GET['q'];

$sql = "SELECT id, name, email
        FROM users
        WHERE name LIKE '%" . $q . "%'";

$result = mysqli_query($conn, $sql);