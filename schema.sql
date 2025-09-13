-- Users table
CREATE TABLE IF NOT EXISTS users (
    id SERIAL PRIMARY KEY,
    name TEXT NOT NULL,
    email TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    role TEXT NOT NULL CHECK (role IN ('admin', 'user'))
);

-- Daily milk production table
CREATE TABLE IF NOT EXISTS productions (
    id SERIAL PRIMARY KEY,
    date DATE UNIQUE NOT NULL,
    total_liters REAL NOT NULL CHECK (total_liters >= 0)
);

-- Deliveries to users
CREATE TABLE IF NOT EXISTS deliveries (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    date DATE NOT NULL,
    liters REAL NOT NULL CHECK (liters >= 0),
    UNIQUE(user_id, date)
);
