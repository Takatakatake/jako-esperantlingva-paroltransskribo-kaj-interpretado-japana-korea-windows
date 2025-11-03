@echo off
REM Convenience launcher: delegates to easy_start.cmd for Windows users.
call "%~dp0easy_start.cmd" %*
exit /b %ERRORLEVEL%
