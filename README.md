# Stock2

支援 Windows 與 macOS，請使用 Python 3.10 以上。

## 第一次使用

1. 安裝 Python 3.10 以上。
2. 在專案目錄執行 `python -m pip install -r requirements.txt`（macOS 使用 `python3`）。
3. Windows 雙擊「啟動控制台.bat」；macOS 雙擊「啟動Stock2.command」。若套件尚未安裝，Windows 先執行「安裝相依套件.bat」，macOS 先執行「安裝相依套件.command」。

macOS 第一次雙擊 `.command` 若被系統阻擋，請在「終端機」執行 `chmod +x 啟動Stock2.command 安裝相依套件.command`，再重新開啟。若尚未安裝套件，先雙擊「安裝相依套件.command」。

## 日常操作

控制台會啟動本機服務並開啟瀏覽器。Windows 與 macOS 都能從系統匣選單結束服務；也可以對 `http://localhost:8935/api/shutdown` 發送 POST 來安全關閉。

手機版發布也使用共用的 `deploy_mobile.py`：Windows 雙擊「發布手機版.bat」，macOS 雙擊「發布手機版.command」。兩個平台皆需先安裝 Node.js 20 以上並完成 Cloudflare 登入。
