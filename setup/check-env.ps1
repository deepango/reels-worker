$required = @(
  'B2_BUCKET_NAME',
  'B2_APPLICATION_KEY',
  'B2_APPLICATION_KEY_ID',
  'B2_ENDPOINT',
  'ANTHROPIC_API_KEY',
  'REPLICATE_API_TOKEN',
  'ELEVENLABS_API_KEY',
  'REDIS_URL',
  'DATABASE_URL',
  'N8N_CALLBACK_URL',
  'WORKER_REGION',
  'LOG_LEVEL'
)

$missing = @()
foreach ($name in $required) {
  if ([string]::IsNullOrWhiteSpace([Environment]::GetEnvironmentVariable($name))) {
    $missing += $name
  }
}

if ($missing.Count -gt 0) {
  Write-Host "Missing required environment variables:" -ForegroundColor Red
  $missing | ForEach-Object { Write-Host " - $_" -ForegroundColor Red }
  exit 1
}

Write-Host "All required environment variables are set." -ForegroundColor Green
exit 0
