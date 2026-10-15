<?php
mysqli_multi_query($conn, "SELECT id FROM users; SELECT id FROM teams");
