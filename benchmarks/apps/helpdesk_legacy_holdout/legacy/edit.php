<?php
require_once __DIR__ . '/db.php';
$id = (int)($_GET['id'] ?? $_POST['id'] ?? 1);
$error = '';
if ($_SERVER['REQUEST_METHOD'] === 'POST') {
  $status = mysqli_real_escape_string($conn, $_POST['status'] ?? 'open');
  $priority = mysqli_real_escape_string($conn, $_POST['priority'] ?? 'normal');
  // H05 dynamic UPDATE
  $sql = "UPDATE tickets SET status = '$status', priority = '$priority' WHERE id = $id";
  if (mysqli_query($conn, $sql)) { header('Location: view.php?id='.$id); exit; }
  $error = mysqli_error($conn);
}
// H06 dynamic SELECT used to populate form
$sql = "SELECT id, subject, priority, status FROM tickets WHERE id = $id";
$result = mysqli_query($conn, $sql);
$row = mysqli_fetch_assoc($result);
page_header('Edit Ticket');
if ($error) echo '<p class="error">'.h($error).'</p>';
if (!$row) echo '<p>Ticket not found.</p>';
else {
 echo '<form method="post"><input type="hidden" name="id" value="'.h($row['id']).'"><p>'.h($row['subject']).'</p><label>Status <select name="status">';
 foreach (['open','in_progress','closed'] as $option) echo '<option value="'.h($option).'" '.($row['status']===$option?'selected':'').'>'.h($option).'</option>';
 echo '</select></label><label>Priority <select name="priority">';
 foreach (['low','normal','high'] as $option) echo '<option value="'.h($option).'" '.($row['priority']===$option?'selected':'').'>'.h($option).'</option>';
 echo '</select></label><button>Save changes</button></form>';
}
page_footer();
