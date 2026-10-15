## helpdesk

### Q002 `create.php:13`, mechanical containment check: OK

- call blocks: `['ELSE@474', 'IF@53']`
- recovered SQL: `INSERT INTO tickets (agent_id, subject, description, priority) VALUES ({{agent_id}}, '{{subject}}', '{{description}}', '{{priority}}')`
- chain L12 ASSIGN blocks `['ELSE@474', 'IF@53']`

```php
  12: $sql = "INSERT INTO tickets (agent_id, subject, description, priority) VALUES ($agent_id, '$subject', '$description', '$priority')";
  13: if (mysqli_query($conn, $sql)) { header('Location: index.php'); exit; }
```

### Q004 `edit.php:10`, mechanical containment check: OK

- call blocks: `['IF@100']`
- recovered SQL: `UPDATE tickets SET status = '{{status}}', priority = '{{priority}}' WHERE id = {{id}}`
- chain L9 ASSIGN blocks `['IF@100']`

```php
   9: $sql = "UPDATE tickets SET status = '$status', priority = '$priority' WHERE id = $id";
  10: if (mysqli_query($conn, $sql)) { header('Location: view.php?id='.$id); exit; }
```

Newly recovered in helpdesk: 2

## hms_kishan0725

### Q009 `admin-panel1.php:15`, mechanical containment check: OK

- call blocks: `['IF@109']`
- recovered SQL: `insert into doctb(username,password,email,spec,docFees)values('{{doctor}}','{{dpassword}}','{{demail}}','{{spec}}','{{docFees}}')`
- chain L14 ASSIGN blocks `['IF@109']`

```php
  14: $query="insert into doctb(username,password,email,spec,docFees)values('$doctor','$dpassword','$demail','$spec','$docFees')";
  15: $result=mysqli_query($con,$query);
```

### Q010 `admin-panel1.php:27`, mechanical containment check: OK

- call blocks: `['IF@562']`
- recovered SQL: `delete from doctb where email='{{demail}}';`
- chain L26 ASSIGN blocks `['IF@562']`

```php
  26: $query="delete from doctb where email='$demail';";
  27: $result=mysqli_query($con,$query);
```

### Q016 `appsearch.php:17`, mechanical containment check: OK

- call blocks: `['IF@443']`
- recovered SQL: `select * from appointmenttb where contact= '{{contact}}';`
- chain L16 ASSIGN blocks `['IF@443']`

```php
  16: $query = "select * from appointmenttb where contact= '$contact';";
  17: $result = mysqli_query($con,$query);
```

### Q017 `contact.php:11`, mechanical containment check: OK

- call blocks: `['IF@63']`
- recovered SQL: `insert into contact(name,email,contact,message) values('{{name}}','{{email}}','{{contact}}','{{message}}');`
- chain L10 ASSIGN blocks `['IF@63']`

```php
  10: $query="insert into contact(name,email,contact,message) values('$name','$email','$contact','$message');";
  11: $result = mysqli_query($con,$query);
```

### Q022 `doctorsearch.php:16`, mechanical containment check: OK

- call blocks: `['IF@440']`
- recovered SQL: `select * from doctb where email= '{{contact}}'`
- chain L15 ASSIGN blocks `['IF@440']`

```php
  15: $query = "select * from doctb where email= '$contact'";
  16: $result = mysqli_query($con,$query);
```

### Q023 `func.php:8`, mechanical containment check: OK

- call blocks: `['IF@80']`
- recovered SQL: `select * from patreg where email='{{email}}' and password='{{password}}';`
- chain L7 ASSIGN blocks `['IF@80']`

```php
   7: $query="select * from patreg where email='$email' and password='$password';";
   8: $result=mysqli_query($con,$query);
```

### Q024 `func.php:34`, mechanical containment check: OK

- call blocks: `['IF@945']`
- recovered SQL: `update appointmenttb set payment='{{status}}' where contact='{{contact}}';`
- chain L33 ASSIGN blocks `['IF@945']`

```php
  33: $query="update appointmenttb set payment='$status' where contact='$contact';";
  34: $result=mysqli_query($con,$query);
```

### Q025 `func.php:62`, mechanical containment check: OK

- call blocks: `['IF@1554']`
- recovered SQL: `insert into doctb(username,password,email,docFees)values('{{doctor}}','{{dpassword}}','{{demail}}','{{docFees}}')`
- chain L61 ASSIGN blocks `['IF@1554']`

```php
  61: $query="insert into doctb(username,password,email,docFees)values('$doctor','$dpassword','$demail','$docFees')";
  62: $result=mysqli_query($con,$query);
```

### Q026 `func1.php:8`, mechanical containment check: OK

- call blocks: `['IF@80']`
- recovered SQL: `select * from doctb where username='{{dname}}' and password='{{dpass}}';`
- chain L7 ASSIGN blocks `['IF@80']`

```php
   7: $query="select * from doctb where username='$dname' and password='$dpass';";
   8: $result=mysqli_query($con,$query);
```

### Q028 `func2.php:14`, mechanical containment check: OK

- call blocks: `['IF@320', 'IF@80']`
- recovered SQL: `insert into patreg(fname,lname,gender,email,contact,password,cpassword) values ('{{fname}}','{{lname}}','{{gender}}','{{email}}','{{contact}}','{{password}}','{{cpassword}}');`
- chain L13 ASSIGN blocks `['IF@320', 'IF@80']`

```php
  13: $query="insert into patreg(fname,lname,gender,email,contact,password,cpassword) values ('$fname','$lname','$gender','$email','$contact','$password','$
  14: $result=mysqli_query($con,$query);
```

### Q029 `func2.php:26`, mechanical containment check: OK

- call blocks: `['IF@320', 'IF@80']`
- recovered SQL: `select * from patreg;`
- chain L25 ASSIGN blocks `['IF@320', 'IF@80']`

```php
  25: $query1 = "select * from patreg;";
  26: $result1 = mysqli_query($con,$query1);
```

### Q030 `func2.php:41`, mechanical containment check: OK

- call blocks: `['IF@1150']`
- recovered SQL: `update appointmenttb set payment='{{status}}' where contact='{{contact}}';`
- chain L40 ASSIGN blocks `['IF@1150']`

```php
  40: $query="update appointmenttb set payment='$status' where contact='$contact';";
  41: $result=mysqli_query($con,$query);
```

### Q031 `func2.php:66`, mechanical containment check: OK

- call blocks: `['IF@1775']`
- recovered SQL: `insert into doctb(name)values('{{name}}')`
- chain L65 ASSIGN blocks `['IF@1775']`

```php
  65: $query="insert into doctb(name)values('$name')";
  66: $result=mysqli_query($con,$query);
```

### Q032 `func3.php:8`, mechanical containment check: OK

- call blocks: `['IF@80']`
- recovered SQL: `select * from admintb where username='{{username}}' and password='{{password}}';`
- chain L7 ASSIGN blocks `['IF@80']`

```php
   7: $query="select * from admintb where username='$username' and password='$password';";
   8: $result=mysqli_query($con,$query);
```

### Q033 `func3.php:24`, mechanical containment check: OK

- call blocks: `['IF@591']`
- recovered SQL: `update appointmenttb set payment='{{status}}' where contact='{{contact}}';`
- chain L23 ASSIGN blocks `['IF@591']`

```php
  23: $query="update appointmenttb set payment='$status' where contact='$contact';";
  24: $result=mysqli_query($con,$query);
```

### Q035 `func3.php:49`, mechanical containment check: OK

- call blocks: `['IF@1180']`
- recovered SQL: `insert into doctb(name)values('{{name}}')`
- chain L48 ASSIGN blocks `['IF@1180']`

```php
  48: $query="insert into doctb(name)values('$name')";
  49: $result=mysqli_query($con,$query);
```

### Q037 `messearch.php:15`, mechanical containment check: OK

- call blocks: `['IF@361']`
- recovered SQL: `select * from contact where contact= '{{contact}}'`
- chain L14 ASSIGN blocks `['IF@361']`

```php
  14: $query = "select * from contact where contact= '$contact'";
  15: $result = mysqli_query($con,$query);
```

### Q038 `newfunc.php:23`, mechanical containment check: OK

- call blocks: `['IF@524']`
- recovered SQL: `update appointmenttb set payment='{{status}}' where contact='{{contact}}';`
- chain L22 ASSIGN blocks `['IF@524']`

```php
  22: $query="update appointmenttb set payment='$status' where contact='$contact';";
  23: $result=mysqli_query($con,$query);
```

### Q041 `newfunc.php:84`, mechanical containment check: OK

- call blocks: `['IF@2143']`
- recovered SQL: `insert into doctb(username)values('{{username}}')`
- chain L83 ASSIGN blocks `['IF@2143']`

```php
  83: $query="insert into doctb(username)values('$username')";
  84: $result=mysqli_query($con,$query);
```

### Q042 `patientsearch.php:15`, mechanical containment check: OK

- call blocks: `['IF@363']`
- recovered SQL: `select * from patreg where contact= '{{contact}}'`
- chain L14 ASSIGN blocks `['IF@363']`

```php
  14: $query = "select * from patreg where contact= '$contact'";
  15: $result = mysqli_query($con,$query);
```

### Q044 `search.php:8`, mechanical containment check: OK

- call blocks: `['IF@80']`
- recovered SQL: `select * from appointmenttb where contact='{{contact}}' and doctor='{{docname}}';`
- chain L7 ASSIGN blocks `['IF@80']`

```php
   7: $query="select * from appointmenttb where contact='$contact' and doctor='$docname';";
   8: $result=mysqli_query($con,$query);
```

Newly recovered in hms_kishan0725: 21

## news_buzz

### Q001 `adduser.php:51`, mechanical containment check: OK

- call blocks: `['ELSE@1131', 'ELSE@1269', 'ELSE@287', 'IF@295']`
- recovered SQL: `INSERT INTO users(username,firstname,lastname,email,password,role) VALUES ('{{username}}' , '{{firstname}}' , '{{lastname}}' , '{{email}}', '{{password}}' , '{{role}}')`
- chain L50 ASSIGN blocks `['ELSE@1269', 'ELSE@1131', 'IF@295', 'ELSE@287']`

```php
  50: $query = "INSERT INTO users(username,firstname,lastname,email,password,role) VALUES ('$username' , '$firstname' , '$lastname' , '$email', '$password' 
  51: $result = mysqli_query($conn , $query) or die(mysqli_error($conn));
```

### Q003 `editposts.php:88`, mechanical containment check: OK

- call blocks: `['ELSE@1493', 'IF@522', 'IF@863']`
- recovered SQL: `UPDATE posts SET title = '{{post_title}}' , tag = '{{post_tag}}' , content='{{post_content}}' , \tstatus = '{{post_status}}' , image = '{{picture}}' , postdate = '{{post_date}}' WHERE id= '{{post_id}}'`
- chain L87 ASSIGN blocks `['ELSE@1493', 'IF@863', 'IF@522']`

```php
  87: $queryupdate = "UPDATE posts SET title = '$post_title' , tag = '$post_tag' , content='$post_content' , 	status = '$post_status' , image = '$picture' ,
  88: $result = mysqli_query($conn , $queryupdate) or die(mysqli_error($conn));
```

### Q028 `login.php:10`, mechanical containment check: OK

- call blocks: `['IF@62']`
- recovered SQL: `SELECT * FROM users WHERE username = '{{username}}'`
- chain L9 ASSIGN blocks `['IF@62']`

```php
   9: $query = "SELECT * FROM users WHERE username = '$username'";
  10: $result = mysqli_query($conn , $query) or die (mysqli_error($conn));
```

### Q007 `posts.php:51`, mechanical containment check: OK

- call blocks: `['IF@623']`
- recovered SQL: `SELECT * FROM posts ORDER BY id DESC`
- chain L50 ASSIGN blocks `['IF@623']`

```php
  50: $query = "SELECT * FROM posts ORDER BY id DESC";
  51: $run_query = mysqli_query($conn, $query) or die(mysqli_error($conn));
```

### Q008 `posts.php:98`, mechanical containment check: OK

- call blocks: `['IF@3125', 'IF@623']`
- recovered SQL: `DELETE FROM posts WHERE id='{{post_del}}'`
- chain L97 ASSIGN blocks `['IF@3125', 'IF@623']`

```php
  97: $del_query = "DELETE FROM posts WHERE id='$post_del'";
  98: $run_del_query = mysqli_query($conn, $del_query) or die (mysqli_error($conn));
```

### Q009 `posts.php:110`, mechanical containment check: OK

- call blocks: `['IF@3673', 'IF@623']`
- recovered SQL: `UPDATE posts SET status='published' WHERE id='{{post_pub}}'`
- chain L109 ASSIGN blocks `['IF@3673', 'IF@623']`

```php
 109: $pub_query = "UPDATE posts SET status='published' WHERE id='$post_pub'";
 110: $run_pub_query = mysqli_query($conn, $pub_query) or die (mysqli_error($conn));
```

### Q010 `posts.php:153`, mechanical containment check: OK

- call blocks: `['ELSE@4249', 'IF@4254']`
- recovered SQL: `SELECT * FROM posts WHERE author = '{{currentuser}}' ORDER BY id DESC`
- chain L152 ASSIGN blocks `['IF@4254', 'ELSE@4249']`

```php
 152: $query = "SELECT * FROM posts WHERE author = '$currentuser' ORDER BY id DESC";
 153: $run_query = mysqli_query($conn, $query) or die(mysqli_error($conn));
```

### Q011 `posts.php:200`, mechanical containment check: OK

- call blocks: `['ELSE@4249', 'IF@4254', 'IF@6841']`
- recovered SQL: `DELETE FROM posts WHERE id='{{post_del}}'`
- chain L199 ASSIGN blocks `['IF@6841', 'IF@4254', 'ELSE@4249']`

```php
 199: $del_query = "DELETE FROM posts WHERE id='$post_del'";
 200: $run_del_query = mysqli_query($conn, $del_query) or die (mysqli_error($conn));
```

### Q012 `posts.php:212`, mechanical containment check: OK

- call blocks: `['ELSE@4249', 'IF@4254', 'IF@7389']`
- recovered SQL: `UPDATE posts SET status='published' WHERE id='{{post_pub}}'`
- chain L211 ASSIGN blocks `['IF@7389', 'IF@4254', 'ELSE@4249']`

```php
 211: $pub_query = "UPDATE posts SET status='published' WHERE id='$post_pub'";
 212: $run_pub_query = mysqli_query($conn, $pub_query) or die (mysqli_error($conn));
```

### Q013 `posts.php:251`, mechanical containment check: OK

- call blocks: `['ELSE@4249', 'ELSE@7965']`
- recovered SQL: `SELECT * FROM posts WHERE author = '{{currentuser}}' ORDER BY id DESC`
- chain L250 ASSIGN blocks `['ELSE@7965', 'ELSE@4249']`

```php
 250: $query = "SELECT * FROM posts WHERE author = '$currentuser' ORDER BY id DESC";
 251: $run_query = mysqli_query($conn, $query) or die(mysqli_error($conn));
```

### Q014 `posts.php:292`, mechanical containment check: OK

- call blocks: `['ELSE@4249', 'ELSE@7965', 'IF@10126']`
- recovered SQL: `DELETE FROM posts WHERE id='{{post_del}}' AND author='{{currentuser}}'`
- chain L291 ASSIGN blocks `['IF@10126', 'ELSE@7965', 'ELSE@4249']`

```php
 291: $del_query = "DELETE FROM posts WHERE id='$post_del' AND author='$currentuser'";
 292: $run_del_query = mysqli_query($conn, $del_query) or die (mysqli_error($conn));
```

### Q015 `profile.php:7`, mechanical containment check: OK

- call blocks: `['IF@84']`
- recovered SQL: `SELECT * FROM users WHERE username = '{{username}}'`
- chain L6 ASSIGN blocks `['IF@84']`

```php
   6: $query = "SELECT * FROM users WHERE username = '$username'" ;
   7: $result= mysqli_query($conn , $query) or die (mysqli_error($conn));
```

### Q016 `profile.php:55`, mechanical containment check: OK

- call blocks: `['ELSE@1397', 'ELSE@1568', 'IF@1573', 'IF@579', 'IF@84']`
- recovered SQL: `UPDATE users SET firstname = '{{userfirstname}}' , lastname='{{userlastname}}' , email='{{useremail}}' WHERE id = '{{userid}}'`
- chain L54 ASSIGN blocks `['IF@1573', 'ELSE@1568', 'ELSE@1397', 'IF@579', 'IF@84']`

```php
  54: $updatequery1 = "UPDATE users SET firstname = '$userfirstname' , lastname='$userlastname' , email='$useremail' WHERE id = '$userid' " ;
  55: $result2 = mysqli_query($conn , $updatequery1) or die(mysqli_error($conn));
```

### Q017 `profile.php:76`, mechanical containment check: OK

- call blocks: `['ELSE@1397', 'ELSE@1568', 'ELSE@2172', 'ELSE@2388', 'IF@579', 'IF@84']`
- recovered SQL: `UPDATE users SET password = '{{userpassword}}', firstname='{{userfirstname}}' , lastname= '{{userlastname}}' , email= '{{useremail}}' WHERE id='{{userid}}'`
- chain L75 ASSIGN blocks `['ELSE@2388', 'ELSE@2172', 'ELSE@1568', 'ELSE@1397', 'IF@579', 'IF@84']`

```php
  75: $updatequery = "UPDATE users SET password = '$userpassword', firstname='$userfirstname' , lastname= '$userlastname' , email= '$useremail' WHERE id='$u
  76: $result1 = mysqli_query($conn , $updatequery) or die(mysqli_error($conn));
```

### Q018 `profile1.php:7`, mechanical containment check: OK

- call blocks: `['IF@84']`
- recovered SQL: `SELECT * FROM users WHERE username = '{{username}}'`
- chain L6 ASSIGN blocks `['IF@84']`

```php
   6: $query = "SELECT * FROM users WHERE username = '$username'" ;
   7: $result= mysqli_query($conn , $query) or die (mysqli_error($conn));
```

### Q019 `profile1.php:68`, mechanical containment check: OK

- call blocks: `['ELSE@1397', 'ELSE@1568', 'ELSE@1688', 'ELSE@1871', 'IF@579', 'IF@84']`
- recovered SQL: `UPDATE users SET password = '{{userpassword}}', firstname='{{userfirstname}}' , lastname= '{{userlastname}}' , email= '{{useremail}}' WHERE id='{{userid}}'`
- chain L67 ASSIGN blocks `['ELSE@1871', 'ELSE@1688', 'ELSE@1568', 'ELSE@1397', 'IF@579', 'IF@84']`

```php
  67: $updatequery = "UPDATE users SET password = '$userpassword', firstname='$userfirstname' , lastname= '$userlastname' , email= '$useremail' WHERE id='$u
  68: $result1 = mysqli_query($conn , $updatequery) or die(mysqli_error($conn));
```

### Q020 `publishnews.php:73`, mechanical containment check: OK

- call blocks: `['ELSE@1198', 'ELSE@1758', 'ELSE@1917', 'ELSE@2031', 'IF@2211', 'IF@454']`
- recovered SQL: `INSERT INTO posts (title,author,postdate,image,content,status,tag) VALUES ('{{post_title}}' , '{{post_author}}' , '{{post_date}}' , '{{picture}}' , '{{post_content}}' , '{{post_status}}', '{{post_tag}}')`
- chain L72 ASSIGN blocks `['IF@2211', 'ELSE@2031', 'ELSE@1917', 'ELSE@1758', 'ELSE@1198', 'IF@454']`

```php
  72: $query = "INSERT INTO posts (title,author,postdate,image,content,status,tag) VALUES ('$post_title' , '$post_author' , '$post_date' , '$picture' , '$po
  73: $result = mysqli_query($conn , $query) or die(mysqli_error($conn));
```

### Q030 `registerprocess.php:38`, mechanical containment check: OK

- call blocks: `['ELSE@1076', 'ELSE@910', 'IF@44']`
- recovered SQL: `SELECT * FROM users WHERE username = '{{username}}'`
- chain L37 ASSIGN blocks `['ELSE@1076', 'ELSE@910', 'IF@44']`

```php
  37: $checkusername = "SELECT * FROM users WHERE username = '$username'";
  38: $run_check = mysqli_query($conn , $checkusername) or die(mysqli_error($conn));
```

### Q031 `registerprocess.php:51`, mechanical containment check: OK

- call blocks: `['ELSE@1076', 'ELSE@910', 'IF@44']`
- recovered SQL: `INSERT INTO users(username,firstname,lastname,email,password) VALUES ('{{username}}' , '{{firstname}}' , '{{lastname}}' , '{{email}}', '{{password}}')`
- chain L50 ASSIGN blocks `['ELSE@1076', 'ELSE@910', 'IF@44']`

```php
  50: $query = "INSERT INTO users(username,firstname,lastname,email,password) VALUES ('$username' , '$firstname' , '$lastname' , '$email', '$password')";
  51: $result = mysqli_query($conn , $query) or die(mysqli_error($conn));
```

### Q032 `search.php:19`, mechanical containment check: OK

- call blocks: `['IF@350']`
- recovered SQL: `SELECT * FROM posts WHERE tag LIKE '%{{search}}%' AND status='published'`
- chain L18 ASSIGN blocks `['IF@350']`

```php
  18: $query = "SELECT * FROM posts WHERE tag LIKE '%$search%' AND status='published'";
  19: $search_query = mysqli_query($conn, $query) or die(mysqli_error($conn));
```

### Q021 `users.php:53`, mechanical containment check: OK

- call blocks: `['ELSE@289', 'IF@294']`
- recovered SQL: `SELECT * FROM users`
- chain L52 ASSIGN blocks `['IF@294', 'ELSE@289']`

```php
  52: $query = "SELECT * FROM users";
  53: $select_users = mysqli_query($conn, $query) or die(mysqli_error($conn));
```

### Q022 `users.php:84`, mechanical containment check: OK

- call blocks: `['ELSE@289', 'IF@2395', 'IF@294']`
- recovered SQL: `SELECT role FROM users WHERE id = '{{the_user_id}}'`
- chain L83 ASSIGN blocks `['IF@2395', 'IF@294', 'ELSE@289']`

```php
  83: $query0 = "SELECT role FROM users WHERE id = '$the_user_id'";
  84: $result = mysqli_query($conn , $query0) or die(mysqli_error($conn));
```

### Q023 `users.php:96`, mechanical containment check: OK

- call blocks: `['ELSE@289', 'ELSE@2890', 'IF@2395', 'IF@294']`
- recovered SQL: `DELETE FROM users WHERE id = '{{the_user_id}}'`
- chain L94 ASSIGN blocks `['ELSE@2890', 'IF@2395', 'IF@294', 'ELSE@289']`

```php
  94: $query = "DELETE FROM users WHERE id = '$the_user_id'";
  96: $delete_query = mysqli_query($conn, $query) or die (mysqli_error($conn));
```

### Q024 `users.php:109`, mechanical containment check: OK

- call blocks: `['ELSE@289', 'IF@294', 'IF@3254']`
- recovered SQL: `SELECT role FROM users WHERE id = '{{the_user_id}}'`
- chain L108 ASSIGN blocks `['IF@3254', 'IF@294', 'ELSE@289']`

```php
 108: $query0 = "SELECT role FROM users WHERE id = '$the_user_id'";
 109: $result = mysqli_query($conn , $query0) or die(mysqli_error($conn));
```

### Q025 `users.php:123`, mechanical containment check: OK

- call blocks: `['ELSE@289', 'ELSE@3756', 'ELSE@3884', 'IF@294', 'IF@3254']`
- recovered SQL: `UPDATE users SET role = 'admin' WHERE id = '{{the_user_id}}'`
- chain L121 ASSIGN blocks `['ELSE@3884', 'ELSE@3756', 'IF@3254', 'IF@294', 'ELSE@289']`

```php
 121: $query = "UPDATE users SET role = 'admin' WHERE id = '$the_user_id'";
 123: $change_to_admin_query = mysqli_query($conn, $query) or die (mysqli_error($conn));
```

### Q026 `users.php:176`, mechanical containment check: OK

- call blocks: `['ELSE@289', 'ELSE@4331']`
- recovered SQL: `SELECT * FROM users`
- chain L175 ASSIGN blocks `['ELSE@4331', 'ELSE@289']`

```php
 175: $query = "SELECT * FROM users";
 176: $select_users = mysqli_query($conn, $query) or die(mysqli_error($conn));
```

Newly recovered in news_buzz: 26

## fee_rainbow

### Q015 `products.php:165`, mechanical containment check: OK

- call blocks: `['IF@5251']`
- recovered SQL: `DELETE FROM products WHERE id = {{id}}`
- chain L164 ASSIGN blocks `['IF@5251']`

```php
 164: $queryDelete = "DELETE FROM products WHERE id = $id";
 165: mysqli_query($conn, $queryDelete);
```

Newly recovered in fee_rainbow: 1

