@echo off
REM ================================================
REM  Novel Translator - Windows 构建脚本
REM  生成开箱即用的发行包
REM ================================================
chcp 65001 >nul

echo.
echo ======================================
echo  Novel Translator v3.0.0 构建脚本
echo ======================================
echo.

REM 检查 Python
python --version >nul 2>&1
if errorlevel 1 (
    echo [错误] 未找到 Python，请先安装 Python 3.10+
    pause
    exit /b 1
)

REM 安装/升级构建依赖
echo [1/4] 安装构建依赖...
pip install pyinstaller --upgrade -q
pip install -r requirements.txt -q

REM 清理上次构建
echo [2/4] 清理旧构建...
if exist build rmdir /s /q build
if exist dist rmdir /s /q dist

REM 执行打包
echo [3/4] 正在打包（约需 1-3 分钟）...
pyinstaller novel_translator.spec --noconfirm

REM 复制示例文件
echo [4/4] 复制发行附件...
if exist "dist\NovelTranslator" (
    copy /y "README.md" "dist\NovelTranslator\README.md" >nul
    copy /y "LICENSE" "dist\NovelTranslator\LICENSE" >nul
    copy /y "examples\glossary_example.json" "dist\NovelTranslator\glossary_example.json" >nul
)

echo.
if exist "dist\NovelTranslator\NovelTranslator.exe" (
    echo ======================================
    echo  构建成功！
    echo  输出目录: dist\NovelTranslator\
    echo  可执行文件: dist\NovelTranslator\NovelTranslator.exe
    echo ======================================
    echo.
    echo 发行方式: 将 dist\NovelTranslator 整个文件夹压缩为
    echo          NovelTranslator-v3.0.0-win-x64.zip 即可发布
) else (
    echo [错误] 构建失败，请检查上方日志
)

echo.
pause
