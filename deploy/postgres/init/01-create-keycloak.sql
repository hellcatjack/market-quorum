\set ON_ERROR_STOP on
SELECT 'CREATE DATABASE keycloak OWNER tradingng'
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'keycloak')\gexec
