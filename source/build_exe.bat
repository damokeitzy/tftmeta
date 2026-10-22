@echo off
rem Builds dist\TFTMeta\TFTMeta.exe (a folder build: starts fast, fewer antivirus false alarms than one big file).
rem Needs: py -m pip install -r requirements.txt pyinstaller
cd /d "%~dp0"
py -m PyInstaller --noconfirm --clean --windowed --name TFTMeta ^
  --collect-all rapidocr --collect-all onnxruntime ^
  --hidden-import tools.update_game_data --hidden-import tools.extract_set --hidden-import tools.fetch_items ^
  --hidden-import tools.fetch_augment_icons --hidden-import tools.import_teamfight ^
  --hidden-import tools.import_tftacademy --hidden-import tools.import_seemeta ^
  --hidden-import tools.game_history --hidden-import tools.shop_report ^
  --add-data "data\comp_overrides.json;seed" --add-data "data\custom_comps.json;seed" ^
  --add-data "data\shop_odds.json;seed" --add-data "data\special.json;seed" ^
  --add-data "data\item_stats.json;seed" --add-data "data\teamfight_links.txt;seed" ^
  run.py
echo.
echo Built: dist\TFTMeta\TFTMeta.exe  (run it from there; inside this project it uses this project's data folder)
