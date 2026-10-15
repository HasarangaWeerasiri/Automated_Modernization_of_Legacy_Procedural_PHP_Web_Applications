<?php
$id = $_GET['id'];
$r = $mysqli->query("SELECT id, name FROM users WHERE id = " . $id);
