#!/bin/bash
# Execute SQL migrations on Supabase via supabase CLI
# Usage: bash run_migrations.sh clean_migrations.sql

SQL_FILE="$1"
if [ -z "$SQL_FILE" ]; then
    echo "Usage: $0 <sql_file>"
    exit 1
fi

echo "=== Executing migrations from $SQL_FILE ==="

# Read the entire file
CONTENT=$(cat "$SQL_FILE")

# Use Python to split into statements and execute one by one
python3.14 << 'PYEOF'
import subprocess
import sys

sql_file = sys.argv[1] if len(sys.argv) > 1 else "clean_migrations.sql"

with open(sql_file, 'r') as f:
    sql = f.read()

# Split into statements
statements = []
current = []
in_single = False
in_double = False
paren_depth = 0

for char in sql:
    if char == "'" and not in_double:
        in_single = not in_single
    elif char == '"' and not in_single:
        in_double = not in_double
    if not in_single and not in_double:
        if char == '(':
            paren_depth += 1
        elif char == ')':
            paren_depth -= 1
    if char == ';' and not in_single and not in_double and paren_depth == 0:
        stmt = ''.join(current).strip()
        if stmt and stmt != 'BEGIN' and not stmt.startswith('--'):
            statements.append(stmt)
        current = []
    else:
        current.append(char)

stmt = ''.join(current).strip()
if stmt and stmt != 'BEGIN' and not stmt.startswith('--'):
    statements.append(stmt)

print(f"Total statements: {len(statements)}")

success = 0
errors = []
skipped = 0

for i, s in enumerate(statements, 1):
    display = s[:70].replace('\n', ' ') + ('...' if len(s) > 70 else '')
    
    # Use bash to pipe to supabase CLI
    result = subprocess.run(
        ['bash', '-c', f'echo {repr(s)} | supabase db query --linked'],
        capture_output=True,
        text=True,
        timeout=15,
    )
    
    if result.returncode == 0:
        print(f"[{i:3d}] ✅ {display}")
        success += 1
    else:
        stderr = result.stderr
        if 'already exists' in stderr or 'duplicate' in stderr.lower():
            print(f"[{i:3d}] ⏭️  SKIP")
            skipped += 1
        else:
            print(f"[{i:3d}] ❌ {stderr[:100]}")
            errors.append({"i": i, "stmt": display, "error": stderr[:150]})
            if len(errors) >= 3:
                print("\n⛔ Stopping after 3 errors")
                break

print(f"\n{'='*50}")
print(f"✅ SUCCESS: {success}")
print(f"⏭️  SKIPPED: {skipped}")
print(f"❌ FAILED: {len(errors)}")

if errors:
    print("\nErrors:")
    for e in errors:
        print(f"  [{e['i']}] {e['stmt'][:60]}: {e['error'][:80]}")
PYEOF "$SQL_FILE"
