#!/usr/bin/env python3
"""Generate SQL compatible with Supabase Management API (no ENUMs)."""
import re
import sys

def convert_enums_to_text(sql_text):
    """Convert PostgreSQL ENUMs to TEXT with CHECK constraints."""
    
    # Find all CREATE TYPE ... AS ENUM statements
    enum_pattern = r"CREATE TYPE (\w+) AS ENUM \((.*?)\);\n"
    enums = re.findall(enum_pattern, sql_text)
    
    if not enums:
        return sql_text
    
    # Build replacement mapping
    for enum_name, values_str in enums:
        values = [v.strip().strip("'") for v in values_str.split(',')]
        
        # Replace usages of this ENUM type with TEXT
        sql_text = re.sub(rf'\b{enum_name}\b', 'TEXT', sql_text)
        
        # Add CHECK constraints where this type is used in CREATE TABLE
        # Find CREATE TABLE statements that reference this type
        table_pattern = rf'CREATE TABLE (\w+)\s*\((.*?)\);'
        tables = re.findall(table_pattern, sql_text, re.DOTALL)
        
        for table_name, table_body in tables:
            # Check if any column uses this enum type (now TEXT)
            col_pattern = rf'(\w+)\s+TEXT'
            cols = re.findall(col_pattern, table_body)
            for col in cols:
                # Add CHECK constraint if not already present
                constraint_name = f"chk_{table_name}_{col}_{enum_name}"
                if constraint_name not in sql_text:
                    # Find the column line and add constraint
                    old_line = f"{col} TEXT"
                    new_line = f"{col} TEXT CHECK ({col} IN ({', '.join(repr(v) for v in values)}))"
                    # Only replace in the CREATE TABLE context
                    sql_text = sql_text.replace(old_line, new_line, 1)
    
    # Remove CREATE TYPE statements
    sql_text = re.sub(enum_pattern, '', sql_text)
    
    return sql_text


def main():
    with open(sys.argv[1], "r", encoding="utf-8") as f:
        sql_text = f.read()
    
    # Remove INFO and comment lines
    lines = sql_text.split('\n')
    lines = [l for l in lines if not l.startswith('INFO ') and not l.startswith('--')]
    sql_text = '\n'.join(lines)
    
    # Convert ENUMs to TEXT
    sql_text = convert_enums_to_text(sql_text)
    
    # Write output
    output_path = sys.argv[2] if len(sys.argv) > 2 else sys.argv[1].replace('.sql', '_converted.sql')
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(sql_text)
    
    print(f"✅ SQL convertido: {output_path}")


if __name__ == "__main__":
    main()
