@echo off
setlocal
python -m venv .venv || goto :err
call .venv\Scripts\activate
pip install -r requirements.txt pyinstaller || goto :err
pyinstaller --noconfirm --noconsole --name Constelyric --icon assets\icon.ico --collect-all winsdk constelyric.py || goto :err
echo.
echo App built: dist\Constelyric\Constelyric.exe
set ISCC="C:\Program Files (x86)\Inno Setup 6\ISCC.exe"
if exist %ISCC% (
  %ISCC% installer\Constelyric.iss
  echo Installer built in dist\
) else (
  echo Inno Setup not found - skipping installer. Get it at https://jrsoftware.org/isinfo.php
)
pause
exit /b 0
:err
echo Build failed.
pause
exit /b 1
