-- Phase 4e runtime check: a read-only user for the substitute replay API (tools/runtime/replay/). Not used by phpBlog.
CREATE USER 'c2replay'@'%' IDENTIFIED BY 'c2replay';
GRANT SELECT ON `phpblog`.* TO 'c2replay'@'%';
FLUSH PRIVILEGES;
