@echo off
rem Flash a dipper boot image and then actually reboot the phone.
rem
rem Do NOT write this as one line with a pipe:
rem     fastboot flash boot x.img | fastboot reboot
rem A pipe starts both processes at once, fastboot does not read stdin, and
rem the reboot races the flash. The phone reboots, comes back to fastboot,
rem the flash re-runs and prints OKAY - and then nothing reboots it again,
rem so it sits in the fastboot UI looking exactly like a rejected image.
rem Use two lines, or &&, or this script.
setlocal
cd /d "%~dp0\.."

set IMG=%~1
if "%IMG%"=="" set IMG=shim\out\shim-boot-dipper.img

echo flashing %IMG%
fastboot flash boot "%IMG%"
if errorlevel 1 (
    echo FLASH FAILED - not rebooting
    exit /b 1
)

echo rebooting
fastboot reboot
