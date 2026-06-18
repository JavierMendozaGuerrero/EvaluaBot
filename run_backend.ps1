$ErrorActionPreference = "Stop"

Get-Content .env | ForEach-Object {
  if ($_ -match '^\s*([^#][^=]+)=(.*)$') {
    [Environment]::SetEnvironmentVariable($matches[1].Trim(), $matches[2].Trim(), "Process")
  }
}

if (-not $env:NOTION_USERS_DATABASE_NAME) {
  $env:NOTION_USERS_DATABASE_NAME = "Usuarios web"
}
if (-not $env:NOTION_EMPLOYEES_DATABASE_ID) {
  $env:NOTION_EMPLOYEES_DATABASE_ID = $env:NOTION_PARENT_PAGE_ID
}

python bot.py
