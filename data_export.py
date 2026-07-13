"""Data export helpers for ECGComparisonPython

Exports all database tables to CSV files and a single Excel workbook (one sheet per table).
"""
from __future__ import annotations

import os
import sqlite3
from typing import List, Dict, Optional
import zipfile

import pandas as pd

try:
    # Prefer to reuse the project's StoragePaths for default locations
    from db import StoragePaths
except Exception:  # pragma: no cover - defensive
    StoragePaths = None



import json


def _get_table_names(conn: sqlite3.Connection) -> List[str]:
    # Query the SQLite catalog to enumerate user tables. We explicitly
    # exclude internal sqlite_ tables to avoid attempting to export
    # implementation-specific metadata.
    rows = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'").fetchall()
    return [r[0] for r in rows]


def get_table_data(conn: sqlite3.Connection, table_name: str) -> pd.DataFrame:
    """Read a table from the database and apply export formatting rules.

    - For 'ecg_records', excludes 'image_hash' and 'image_filename', and expands 'analysis_json' fields.
    """
    df = pd.read_sql_query(f"SELECT * FROM [{table_name}]", conn)
    if table_name == "ecg_records":
        cols_to_drop = [c for c in ["image_hash", "image_filename"] if c in df.columns]
        if cols_to_drop:
            df = df.drop(columns=cols_to_drop)

        if "analysis_json" in df.columns:
            expanded_rows = []
            for _, row in df.iterrows():
                expanded = {}
                analysis_str = row["analysis_json"]
                if isinstance(analysis_str, str) and analysis_str.strip():
                    try:
                        analysis = json.loads(analysis_str)
                        if isinstance(analysis, dict):
                            for k, v in analysis.items():
                                # Unpack top-level field
                                if isinstance(v, (dict, list)):
                                    expanded[k] = json.dumps(v)
                                else:
                                    expanded[k] = v

                                # If it's a dict, expand it
                                if isinstance(v, dict):
                                    for sub_k, sub_v in v.items():
                                        val_str = json.dumps(sub_v) if isinstance(sub_v, (dict, list)) else sub_v
                                        expanded[f"{k}_{sub_k}"] = val_str
                                        expanded[sub_k] = val_str
                        else:
                            expanded["analysis_json_value"] = analysis_str
                    except Exception:
                        expanded["analysis_json_error"] = "Failed to parse JSON"
                expanded_rows.append(expanded)

            expanded_df = pd.DataFrame(expanded_rows, index=df.index)
            df = df.drop(columns=["analysis_json"])
            df = pd.concat([df, expanded_df], axis=1)

    return df


def export_all_tables_to_csv(paths: Optional[StoragePaths] = None, output_dir: Optional[str] = None) -> List[str]:
    """Export all tables in the SQLite database to individual CSV files.

    Returns list of file paths written.
    """
    if paths is None:
        if StoragePaths is None:
            raise RuntimeError("StoragePaths not available; please pass a paths argument")
        paths = StoragePaths.current()

    if output_dir is None:
        output_dir = os.path.join(paths.data_dir, "exports")
    os.makedirs(output_dir, exist_ok=True)

    written: List[str] = []
    with sqlite3.connect(paths.db_path) as conn:
        table_names = _get_table_names(conn)
        # Export each table into its own CSV file. If a particular table
        # cannot be read (for example, odd data or schema edge-cases), we
        # log nothing and continue exporting the remaining tables so a
        # single issue does not prevent a full export.
        for table in table_names:
            try:
                df = get_table_data(conn, table)
            except Exception:
                # If reading a table fails, skip it but continue exporting others
                continue
            file_path = os.path.join(output_dir, f"{table}.csv")
            df.to_csv(file_path, index=False)
            written.append(file_path)

    return written


def export_all_tables_to_excel(paths: Optional[StoragePaths] = None, excel_path: Optional[str] = None) -> str:
    """Export all tables into a single Excel workbook (one sheet per table).

    Returns the excel file path written.
    """
    if paths is None:
        if StoragePaths is None:
            raise RuntimeError("StoragePaths not available; please pass a paths argument")
        paths = StoragePaths.current()

    exports_dir = os.path.join(paths.data_dir, "exports")
    os.makedirs(exports_dir, exist_ok=True)

    if excel_path is None:
        excel_path = os.path.join(exports_dir, "ecg_all_tables.xlsx")

    # Read each table and write it into the Excel workbook as its own
    # sheet. Excel sheet names are limited to 31 characters so we enforce
    # that to avoid errors from long table names.
    with sqlite3.connect(paths.db_path) as conn:
        table_names = _get_table_names(conn)
    # Try to pick an available Excel writer engine. pandas defaults to
    # openpyxl for .xlsx files but that package may not be installed in
    # every environment. Fall back to XlsxWriter if available. If neither
    # is installed, gracefully fall back to zipping CSV exports so the
    # user still receives a single archive containing all table CSVs.
    engine = None
    try:
        import openpyxl  # noqa: F401
        engine = "openpyxl"
    except Exception:
        try:
            import xlsxwriter  # noqa: F401
            engine = "xlsxwriter"
        except Exception:
            engine = None

    if engine is None:
        # Fallback: write CSVs then zip them. Use .zip target when no Excel
        # engine is available so the caller still gets a single downloadable
        # file containing all tables.
        csv_files = export_all_tables_to_csv(paths=paths, output_dir=exports_dir)
        zip_path = excel_path
        # If the default excel_path ends with .xlsx, switch to .zip
        if zip_path.endswith(".xlsx"):
            zip_path = zip_path[:-5] + ".zip"
        with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            for fpath in csv_files:
                zf.write(fpath, arcname=os.path.basename(fpath))
        return zip_path

    # Use pandas ExcelWriter with the selected engine
    with pd.ExcelWriter(excel_path, engine=engine) as writer:
        with sqlite3.connect(paths.db_path) as conn:
            for table in table_names:
                try:
                    df = get_table_data(conn, table)
                except Exception:
                    # skip unreadable tables
                    continue
                # Excel sheet names limited to 31 chars
                sheet_name = table[:31]
                df.to_excel(writer, sheet_name=sheet_name, index=False)

    return excel_path


def export_all_data(paths: Optional[StoragePaths] = None, output_dir: Optional[str] = None, excel_path: Optional[str] = None) -> Dict[str, Optional[object]]:
    """Convenience function to export both CSV files and an Excel workbook.

    Returns a dict: {"csv_files": [...], "excel_file": str}
    """
    csv_files = export_all_tables_to_csv(paths=paths, output_dir=output_dir)
    excel_file = export_all_tables_to_excel(paths=paths, excel_path=excel_path)
    return {"csv_files": csv_files, "excel_file": excel_file}
