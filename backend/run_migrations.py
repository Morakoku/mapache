#!/usr/bin/env python3
"""Execute SQL migrations on Supabase via supabase CLI, batched."""
import subprocess
import sys
from pathlib import Path

SUPABASE_CMD = r"C:\Users\edwin\AppData\Local\hermes\node\supabase.cmd"

def split_sql_statements(sql_text):
    """Split SQL into individual statements."""
    statements = []
    current = []
    in_single = False
    in_double = False
    paren_depth = 0
    
    for char in sql_text:
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
    
    return statements


def execute_batch(sql, timeout=20):
    """Execute SQL via supabase db query --linked."""
    result = subprocess.run(
        [SUPABASE_CMD, 'db', 'query', '--linked'],
        input=sql,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    return result


def main():
    sql_file = Path("C:/Users/edwin/Documents/Trinidad/mapache/backend/clean_migrations.sql")
    sql_text = sql_file.read_text()
    
    statements = split_sql_statements(sql_text)
    print(f"Total statements: {len(statements)}")
    
    skip = int(sys.argv[1]) if len(sys.argv) > 1 else 0
    batch_size = 5  # statements per call
    
    success = 0
    errors = []
    skipped = 0
    
    batch = []
    batch_start = skip + 1
    
    for i, stmt in enumerate(statements, 1):
        if i <= skip:
            continue
        batch.append((i, stmt))
        
        if len(batch) >= batch_size or i == len(statements) or i == len([s for s in statements if True]):
            # Execute batch
            batch_sql = '\n'.join(s for _, s in batch)
            display = f"[{batch[0][0]}-{batch[-1][0]}] {len(batch)} statements"
            
            result = execute_batch(batch_sql)
            
            if result.returncode == 0:
                for idx, stmt in batch:
                    s_display = stmt[:60].replace('\n', ' ') + ('...' if len(stmt) > 60 else '')
                    print(f"[{idx:3d}] ✅ {s_display}")
                    success += 1
            else:
                stderr = result.stderr
                if 'already exists' in stderr or 'duplicate' in stderr.lower():
                    for idx, stmt in batch:
                        print(f"[{idx:3d}] ⏭️  SKIP")
                        skipped += 1
                else:
                    print(f"[{batch[0][0]:3d}-{batch[-1][0]:3d}] ❌ {stderr[:100]}")
                    for idx, stmt in batch:
                        errors.append({"i": idx, "stmt": stmt[:60], "error": stderr[:100]})
            
            batch = []
    
    print(f"\n{'='*50}")
    print(f"✅ SUCCESS: {success}")
    print(f"⏭️  SKIPPED: {skipped}")
    print(f"❌ FAILED: {len(errors)}")
    
    if errors:
        print("\nErrors:")
        for e in errors[:5]:
            print(f"  [{e['i']}] {e['stmt']}: {e['error']}")


if __name__ == "__main__":
    main()
