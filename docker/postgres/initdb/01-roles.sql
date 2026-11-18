\set ON_ERROR_STOP on
\getenv owner_password HAATLY_DB_OWNER_PASSWORD
\getenv app_password HAATLY_DB_APP_PASSWORD
\getenv ops_password HAATLY_DB_OPS_PASSWORD

SELECT format('CREATE ROLE haatly_owner LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS PASSWORD %L', :'owner_password') \gexec
SELECT format('CREATE ROLE haatly_app LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS PASSWORD %L', :'app_password') \gexec
SELECT format('CREATE ROLE haatly_ops LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE BYPASSRLS PASSWORD %L', :'ops_password') \gexec

ALTER ROLE haatly_ops SET log_statement = 'all';
ALTER ROLE haatly_ops SET application_name = 'haatly-break-glass';

SELECT format('ALTER DATABASE %I OWNER TO haatly_owner', current_database()) \gexec
SELECT format('ALTER DATABASE %I SET timezone = %L', current_database(), 'UTC') \gexec
SELECT format('REVOKE ALL ON DATABASE %I FROM PUBLIC', current_database()) \gexec
SELECT format('GRANT CONNECT ON DATABASE %I TO haatly_owner, haatly_app, haatly_ops', current_database()) \gexec

CREATE EXTENSION IF NOT EXISTS postgis;
CREATE EXTENSION IF NOT EXISTS pg_trgm;

REVOKE CREATE ON SCHEMA public FROM PUBLIC;
GRANT USAGE ON SCHEMA public TO haatly_app, haatly_ops;

ALTER DEFAULT PRIVILEGES FOR ROLE haatly_owner GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO haatly_app, haatly_ops;
ALTER DEFAULT PRIVILEGES FOR ROLE haatly_owner GRANT USAGE, SELECT ON SEQUENCES TO haatly_app, haatly_ops;
ALTER DEFAULT PRIVILEGES FOR ROLE haatly_owner REVOKE EXECUTE ON FUNCTIONS FROM PUBLIC;
ALTER DEFAULT PRIVILEGES FOR ROLE haatly_owner GRANT EXECUTE ON FUNCTIONS TO haatly_app, haatly_ops;
