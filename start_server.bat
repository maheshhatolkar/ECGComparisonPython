@echo off
REM Start ECGComparisonPython Streamlit server
REM Usage: double-click this file or run it from CMD/PowerShell.

:: Change to project directory (allows relative imports and files to resolve)
cd /d C:\Projects\ECGComparisonPython

n:: Run the Streamlit app using the virtual environment Python
"C:\Projects\ECGComparisonPython\.venv\Scripts\python.exe" -m streamlit run ECGComparisonPython.py

nIF %ERRORLEVEL% NEQ 0 (
    echo Streamlit exited with error code %ERRORLEVEL%.
    pause
)
