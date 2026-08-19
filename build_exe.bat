@echo off
REM build_exe.bat — Builds an encrypted/obfuscated, standalone .exe.
REM Run from the ECMS folder: build_exe.bat
REM Output: dist\ExamManagementSystem.exe

echo ============================================
echo Step 1: Installing build tools
echo ============================================
pip install pyinstaller pyarmor bcrypt supabase streamlit pymupdf pandas openpyxl

echo.
echo ============================================
echo Step 2: Obfuscating source with pyarmor
echo ============================================
if exist dist_protected rmdir /s /q dist_protected
pyarmor gen -O dist_protected run_app.py app.py auth.py db.py admin_panel.py cs_panel.py student_portal.py owner_panel.py data_ingestion.py seat_assignment.py reporting.py remuneration.py

if not exist dist_protected (
    echo pyarmor step failed - see errors above.
    exit /b 1
)

echo.
echo ============================================
echo Step 3: Copying .streamlit (secrets) and assets into protected build
echo ============================================
xcopy /E /I /Y .streamlit dist_protected\.streamlit
xcopy /E /I /Y assets dist_protected\assets

echo.
echo ============================================
echo Step 4: Packaging with PyInstaller
echo ============================================
cd dist_protected
pyinstaller --onefile --name ExamManagementSystem ^
  --add-data "app.py;." --add-data "auth.py;." --add-data "db.py;." ^
  --add-data "admin_panel.py;." --add-data "cs_panel.py;." ^
  --add-data "student_portal.py;." --add-data "owner_panel.py;." ^
  --add-data "data_ingestion.py;." --add-data "seat_assignment.py;." ^
  --add-data "reporting.py;." --add-data "remuneration.py;." ^
  --add-data "assets;assets" ^
  --add-data ".streamlit;.streamlit" ^
  run_app.py

echo.
echo ============================================
echo Done. Your .exe is at:
echo   dist_protected\dist\ExamManagementSystem.exe
echo ============================================
echo.
echo IMPORTANT: Test it on a machine WITHOUT Python installed before
echo distributing, to catch any missing-dependency issues early.
cd ..
pause
