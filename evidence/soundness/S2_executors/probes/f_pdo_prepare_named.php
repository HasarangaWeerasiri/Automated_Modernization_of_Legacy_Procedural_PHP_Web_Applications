<?php
$stmt = $pdo->prepare("UPDATE users SET name = :name WHERE id = :id");
$stmt->execute(['name' => $_POST['name'], 'id' => $_POST['id']]);
