@echo off
rem Builds the isolated LaTeX image from the Dockerfile beside this script. Needs Docker Desktop running; downloads about 1 GB.
cd /d "%~dp0"
docker build -t lemfolio/tex:0.1 - < Dockerfile.tex
echo.
if errorlevel 1 (echo The image was not built. Is Docker Desktop running?) else (echo The TeX image is ready. Refresh Lemfolio.)
pause
