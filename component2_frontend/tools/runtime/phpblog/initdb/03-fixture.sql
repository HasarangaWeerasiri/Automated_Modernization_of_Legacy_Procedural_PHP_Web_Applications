-- Phase 4f runtime check: a recorded data fixture for phpBlog (owner, Phase 4f). DATA ONLY: no phpBlog file and no
-- generated code changes. Loaded after install/database.sql (01) and the replay user (02), only by the
-- docker-compose.fixture.yml override; the shipped-seed run does not load it. No users: nobody can log in.
-- Values are written as phpBlog's own admin pages write them: dates in the settings' date_format (d.m.Y,
-- admin/add_post.php 11), times H:i (12), content HTML-escaped (as install/database.sql stores its page).

INSERT INTO `categories` (`id`, `category`, `slug`) VALUES
(2, 'Tutorials', 'tutorials');

INSERT INTO `posts` (`id`, `category_id`, `title`, `slug`, `image`, `content`, `author_id`, `date`, `time`, `active`, `featured`, `views`) VALUES
(1, 1, 'Welcome to the blog', 'welcome-to-the-blog', '', '&lt;p&gt;This is the first post of the fixture.&lt;/p&gt;', 1, '01.10.2026', '09:00', 'Yes', 'Yes', 12),
(2, 2, 'Writing a first page', 'writing-a-first-page', '', '&lt;p&gt;A short tutorial post.&lt;/p&gt;', 1, '05.10.2026', '10:30', 'Yes', 'No', 7),
(3, 2, 'Lists and tables', 'lists-and-tables', '', '&lt;p&gt;A second tutorial post.&lt;/p&gt;', 1, '10.10.2026', '14:15', 'Yes', 'No', 3);

INSERT INTO `pages` (`id`, `title`, `slug`, `content`) VALUES
(2, 'Terms', 'terms', '&lt;p&gt;Fixture page: terms of use.&lt;/p&gt;');

INSERT INTO `comments` (`id`, `post_id`, `user_id`, `comment`, `date`, `time`, `approved`, `guest`) VALUES
(1, 1, 'Reader', 'Nice start.', '02.10.2026', '08:10', 'Yes', 'Yes'),
(2, 2, 'Student', 'Thanks, this helped.', '06.10.2026', '19:45', 'Yes', 'Yes');

INSERT INTO `menu` (`id`, `page`, `path`, `fa_icon`) VALUES
(6, 'Terms', 'page?name=terms', 'fa-file-alt');
