<?php
$id = $_GET['id'];
$r = mysql_query("SELECT id, email FROM users WHERE id = '$id'");
