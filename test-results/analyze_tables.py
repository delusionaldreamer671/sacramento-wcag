"""Quick script to analyze table structure in HTML outputs."""
import sys
from html.parser import HTMLParser
from pathlib import Path


class TableAnalyzer(HTMLParser):
    def __init__(self):
        super().__init__()
        self.tables = []
        self.in_table = False
        self.in_thead = False
        self.in_tbody = False
        self.in_tr = False
        self.in_cell = False
        self.current_row = []
        self.current_cell = ""
        self.header_rows = []
        self.body_rows = []

    def handle_starttag(self, tag, attrs):
        if tag == "table":
            self.in_table = True
            self.header_rows = []
            self.body_rows = []
        elif tag == "thead":
            self.in_thead = True
        elif tag == "tbody":
            self.in_tbody = True
        elif tag == "tr":
            self.in_tr = True
            self.current_row = []
        elif tag in ("td", "th"):
            self.in_cell = True
            self.current_cell = ""

    def handle_data(self, data):
        if self.in_cell:
            self.current_cell += data

    def handle_endtag(self, tag):
        if tag in ("td", "th"):
            self.in_cell = False
            self.current_row.append(self.current_cell.strip())
        elif tag == "tr":
            self.in_tr = False
            if self.in_thead:
                self.header_rows.append(self.current_row)
            elif self.in_tbody:
                self.body_rows.append(self.current_row)
        elif tag == "thead":
            self.in_thead = False
        elif tag == "tbody":
            self.in_tbody = False
        elif tag == "table":
            self.in_table = False
            self.tables.append({
                "header_rows": list(self.header_rows),
                "body_rows": list(self.body_rows),
            })


def analyze(html_path: str):
    with open(html_path, encoding="utf-8") as f:
        html = f.read()

    analyzer = TableAnalyzer()
    analyzer.feed(html)

    print(f"\n=== {Path(html_path).name} === ({len(analyzer.tables)} tables)")
    for i, t in enumerate(analyzer.tables):
        cols_h = len(t["header_rows"][0]) if t["header_rows"] else 0
        body_cols = set(len(r) for r in t["body_rows"])
        ok = "OK" if body_cols == {cols_h} or (not body_cols and cols_h) else "MISMATCH"
        print(f"  Table {i}: header={cols_h}cols, body={sorted(body_cols)}, rows={len(t['body_rows'])} [{ok}]")
        if ok == "MISMATCH":
            for j, row in enumerate(t["body_rows"]):
                if len(row) != cols_h:
                    text = " | ".join(r[:50] for r in row)
                    print(f"    Row {j} ({len(row)} cols): {text[:150]}")
                    if j > 8:
                        print(f"    ... (showing first 10 mismatched rows)")
                        break


import io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

if __name__ == "__main__":
    results_dir = Path("C:/Users/sahaj/sacramento-wcag/test-results")
    # Analyze files with known issues
    for name in ["BLApp.html", "SCTDF TIF Fees Tables -Acella Effective 3-31-25.html",
                  "2019 SCTDF TIF -Nexus Study With App A-H.html",
                  "Tree_Permit_Application.html"]:
        path = results_dir / name
        if path.exists():
            analyze(str(path))
