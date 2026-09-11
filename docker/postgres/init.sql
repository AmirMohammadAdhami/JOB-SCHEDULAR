-- init.sql: Executed when the PostgreSQL container starts for the first time.
-- The POSTGRES_DB, POSTGRES_USER, POSTGRES_PASSWORD env vars in docker-compose
-- handle database and user creation automatically. This file is for any
-- additional setup we need.

-- Enable the uuid-ossp extension for gen_random_uuid()
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- Enable the pgcrypto extension (needed for gen_random_uuid in older PG versions)
CREATE EXTENSION IF NOT EXISTS "pgcrypto";
