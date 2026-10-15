<?php
require_once __DIR__ . '/db.php';
$error = '';
if ($_SERVER['REQUEST_METHOD'] === 'POST') {
  $subject = mysqli_real_escape_string($conn, $_POST['subject'] ?? '');
  $description = mysqli_real_escape_string($conn, $_POST['description'] ?? '');
  $agent_id = (int)($_POST['agent_id'] ?? 1);
  $priority = mysqli_real_escape_string($conn, $_POST['priority'] ?? 'normal');
  if ($subject === '' || $description === '') $error = 'Subject and description are required.';
  else {
    // H04 dynamic INSERT; demonstrates a procedural escaped-string pattern
    $sql = "INSERT INTO tickets (agent_id, subject, description, priority) VALUES ($agent_id, '$subject', '$description', '$priority')";
    if (mysqli_query($conn, $sql)) { header('Location: index.php'); exit; }
    $error = mysqli_error($conn);
  }
}
page_header('Create Ticket');
if ($error) echo '<p class="error">'.h($error).'</p>';
echo '<form method="post"><label>Subject <input required name="subject" maxlength="160"></label><label>Description <textarea required name="description"></textarea></label><label>Agent <select name="agent_id"><option value="1">Amara Silva</option><option value="2">Nimal Perera</option><option value="3">Kavindi Fernando</option><option value="4">Ruwan Jayasekara</option></select></label><label>Priority <select name="priority"><option>normal</option><option>low</option><option>high</option></select></label><button>Create</button></form>';
page_footer();
