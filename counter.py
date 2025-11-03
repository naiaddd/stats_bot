import sys
from datetime import datetime

def count_nonblank_lines(filepath):
    with open(filepath, 'r', encoding='utf-8') as f:
        lines = f.readlines()
    nonblank_lines = [line for line in lines if line.strip()]
    return len(nonblank_lines), lines

def write_header(filepath, count):
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    header = f"# Real Linecount : {count} as of {timestamp}\n"

    count, lines = count_nonblank_lines(filepath)
    # Avoid stacking multiple headers if re-run
    if lines and lines[0].startswith("# Real Linecount :"):
        lines[0] = header
    else:
        lines.insert(0, header)

    with open(filepath, 'w', encoding='utf-8') as f:
        f.writelines(lines)

if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: python count_nonblank.py <file.py>")
        sys.exit(1)

    path = sys.argv[1]
    try:
        count, _ = count_nonblank_lines(path)
        write_header(path, count)
        print(f"Updated '{path}' with line count {count}.")
    except FileNotFoundError:
        print(f"File not found: {path}")
