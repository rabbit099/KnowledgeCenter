-- Test database only (tmpfs, thrown away after the run). Creates a NON-superuser owner,
-- so the tests prove that the search objects can be created without superuser rights.
CREATE ROLE app LOGIN PASSWORD 'test' NOSUPERUSER NOCREATEDB NOCREATEROLE;
CREATE DATABASE kb_test OWNER app;
