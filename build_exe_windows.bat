@echo off
cd /d "%~dp0"
python -m pip install --upgrade pyinstaller
pyinstaller --noconfirm --clean --windowed --onefile --name SecondSightExtractor main.py

echo.
echo Build complete: dist\SecondSightExtractor.exe
pause
