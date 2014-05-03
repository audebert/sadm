CREATE USER hfs WITH PASSWORD 'DEFAULT_PASSWORD';
CREATE DATABASE hfs OWNER hfs;
\c hfs; -- Connect to database hfs
SET ROLE hfs; -- Switch to user hfs
CREATE TABLE user_location(id SERIAL, hfs INTEGER, username VARCHAR);
