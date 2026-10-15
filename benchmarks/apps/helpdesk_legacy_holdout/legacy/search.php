<?php
require_once __DIR__ . '/db.php';
$term = mysqli_real_escape_string($conn, $_GET['term'] ?? '');
// H08 LIKE with wildcard concatenation
$sql = "SELECT id, subject, status FROM tickets WHERE subject LIKE '%$term%' ORDER BY id DESC";
$result = mysqli_query($conn, $sql);
page_header('Search Tickets');
echo '<form method="get"><label>Search subject<input name="term" value="'.h($_GET['term'] ?? '').'"></label><button>Search</button></form>';
echo '<table><tr><th>ID</th><th>Subject</th><th>Status</th></tr>';
while ($row = mysqli_fetch_assoc($result)) echo '<tr><td>'.h($row['id']).'</td><td><a href="view.php?id='.urlencode($row['id']).'">'.h($row['subject']).'</a></td><td>'.h($row['status']).'</td></tr>';
echo '</table>'; page_footer();
