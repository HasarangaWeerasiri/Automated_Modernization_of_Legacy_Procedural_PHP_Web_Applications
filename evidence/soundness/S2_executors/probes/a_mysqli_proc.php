<?php
$id = $_GET['id'];
$r = mysqli_query($conn, "SELECT id, name FROM users WHERE id = $id");
