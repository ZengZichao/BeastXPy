@echo off
rem beastx_validate.cmd - Windows Gate-3 validation wrapper for BeastXPy.
rem
rem Usage:
rem   beastx_validate.cmd XML_PATH [--beast-version 1.10.4^|10.5.0] [BeastXValidator args...]
rem
rem Environment overrides:
rem   BEASTX_JAVA               explicit java.exe
rem   JAVA_HOME                 used when BEASTX_JAVA is unset
rem   BEASTX_JAR                external beast.jar override
rem   BEASTX_VALIDATE_JAR_DIR   directory holding beast-<version>.jar
rem   BEASTX_VALIDATE_CLASSES   validator class directory override
rem
rem Exit code: 0 = valid, 1 = invalid, 2 = environment/usage error.
rem The cross-platform Python entry points are preferred:
rem   python -m beastxpy.main validate --xml out.xml --beast
rem   python tools\official_validate.py out.xml
setlocal EnableExtensions EnableDelayedExpansion

set "SCRIPT_DIR=%~dp0"
set "XML_PATH="
set "BEAST_VERSION=1.10.4"
set "EXTRA="

:parse_args
if "%~1"=="" goto args_done
if /I "%~1"=="--beast-version" (
  if "%~2"=="" (
    echo beastx_validate: --beast-version requires a value ^(1.10.4 or 10.5.0^) 1>&2
    exit /b 2
  )
  set "BEAST_VERSION=%~2"
  shift
  shift
  goto parse_args
)
if defined XML_PATH (
  set "EXTRA=!EXTRA! %1"
) else (
  set "XML_PATH=%~1"
)
shift
goto parse_args

:args_done
if not defined XML_PATH (
  echo usage: beastx_validate.cmd XML_PATH [--beast-version 1.10.4^|10.5.0] [args...] 1>&2
  exit /b 2
)
if not "%BEAST_VERSION%"=="1.10.4" if not "%BEAST_VERSION%"=="10.5.0" (
  echo beastx_validate: invalid --beast-version "%BEAST_VERSION%" ^(1.10.4 or 10.5.0^) 1>&2
  exit /b 2
)

rem ---- resolve the BEAST jar ------------------------------------------------
set "JAR="
if defined BEASTX_JAR if exist "%BEASTX_JAR%" set "JAR=%BEASTX_JAR%"
if not defined JAR (
  if defined BEASTX_VALIDATE_JAR_DIR (
    set "JAR_DIR=!BEASTX_VALIDATE_JAR_DIR!"
  ) else (
    set "JAR_DIR=!SCRIPT_DIR!jars"
  )
  set "JAR=!JAR_DIR!\beast-%BEAST_VERSION%.jar"
)
if not exist "%JAR%" (
  echo beastx_validate: beast jar not found: "%JAR%" 1>&2
  echo   set BEASTX_JAR or BEASTX_VALIDATE_JAR_DIR, or run build_jars.py 1>&2
  exit /b 2
)

rem ---- resolve java ---------------------------------------------------------
set "JAVA_BIN="
if defined BEASTX_JAVA (
  if exist "%BEASTX_JAVA%\bin\java.exe" (
    set "JAVA_BIN=%BEASTX_JAVA%\bin\java.exe"
  ) else if exist "%BEASTX_JAVA%" (
    set "JAVA_BIN=%BEASTX_JAVA%"
  )
)
if not defined JAVA_BIN if defined JAVA_HOME if exist "%JAVA_HOME%\bin\java.exe" set "JAVA_BIN=%JAVA_HOME%\bin\java.exe"
if not defined JAVA_BIN for %%J in (java.exe) do set "JAVA_BIN=%%~$PATH:J"
if not defined JAVA_BIN (
  echo beastx_validate: no usable Java runtime found ^(set JAVA_HOME or BEASTX_JAVA^) 1>&2
  exit /b 2
)

rem ---- build the classpath with the Windows separator ----------------------
set "CLASSPATH=%JAR%"
if defined BEASTX_VALIDATE_CLASSES (
  if exist "!BEASTX_VALIDATE_CLASSES!" set "CLASSPATH=!CLASSPATH!;!BEASTX_VALIDATE_CLASSES!"
) else (
  if exist "!SCRIPT_DIR!classes\!BEAST_VERSION!" (
    set "CLASSPATH=!CLASSPATH!;!SCRIPT_DIR!classes\!BEAST_VERSION!"
  ) else if exist "!SCRIPT_DIR!classes" (
    set "CLASSPATH=!CLASSPATH!;!SCRIPT_DIR!classes"
  )
)
if exist "!SCRIPT_DIR!parsers" set "CLASSPATH=!CLASSPATH!;!SCRIPT_DIR!parsers"

if defined BEASTX_VALIDATE_TRACE echo beastx_validate: java=!JAVA_BIN! jar=!JAR! classpath=!CLASSPATH! 1>&2

"!JAVA_BIN!" -Dfile.encoding=UTF-8 -cp "!CLASSPATH!" BeastXValidator "%XML_PATH%" !EXTRA!
exit /b %ERRORLEVEL%
