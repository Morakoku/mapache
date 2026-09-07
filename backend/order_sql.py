#!/usr/bin/env python3
"""Execute SQL migrations on Supabase via direct DB connection."""
import json
from pathlib import Path
import re
import sys

# Read SQL file
sql_file = Path("/tmp/migrations_v2.sql")
if not sql_file.exists():
    sql_file = Path("C:/Users/edwin/Documents/Trinidad/mapache/backend/migrations.sql")

sql_text = sql_file.read_text()

# Clean INFO lines
lines = [l for l in sql_text.split('\n') if not l.startswith('INFO')]
clean_sql = '\n'.join(lines)

# Extract CREATE statements in correct order:
# 1. CREATE TYPE (ENUMs must be first)
# 2. CREATE EXTENSION
# 3. CREATE TABLE
# 4. CREATE INDEX
# 5. Other (INSERT, ALTER, etc.)

create_types = re.findall(r'(CREATE TYPE [^;]+;)', clean_sql, re.IGNORECASE | re.DOTALL)
create_ext = re.findall(r'(CREATE EXTENSION [^;]+;)', clean_sql, re.IGNORECASE | re.DOTALL)
create_tables = re.findall(r'(CREATE TABLE [^;]+;)', clean_sql, re.IGNORECASE | re.DOTALL)
create_indexes = re.findall(r'(CREATE (?:UNIQUE )?INDEX [^;]+;)', clean_sql, re.IGNORECASE | re.DOTALL)
other = re.findall(r'(INSERT INTO [^;]+;|ALTER [^;]+;|COMMENT ON [^;]+;)', clean_sql, re.IGNORECASE | re.DOTALL)

ordered = '\n\n'.join(create_types + create_ext + create_tables + create_indexes + other)

# Save ordered SQL
ordered_path = Path("/tmp/migrations_ordered_v2.sql")
ordered_path.write_text(ordered)

print(f"Original SQL: {len(clean_sql)} chars")
print(f"Statements:")
print(f"  CREATE TYPE: {len(create_types)}")
print(f"  CREATE EXTENSION: {len(create_ext)}")
print(f"  CREATE TABLE: {len(create_tables)}")
print(f"  CREATE INDEX: {len(create_indexes)}")
print(f"  Other: {len(other)}")
print(f"\nOrdered SQL saved to: {ordered_path}")
print(f"Total: {len(create_types) + len(create_ext) + len(create_tables) + len(create_indexes) + len(other)} statements")
print()

# Verify no super() truncation
for i, stmt in enumerate(create_tables + create_indexes):
    if 'super()' in stmt:
        print(f"⚠️  WARNING: super() found in statement {i+1}")
        print(f"  {stmt[:200]}")
