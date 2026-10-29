@echo off
setlocal
cd /d "%~dp0"
where py >nul 2>nul
if errorlevel 1 goto python_fallback
py -3 -m imvu_obj gui
if errorlevel 1 goto failed
goto end

:python_fallback
where python >nul 2>nul
if errorlevel 1 goto missing_python
python -m imvu_obj gui
if errorlevel 1 goto failed
goto end

:missing_python
echo Python nao foi encontrado. Instale o Python 3.10 ou superior com suporte a Tcl/Tk.
echo Durante a instalacao, marque a opcao para adicionar Python ao PATH.
pause
exit /b 1

:failed
echo.
echo O programa terminou com erro. Consulte a mensagem acima.
pause
exit /b 1

:end
endlocal
