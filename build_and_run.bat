@echo off
echo [1/3] Creating build directory...
if not exist build mkdir build

echo [2/3] Compiling 1,000 Hz C Spinal Cord (actuator_core.c -^> build\actuator_core.dll)...
gcc -shared -O3 src/spinal_cord/actuator_core.c -o build/actuator_core.dll
if %errorlevel% neq 0 (
    echo [ERROR] GCC compilation failed. Ensure MinGW-w64 gcc is in your PATH.
    exit /b %errorlevel%
)

echo [3/3] Launching Sovereign MI Core...
python main.py
