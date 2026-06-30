# 从正在运行的 Edge 获取 XHS Cookie
# PowerShell 脚本 - 查找 Edge 调试端口并通过 CDP 获取 cookie

# 找到 Edge 进程的调试端口
$edgeProcess = Get-Process msedge -ErrorAction SilentlyContinue | Where-Object { $_.MainWindowHandle -ne 0 } | Select-Object -First 1
if (-not $edgeProcess) {
    Write-Output "No visible Edge window found"
    exit 1
}

# 尝试查找 Edge 的用户数据目录中的调试端口信息
$userDataDir = "$env:LOCALAPPDATA\Microsoft\Edge\User Data"
$portFile = "$userDataDir\DevToolsActivePort"
if (Test-Path $portFile) {
    $port = (Get-Content $portFile -First 1).Trim()
    Write-Output "Edge DevTools port: $port"
    
    # 通过 CDP 获取 cookie
    $wsUrl = "http://127.0.0.1:$port/json"
    try {
        $targets = Invoke-RestMethod -Uri $wsUrl -ErrorAction Stop
        # 找 xiaohongshu.com 的页面
        $xhsTarget = $targets | Where-Object { $_.url -like "*xiaohongshu*" } | Select-Object -First 1
        if (-not $xhsTarget) {
            $xhsTarget = $targets | Select-Object -First 1
        }
        
        if ($xhsTarget) {
            # 使用 CDP 获取所有 cookie
            $wsUrl = $xhsTarget.webSocketDebuggerUrl
            Write-Output "Connected to: $($xhsTarget.url)"
            
            # 通过 REST API 获取 cookie (更简单的方式)
            $cookieResult = Invoke-RestMethod -Uri "http://127.0.0.1:$port/json/protocol" -ErrorAction SilentlyContinue
            if (-not $cookieResult) {
                # 使用 DevTools Protocol via HTTP
                $storageCookies = Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:$port/json/new?Storage.getCookies" -ErrorAction SilentlyContinue
            }
        }
    } catch {
        Write-Output "CDP connection failed: $_"
    }
}

# 如果 CDP 失败，尝试 copy 并读取 cookie 数据库
Write-Output "Attempting cookie DB copy method..."
$dbPath = "$env:LOCALAPPDATA\Microsoft\Edge\User Data\Default\Network\Cookies"
$tmpFile = [System.IO.Path]::GetTempFileName()
try {
    # Try Volume Shadow Copy or direct copy
    Copy-Item -Path $dbPath -Destination $tmpFile -Force -ErrorAction SilentlyContinue
    if (Test-Path $tmpFile) {
        Write-Output "Cookie DB copied to: $tmpFile"
        Write-Output $tmpFile
    }
} catch {
    Write-Output "Failed to copy cookie DB: $_"
    exit 1
}
