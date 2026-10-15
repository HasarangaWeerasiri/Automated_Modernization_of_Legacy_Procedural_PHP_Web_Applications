-- Phase 4 runtime check: a read-only user for the substitute replay API (tools/runtime/replay/). Not used by HMS.
CREATE USER 'c2replay'@'%' IDENTIFIED BY 'c2replay';
GRANT SELECT ON `myhmsdb`.* TO 'c2replay'@'%';
FLUSH PRIVILEGES;
