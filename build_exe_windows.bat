@echo off
cd /d "%~dp0"
python -m pip install --upgrade pyinstaller
for /f "delims=" %%v in ('python -c "from app.version import __version__; print(__version__)"') do set SSRE_VERSION=%%v
pyinstaller --noconfirm --clean --windowed --onefile --name SecondSightRE_v%SSRE_VERSION% main.py

echo.
echo Build complete: dist\SecondSightRE_v%SSRE_VERSION%.exe
pause
