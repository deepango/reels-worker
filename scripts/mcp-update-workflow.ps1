$rawToken = $env:N8N_MCP_BEARER_TOKEN
if (-not $rawToken) {
  throw "Missing N8N_MCP_BEARER_TOKEN environment variable. Set it to the MCP bearer token (with or without the 'Bearer ' prefix)."
}

$token = if ($rawToken -match '^Bearer\s+') { $rawToken } else { "Bearer $rawToken" }
$uri = "https://reel-video.app.n8n.cloud/mcp-server/http"
$headers = @{
  "Authorization" = $token
  "Content-Type" = "application/json"
  "Accept" = "application/json, text/event-stream"
}
$code = Get-Content -Raw "Reels Video Pipeline.production.codegen.js"
$payload = @{
  jsonrpc = "2.0"
  id = 22
  method = "tools/call"
  params = @{
    name = "update_workflow"
    arguments = @{
      workflowId = "23f9b6fd03yZn3j7"
      code = $code
      name = "Reels Video Pipeline (Production)"
      description = "Production-grade reel generation pipeline with scene-level loop, Replicate polling, ElevenLabs audio upload to B2, Redis queue dispatch, and failure handling."
    }
  }
} | ConvertTo-Json -Depth 50

$response = Invoke-WebRequest -UseBasicParsing -Method Post -Uri $uri -Headers $headers -Body $payload
$response.Content | Out-File -FilePath "mcp-update-response.txt" -Encoding utf8

# Verify node count after update.
$verifyPayload = @{
  jsonrpc = "2.0"
  id = 23
  method = "tools/call"
  params = @{
    name = "get_workflow_details"
    arguments = @{ workflowId = "23f9b6fd03yZn3j7" }
  }
} | ConvertTo-Json -Depth 20
$verifyResp = Invoke-WebRequest -UseBasicParsing -Method Post -Uri $uri -Headers $headers -Body $verifyPayload
$verifyResp.Content | Out-File -FilePath "mcp-verify-response.txt" -Encoding utf8
Write-Output "DONE"