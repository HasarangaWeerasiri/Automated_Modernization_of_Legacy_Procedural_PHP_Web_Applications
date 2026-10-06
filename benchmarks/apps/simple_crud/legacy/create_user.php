<?php

$name = $_POST['name'];
$email = $_POST['email'];

$sql = "INSERT INTO users (name, email, status)
        VALUES ('$name', '$email', 'active')";

mysqli_query($conn, $sql);