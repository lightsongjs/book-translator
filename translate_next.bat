@echo off
chcp 65001 >nul 2>&1
setlocal enabledelayedexpansion

REM Error margin for ratio (0.7 - 1.3)
set MIN_RATIO=0.7
set MAX_RATIO=1.3

echo ========================================
echo   Sistem Automat de Traducere Capitole
echo ========================================
echo.

REM Find the highest translated chapter number
echo Caut ultimul capitol tradus...

set LAST_CHAPTER=0
for /f "delims=" %%f in ('dir /b 04_ro_chapters\*.md 2^>nul') do (
    set "filename=%%f"
    for /f "tokens=2 delims=_" %%n in ("!filename!") do (
        set "word=%%n"
        if /i "!word:~0,7!"=="CHAPTER" (
            for /f "tokens=3 delims=_" %%c in ("!filename!") do (
                set /a "chapter=%%c"
                if !chapter! GTR !LAST_CHAPTER! set LAST_CHAPTER=!chapter!
            )
        )
    )
)

if !LAST_CHAPTER!==0 (
    echo Nu am gasit capitole traduse in 04_ro_chapters/
    echo Incepem cu capitolul 1
    set NEXT_CHAPTER=1
) else (
    set /a NEXT_CHAPTER=!LAST_CHAPTER!+1
    echo Ultimul capitol tradus: !LAST_CHAPTER!
)

echo Urmatorul capitol de tradus: !NEXT_CHAPTER!
echo.

REM Open chapter for translation
echo Deschid capitolul !NEXT_CHAPTER! pentru traducere...
REM Try Windows venv path first, then Linux WSL path
if exist .venv\Scripts\python.exe (
    .venv\Scripts\python.exe book_translator.py --open-chapter !NEXT_CHAPTER!
) else (
    python book_translator.py --open-chapter !NEXT_CHAPTER!
)

if errorlevel 1 (
    echo Eroare la deschiderea capitolului !NEXT_CHAPTER!
    exit /b 1
)

REM Wait for user to finish translation
echo.
echo ========================================
echo Apasa orice tasta cand ai terminat traducerea...
echo ========================================
pause >nul

REM Combine chapters
echo.
echo Combin segmentele capitolului !NEXT_CHAPTER!...
if exist .venv\Scripts\python.exe (
    .venv\Scripts\python.exe book_translator.py --combine-chapter !NEXT_CHAPTER!
) else (
    python book_translator.py --combine-chapter !NEXT_CHAPTER!
)

if errorlevel 1 (
    echo Eroare la combinarea capitolului !NEXT_CHAPTER!
    exit /b 1
)

echo √ Capitolul combinat cu succes
echo.

REM Verify translation
echo Verifica traducerea...
if exist .venv\Scripts\python.exe (
    .venv\Scripts\python.exe book_translator.py --verify !NEXT_CHAPTER! > verify_output.tmp 2>&1
) else (
    python book_translator.py --verify !NEXT_CHAPTER! > verify_output.tmp 2>&1
)

REM Show only the important info
findstr /C:"Line Counts:" /C:"Ratio:" /C:"OK Translation" /C:"ERROR:" /C:"WARNING:" verify_output.tmp

REM Extract ratio from verify output
for /f "tokens=2" %%r in ('findstr "Ratio:" verify_output.tmp') do set RATIO=%%r

if "!RATIO!"=="" (
    echo Nu am putut extrage ratio-ul din verificare
    del verify_output.tmp
    exit /b 1
)

del verify_output.tmp

REM Check if ratio is within acceptable range (0.7 to 1.3)
REM Simple string comparison for ratios between 0 and 2
set RATIO_OK=1

REM Check if ratio starts with 0. (less than 1)
echo !RATIO! | findstr /B "0\." >nul
if !errorlevel!==0 (
    REM Ratio is 0.XX - check if it's >= 0.7
    set FIRST_DIGIT=!RATIO:~2,1!
    if !FIRST_DIGIT! LSS 7 set RATIO_OK=0
)

REM Check if ratio is >= 1.4 (too high)
echo !RATIO! | findstr /B "1\.[4-9]" >nul
if !errorlevel!==0 set RATIO_OK=0

REM Check if ratio >= 2.0 (way too high)
echo !RATIO! | findstr /B "[2-9]\." >nul
if !errorlevel!==0 set RATIO_OK=0

if !RATIO_OK!==0 (
    echo.
    echo WARNING: Ratio !RATIO! in afara marjei ^(0.7-1.3^)
    set /p OPEN_FILES="Deschid fisierele? (y/n): "
    if /i "!OPEN_FILES!"=="y" (
        if exist .venv\Scripts\python.exe (
            .venv\Scripts\python.exe book_translator.py --compare !NEXT_CHAPTER!
        ) else (
            python book_translator.py --compare !NEXT_CHAPTER!
        )
        echo.
    )
    set /p CONTINUE="Continui? (y/n): "
    if /i not "!CONTINUE!"=="y" (
        echo Anulat.
        exit /b 1
    )
)

REM Ask if user wants to send to Kindle
set /p SEND_KINDLE="Trimitem pe Kindle? (y/n): "

if /i "!SEND_KINDLE!"=="y" (
    REM Find the Romanian chapter file (case-insensitive)
    set RO_CHAPTER_FILE=
    for /f "delims=" %%f in ('dir /b /s 04_ro_chapters\*chapter_!NEXT_CHAPTER!_ro.md 2^>nul') do (
        set RO_CHAPTER_FILE=%%f
        goto :found_file
    )

    :found_file
    if "!RO_CHAPTER_FILE!"=="" (
        echo ERROR: Fisier negasit
        exit /b 1
    )

    if exist .venv\Scripts\python.exe (
        .venv\Scripts\python.exe book_translator.py --sendtokindle "!RO_CHAPTER_FILE!" 2>nul | findstr /C:"Sending" /C:"Successfully" /C:"ERROR:"
    ) else (
        python book_translator.py --sendtokindle "!RO_CHAPTER_FILE!" 2>nul | findstr /C:"Sending" /C:"Successfully" /C:"ERROR:"
    )

    if errorlevel 1 (
        echo ERROR: Trimitere esuata
        exit /b 1
    )
) else (
    echo Nu s-a trimis.
)

echo.
echo Gata! Capitol !NEXT_CHAPTER! procesat.
echo.

endlocal
